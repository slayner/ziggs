// WireGuard tunnel via wintun (Windows) — split-tunneling só pra IPs do Albion.
//
// SEGURANÇA — a defesa real é SERVER-SIDE, não aqui.
// O companion só adiciona rotas estáticas pros IPs do Albion no client
// (add_albion_routes), mas isso é client-side e o usuário controla o
// companion — pode editar o código pra adicionar rotas extras. A defesa
// contra abuso vive na VPS (companion-vps-setup.sh):
//   - Default DROP no FORWARD do wg0
//   - Chain ZIGGS_ALBION com ALLOW só pros IPs resolvidos dos hostnames
//     oficiais do Albion (gameinfo*.albiononline.com)
//   - Log de tráfego droppado (rate-limited)
//   - Cron hourly re-resolve os IPs (datacenters rotacionam)
// Mesmo que o client seja modificado pra mandar tráfego não-Albion pelo
// gateway 10.99.0.1, a VPS droppa. Não confie no client.
//
// Fluxo:
//   1. Cria interface wintun "Ziggs" com IP 10.99.0.2/24
//   2. Adiciona rotas estáticas pros IPs do Albion apontando pra essa interface
//   3. Loop de packets:
//      - wintun recebe pacote IP do sistema → boringtun encrypta → UDP pro servidor
//      - UDP recebe do servidor → boringtun decrypta → escreve na wintun
//   4. Em paralelo: mede latência direta vs túnel. Se túnel pior, desliga.
//
// Requer admin (criar interface de rede virtual + adicionar rotas).

use std::net::{IpAddr, Ipv4Addr, SocketAddr, ToSocketAddrs, UdpSocket};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::time::{Duration, Instant};
use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use boringtun::noise::{Tunn, TunnResult};
use boringtun::x25519::{StaticSecret, PublicKey};

#[cfg(target_os = "windows")]
use wintun::Adapter;

use crate::albion_ips;

const TUNNEL_ADAPTER_NAME: &str = "Ziggs";
const TUNNEL_ADAPTER_TYPE: &str = "WireGuard";
const TUNNEL_IPV4_ADDR: Ipv4Addr = Ipv4Addr::new(10, 99, 0, 2);
const TUNNEL_IPV4_GW: Ipv4Addr = Ipv4Addr::new(10, 99, 0, 1);
const TUNNEL_NETMASK: Ipv4Addr = Ipv4Addr::new(255, 255, 255, 0);
const TUNNEL_MTU: usize = 1280; // WireGuard overhead friendly
const KEEPALIVE: Option<u16> = Some(25); // segundos
const PATH_HEALTH_INTERVAL: Duration = Duration::from_secs(5);
const PATH_PROBE_TIMEOUT: Duration = Duration::from_millis(1500);

/// Buffer máximo: pacote IP + overhead do WireGuard (32 bytes)
const WG_BUFFER_SIZE: usize = 65535 + 32;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TunnelConfig {
    /// Endereço do servidor WireGuard (ex. "203.0.113.10:51820")
    pub endpoint: String,
    /// Chave pública do servidor (hex ou base64)
    pub server_pubkey: String,
    /// Chave privada do cliente (hex ou base64) — gerada por generate_keypair
    pub client_privkey: String,
    /// Habilitar túnel automaticamente ao iniciar
    pub enabled: bool,
}

impl Default for TunnelConfig {
    fn default() -> Self {
        Self {
            endpoint: String::new(),
            server_pubkey: String::new(),
            client_privkey: String::new(),
            enabled: false,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InternetPathStatus {
    pub name: String,
    pub local_ip: String,
    pub priority: u32,
    pub latency_ms: Option<f64>,
    pub available: bool,
    pub active: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TunnelStatus {
    pub running: bool,
    pub connected: bool,
    pub direct_latency_ms: Option<f64>,
    pub tunnel_latency_ms: Option<f64>,
    pub using_tunnel: bool,
    pub last_error: Option<String>,
    pub bytes_sent: u64,
    pub bytes_received: u64,
    pub active_interface: Option<String>,
    pub failover_count: u32,
    pub internet_paths: Vec<InternetPathStatus>,
}

impl Default for TunnelStatus {
    fn default() -> Self {
        Self {
            running: false,
            connected: false,
            direct_latency_ms: None,
            tunnel_latency_ms: None,
            using_tunnel: false,
            last_error: None,
            bytes_sent: 0,
            bytes_received: 0,
            active_interface: None,
            failover_count: 0,
            internet_paths: Vec::new(),
        }
    }
}

#[derive(Clone)]
pub struct Tunnel {
    pub status: Arc<Mutex<TunnelStatus>>,
    shutdown: Arc<AtomicBool>,
    installed_routes: Arc<Mutex<Vec<InstalledRoute>>>,
    operation: Arc<Mutex<()>>,
    #[cfg(target_os = "windows")]
    preloaded_paths: Arc<Mutex<Vec<PathCandidate>>>,
}

#[derive(Clone)]
struct InstalledRoute {
    network: Ipv4Addr,
    mask: Ipv4Addr,
}

impl Tunnel {
    pub fn new() -> Self {
        Self {
            status: Arc::new(Mutex::new(TunnelStatus::default())),
            shutdown: Arc::new(AtomicBool::new(false)),
            installed_routes: Arc::new(Mutex::new(Vec::new())),
            operation: Arc::new(Mutex::new(())),
            #[cfg(target_os = "windows")]
            preloaded_paths: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Analisa adaptadores e handshakes no boot, antes de o usuário ligar o
    /// túnel. O start ainda revalida a conexão escolhida, mas não precisa
    /// descobrir e ordenar tudo no caminho crítico do botão.
    pub async fn preload(&self, cfg: TunnelConfig) -> Result<()> {
        let _operation = self.operation.lock().await;
        #[cfg(target_os = "windows")]
        {
            let privkey = parse_key(&cfg.client_privkey).ok_or_else(|| anyhow!("chave privada inválida"))?;
            let server_pub = parse_key(&cfg.server_pubkey).ok_or_else(|| anyhow!("chave pública inválida"))?;
            let endpoint = cfg.endpoint.clone();
            let shutdown = Arc::clone(&self.shutdown);
            let paths = tokio::task::spawn_blocking(move || {
                let endpoint = resolve_endpoint(&endpoint)?;
                let secret = StaticSecret::from(privkey);
                let peer_pub = PublicKey::from(server_pub);
                let mut tunn = Tunn::new(secret, peer_pub, None, KEEPALIVE, 0, None);
                rank_internet_paths(&mut tunn, endpoint, &shutdown)
            }).await.map_err(|e| anyhow!("pré-carga cancelada: {e}"))??;
            let best = paths.iter().find_map(|path| path.latency_ms);
            self.status.lock().await.internet_paths = path_statuses(&paths, u32::MAX);
            self.status.lock().await.tunnel_latency_ms = best;
            *self.preloaded_paths.lock().await = paths;
        }
        #[cfg(not(target_os = "windows"))]
        { let _ = cfg; }
        Ok(())
    }

    pub async fn stop(&self) {
        self.shutdown.store(true, AtomicOrdering::Relaxed);
        if let Err(e) = self.remove_albion_routes().await {
            self.status.lock().await.last_error = Some(format!("limpeza de rotas: {e:#}"));
        }
    }

    pub fn prepare_start(&self) {
        self.shutdown.store(false, AtomicOrdering::Relaxed);
    }

    /// Remove as rotas estáticas do Albion (fallback pra rota direta).
    async fn remove_albion_routes(&self) -> Result<()> {
        #[cfg(target_os = "windows")]
        {
            scrub_stale_routes_now()?;
            self.installed_routes.lock().await.clear();
        }
        Ok(())
    }

    /// Cura rotas órfãs de crash/kill anterior antes de qualquer túnel iniciar.
    pub async fn scrub_stale_routes(&self) -> Result<()> {
        self.remove_albion_routes().await
    }

    /// Loop principal do túnel. Roda numa task separada.
    pub async fn run(&self, cfg: TunnelConfig) {
        let _operation = self.operation.lock().await;
        if self.shutdown.load(AtomicOrdering::Relaxed) { return; }
        {
            let mut s = self.status.lock().await;
            s.running = true;
            s.last_error = None;
        }

        // Pré-validação: mede latência direta vs via túnel antes de ativar rotas.
        // Se túnel não melhora, desliga e mantém rota direta.
        match self.evaluate_and_run(cfg).await {
            Ok(()) => {}
            Err(e) => {
                let mut s = self.status.lock().await;
                s.last_error = Some(format!("{:#}", e));
                s.connected = false;
            }
        }

        self.remove_albion_routes().await.ok();
        let mut s = self.status.lock().await;
        s.running = false;
        s.using_tunnel = false;
        s.connected = false;
        s.active_interface = None;
    }

    /// Sobe túnel, mede latência (informativo), ativa rotas e roda.
    /// O usuário escolhe ligar — sem auto-fallback.
    async fn evaluate_and_run(&self, cfg: TunnelConfig) -> Result<()> {
        // 1. mede latência direta (informativo)
        let direct = measure_albion_latency_direct(&self.shutdown).await;
        {
            let mut s = self.status.lock().await;
            s.direct_latency_ms = direct;
        }

        // O packet loop escolhe a melhor conexão local, valida o handshake e só
        // então instala as rotas do Albion. Wintun e Tunn permanecem os mesmos
        // quando a conexão física muda; apenas o socket UDP externo é trocado.
        self.packet_loop(cfg).await?;
        Ok(())
    }

    /// Adiciona rotas estáticas pros IPs do Albion apontando pro túnel.
    async fn add_albion_routes(&self) -> Result<()> {
        #[cfg(target_os = "windows")]
        {
            let routes = albion_ips::albion_route_targets().await;
            let mut installed: Vec<InstalledRoute> = Vec::new();
            for (network, mask) in routes {
                let gateway = TUNNEL_IPV4_GW.to_string();
                // Cura rota órfã deixada por crash/kill do processo anterior.
                let _ = crate::winutil::no_window(std::process::Command::new("route"))
                    .args(["delete", &network.to_string(), "mask", &mask.to_string(), &gateway])
                    .output();
                let output = crate::winutil::no_window(std::process::Command::new("route"))
                    .args(["add", &network.to_string(), "mask", &mask.to_string(), &gateway, "metric", "1"])
                    .output()?;
                if !output.status.success() {
                    for added in &installed {
                        let _ = crate::winutil::no_window(std::process::Command::new("route"))
                            .args(["delete", &added.network.to_string(), "mask", &added.mask.to_string(), &gateway]).output();
                    }
                    return Err(anyhow!("Windows recusou a rota do jogo para {network}"));
                }
                installed.push(InstalledRoute { network, mask });
            }
            *self.installed_routes.lock().await = installed;
        }
        Ok(())
    }

    /// Loop principal de packets: wintun ↔ UDP via boringtun.
    async fn packet_loop(&self, cfg: TunnelConfig) -> Result<()> {
        #[cfg(target_os = "windows")]
        {
            let privkey = parse_key(&cfg.client_privkey)
                .ok_or_else(|| anyhow!("chave privada inválida"))?;
            let server_pub = parse_key(&cfg.server_pubkey)
                .ok_or_else(|| anyhow!("chave pública inválida"))?;
            let endpoint = resolve_endpoint(&cfg.endpoint)?;

            let wintun = load_wintun()
                .map_err(|e| anyhow!("wintun.dll: {}", e))?;
            let adapter = Adapter::open(&wintun, TUNNEL_ADAPTER_NAME)
                .or_else(|_| Adapter::create(&wintun, TUNNEL_ADAPTER_NAME, TUNNEL_ADAPTER_TYPE, None))
                .map_err(|e| anyhow!("adapter: {}", e))?;
            adapter.set_address(TUNNEL_IPV4_ADDR).ok();
            adapter.set_netmask(TUNNEL_NETMASK).ok();
            // Split tunnel é definido só pelas rotas /32 dos jogos; nunca cria
            // gateway padrão na Wintun, senão tráfego alheio pode ser desviado.
            adapter.set_gateway(None).ok();
            adapter.set_mtu(TUNNEL_MTU).ok();
            let session = Arc::new(adapter.start_session(wintun::MAX_RING_CAPACITY)?);

            let secret = StaticSecret::from(privkey);
            let peer_pub = PublicKey::from(server_pub);
            let mut tunn = Tunn::new(secret, peer_pub, None, KEEPALIVE, 0, None);

            let cached = self.preloaded_paths.lock().await.clone();
            let mut paths = if cached.is_empty() {
                rank_internet_paths(&mut tunn, endpoint, &self.shutdown)?
            } else {
                merge_path_priority(&cached, enumerate_internet_paths()?)
            };
            let (mut udp, mut active_path, initial_latency) = connect_ranked_path(
                &mut tunn, endpoint, &paths, None, &self.shutdown,
            )?.ok_or_else(|| anyhow!("nenhuma conexão com internet alcança a VPS"))?;
            udp.set_nonblocking(true)?;

            self.add_albion_routes().await?;
            {
                let mut s = self.status.lock().await;
                s.tunnel_latency_ms = Some(initial_latency);
                s.using_tunnel = true;
                s.connected = true;
                s.active_interface = Some(active_path.name.clone());
                s.internet_paths = path_statuses(&paths, active_path.if_index);
            }

            let mut send_buf = vec![0u8; WG_BUFFER_SIZE];
            let mut recv_buf = vec![0u8; WG_BUFFER_SIZE];
            let mut tun_out_buf = vec![0u8; WG_BUFFER_SIZE];
            let mut next_health = Instant::now() + PATH_HEALTH_INTERVAL;
            let mut health_deadline: Option<Instant> = None;
            let mut next_timer = Instant::now() + Duration::from_secs(1);
            let mut next_rescan = Instant::now() + Duration::from_secs(15);

            loop {
                if self.shutdown.load(AtomicOrdering::Relaxed) {
                    break;
                }

                let mut did_work = false;

                // 1. Recebe pacote da interface wintun → encrypta → manda UDP
                if let Ok(Some(packet)) = session.try_receive() {
                    let bytes = packet.bytes();
                    match tunn.encapsulate(bytes, &mut send_buf) {
                        TunnResult::WriteToNetwork(encrypted) => {
                            if udp.send(encrypted).is_ok() {
                                let mut s = self.status.lock().await;
                                s.bytes_sent += encrypted.len() as u64;
                            } else {
                                health_deadline = Some(Instant::now());
                            }
                        }
                        TunnResult::Err(e) => {
                            tracing::warn!("wg encap error: {:?}", e);
                        }
                        _ => {}
                    }
                    did_work = true;
                }

                // 2. Recebe UDP do servidor → decrypta → escreve na wintun
                match udp.recv(&mut recv_buf) {
                    Ok(n) => {
                        let result = tunn.decapsulate(None, &recv_buf[..n], &mut tun_out_buf);
                        let verified = !matches!(result, TunnResult::Err(_));
                        match result {
                            TunnResult::WriteToTunnelV4(data, _dst) => {
                                let mut pkt = session.allocate_send_packet(data.len() as u16)?;
                                pkt.bytes_mut().copy_from_slice(data);
                                session.send_packet(pkt);
                                let mut s = self.status.lock().await;
                                s.bytes_received += data.len() as u64;
                                s.connected = true;
                            }
                            TunnResult::WriteToTunnelV6(_, _) => {}
                            TunnResult::WriteToNetwork(resp) => {
                                // handshake response ou cookie reply — reenvia
                                let _ = udp.send(resp);
                            }
                            _ => {}
                        }
                        if verified {
                            health_deadline = None;
                            next_health = Instant::now() + PATH_HEALTH_INTERVAL;
                            self.status.lock().await.connected = true;
                        }
                        did_work = true;
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                    Err(e) => {
                        tracing::warn!("udp recv error: {}", e);
                    }
                }

                let now = Instant::now();
                if now >= next_timer {
                    if let TunnResult::WriteToNetwork(packet) = tunn.update_timers(&mut send_buf) {
                        let _ = udp.send(packet);
                    }
                    next_timer = now + Duration::from_secs(1);
                }

                if now >= next_rescan {
                    if let Ok(current) = enumerate_internet_paths() {
                        let active_present = current.iter().any(|path| path.if_index == active_path.if_index && path.local_ip == active_path.local_ip);
                        paths = merge_path_priority(&paths, current);
                        self.status.lock().await.internet_paths = path_statuses(&paths, active_path.if_index);
                        if !active_present { health_deadline = Some(now); }
                    }
                    next_rescan = now + Duration::from_secs(15);
                }

                if health_deadline.is_none() && now >= next_health {
                    if let TunnResult::WriteToNetwork(packet) = tunn.format_handshake_initiation(&mut send_buf, true) {
                        let _ = udp.send(packet);
                        health_deadline = Some(now + PATH_PROBE_TIMEOUT);
                    }
                    next_health = now + PATH_HEALTH_INTERVAL;
                }

                if health_deadline.is_some_and(|deadline| now >= deadline) {
                    self.status.lock().await.connected = false;
                    match connect_ranked_path(&mut tunn, endpoint, &paths, Some(active_path.if_index), &self.shutdown) {
                        Ok(Some((new_udp, new_path, latency))) => {
                            let failed_if = active_path.if_index;
                            let changed = new_path.if_index != active_path.if_index;
                            udp = new_udp;
                            udp.set_nonblocking(true)?;
                            active_path = new_path;
                            if let Ok(current) = enumerate_internet_paths() {
                                paths = merge_path_priority(&paths, current);
                            }
                            if changed {
                                if let Some(path) = paths.iter_mut().find(|path| path.if_index == failed_if) {
                                    path.available = false;
                                }
                            }
                            if let Some(path) = paths.iter_mut().find(|path| path.if_index == active_path.if_index) {
                                *path = active_path.clone();
                            }
                            let mut s = self.status.lock().await;
                            s.connected = true;
                            s.tunnel_latency_ms = Some(latency);
                            s.active_interface = Some(active_path.name.clone());
                            if changed { s.failover_count += 1; }
                            s.internet_paths = path_statuses(&paths, active_path.if_index);
                            health_deadline = None;
                            next_health = Instant::now() + PATH_HEALTH_INTERVAL;
                        }
                        Ok(None) => {
                            self.status.lock().await.last_error = Some("todas as conexões falharam; usando rota direta".into());
                            break;
                        }
                        Err(e) => {
                            self.status.lock().await.last_error = Some(format!("failover: {e:#}"));
                            break;
                        }
                    }
                }

                if !did_work {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
            }
        }
        Ok(())
    }
}

// ─── helpers ────────────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
fn stale_route_cleanup_script() -> &'static str {
    "Get-NetRoute -NextHop '10.99.0.1' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false -ErrorAction Stop"
}

#[cfg(target_os = "windows")]
pub fn scrub_stale_routes_now() -> Result<()> {
    let output = crate::winutil::no_window(std::process::Command::new("powershell"))
        .args(["-NoProfile", "-NonInteractive", "-Command", stale_route_cleanup_script()])
        .output()?;
    if !output.status.success() {
        return Err(anyhow!("Windows não conseguiu remover rotas antigas do túnel"));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn scrub_stale_routes_now() -> Result<()> { Ok(()) }

#[cfg(target_os = "windows")]
#[derive(Clone, Debug)]
struct PathCandidate {
    if_index: u32,
    name: String,
    local_ip: Ipv4Addr,
    metric: u32,
    latency_ms: Option<f64>,
    available: bool,
}

#[cfg(target_os = "windows")]
fn resolve_endpoint(endpoint: &str) -> Result<SocketAddr> {
    endpoint
        .to_socket_addrs()
        .map_err(|e| anyhow!("endpoint inválido: {e}"))?
        .find(|addr| addr.is_ipv4())
        .ok_or_else(|| anyhow!("endpoint sem endereço IPv4"))
}

/// Lista conexões físicas/virtuais que têm IPv4, gateway padrão e estão Up.
/// O handshake WireGuard abaixo é quem confirma acesso real à internet/VPS.
#[cfg(target_os = "windows")]
fn enumerate_internet_paths() -> Result<Vec<PathCandidate>> {
    let script = r#"Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway -and $_.IPv4Address -and $_.InterfaceAlias -ne 'Ziggs' } | ForEach-Object { "{0}`t{1}`t{2}`t{3}" -f $_.InterfaceIndex,$_.InterfaceAlias,$_.IPv4Address[0].IPAddress,$_.NetIPv4Interface.InterfaceMetric }"#;
    let output = crate::winutil::no_window(std::process::Command::new("powershell"))
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .output()
        .map_err(|e| anyhow!("falha ao listar conexões: {e}"))?;
    if !output.status.success() {
        return Err(anyhow!("Windows não conseguiu listar conexões de internet"));
    }
    let mut paths = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let mut cols = line.trim().split('\t');
        let (Some(idx), Some(name), Some(ip), Some(metric)) =
            (cols.next(), cols.next(), cols.next(), cols.next()) else { continue };
        let (Ok(if_index), Ok(local_ip), Ok(metric)) =
            (idx.parse(), ip.parse(), metric.parse()) else { continue };
        if paths.iter().any(|p: &PathCandidate| p.if_index == if_index) { continue; }
        paths.push(PathCandidate {
            if_index,
            name: name.to_string(),
            local_ip,
            metric,
            latency_ms: None,
            available: false,
        });
    }
    paths.sort_by_key(|p| p.metric);
    Ok(paths)
}

#[cfg(target_os = "windows")]
fn open_path_socket(path: &PathCandidate, endpoint: SocketAddr) -> Result<UdpSocket> {
    use std::os::windows::io::AsRawSocket;
    use windows_sys::Win32::Networking::WinSock::{setsockopt, IPPROTO_IP, IP_UNICAST_IF, SOCKET_ERROR};

    let udp = UdpSocket::bind(SocketAddr::new(IpAddr::V4(path.local_ip), 0))?;
    let index = path.if_index.to_be(); // IP_UNICAST_IF espera DWORD em network byte order.
    let rc = unsafe {
        setsockopt(
            udp.as_raw_socket() as usize,
            IPPROTO_IP,
            IP_UNICAST_IF,
            &index as *const u32 as *const u8,
            std::mem::size_of::<u32>() as i32,
        )
    };
    if rc == SOCKET_ERROR {
        return Err(std::io::Error::last_os_error().into());
    }
    udp.connect(endpoint)?;
    Ok(udp)
}

/// Força um handshake autenticado. Ao sair por outro adaptador, o peer
/// WireGuard da VPS aprende o novo endpoint NAT sem mudar o IP interno/público.
#[cfg(target_os = "windows")]
fn probe_path(tunn: &mut Tunn, udp: &UdpSocket, shutdown: &AtomicBool) -> Option<f64> {
    let mut send_buf = vec![0u8; WG_BUFFER_SIZE];
    let mut recv_buf = vec![0u8; WG_BUFFER_SIZE];
    let mut out_buf = vec![0u8; WG_BUFFER_SIZE];
    udp.set_nonblocking(false).ok()?;
    udp.set_read_timeout(Some(Duration::from_millis(200))).ok()?;
    let start = Instant::now();
    let TunnResult::WriteToNetwork(init) = tunn.format_handshake_initiation(&mut send_buf, true) else {
        return None;
    };
    udp.send(init).ok()?;
    while start.elapsed() < PATH_PROBE_TIMEOUT {
        if shutdown.load(AtomicOrdering::Relaxed) { return None; }
        let n = match udp.recv(&mut recv_buf) {
            Ok(n) => n,
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => continue,
            Err(_) => return None,
        };
        let kind = recv_buf.get(..4).and_then(|b| b.try_into().ok()).map(u32::from_le_bytes);
        let result = tunn.decapsulate(None, &recv_buf[..n], &mut out_buf);
        if matches!(result, TunnResult::Err(_)) { continue; }
        if let TunnResult::WriteToNetwork(response) = result { let _ = udp.send(response); }
        match kind {
            Some(2 | 4) => return Some(start.elapsed().as_secs_f64() * 1000.0),
            Some(3) => {
                if let TunnResult::WriteToNetwork(init) = tunn.format_handshake_initiation(&mut send_buf, true) {
                    let _ = udp.send(init);
                }
            }
            _ => {}
        }
    }
    None
}

#[cfg(target_os = "windows")]
fn rank_internet_paths(tunn: &mut Tunn, endpoint: SocketAddr, shutdown: &AtomicBool) -> Result<Vec<PathCandidate>> {
    let mut paths = enumerate_internet_paths()?;
    for path in &mut paths {
        if let Ok(socket) = open_path_socket(path, endpoint) {
            path.latency_ms = probe_path(tunn, &socket, shutdown);
            path.available = path.latency_ms.is_some();
        }
    }
    paths.sort_by(|a, b| {
        b.available.cmp(&a.available)
            .then_with(|| a.latency_ms.partial_cmp(&b.latency_ms).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.metric.cmp(&b.metric))
    });
    Ok(paths)
}

#[cfg(target_os = "windows")]
fn merge_path_priority(previous: &[PathCandidate], mut current: Vec<PathCandidate>) -> Vec<PathCandidate> {
    for path in &mut current {
        if let Some(old) = previous.iter().find(|old| old.if_index == path.if_index && old.local_ip == path.local_ip) {
            path.latency_ms = old.latency_ms;
            path.available = old.available;
        }
    }
    current.sort_by_key(|path| {
        previous.iter().position(|old| old.if_index == path.if_index && old.local_ip == path.local_ip)
            .unwrap_or(previous.len() + path.metric as usize)
    });
    current
}

#[cfg(target_os = "windows")]
fn connect_ranked_path(
    tunn: &mut Tunn,
    endpoint: SocketAddr,
    previous: &[PathCandidate],
    current_if: Option<u32>,
    shutdown: &AtomicBool,
) -> Result<Option<(UdpSocket, PathCandidate, f64)>> {
    let mut paths = merge_path_priority(previous, enumerate_internet_paths()?);
    // Em failover, tenta as alternativas na prioridade original e a conexão
    // atual por último (ela pode ter voltado enquanto fazíamos a troca).
    if let Some(active) = current_if {
        paths.sort_by_key(|path| path.if_index == active);
    }
    for mut path in paths {
        if shutdown.load(AtomicOrdering::Relaxed) { return Ok(None); }
        let Ok(socket) = open_path_socket(&path, endpoint) else { continue };
        let Some(latency) = probe_path(tunn, &socket, shutdown) else { continue };
        path.available = true;
        path.latency_ms = Some(latency);
        return Ok(Some((socket, path, latency)));
    }
    Ok(None)
}

#[cfg(target_os = "windows")]
fn path_statuses(paths: &[PathCandidate], active_if: u32) -> Vec<InternetPathStatus> {
    paths.iter().enumerate().map(|(index, path)| InternetPathStatus {
        name: path.name.clone(),
        local_ip: path.local_ip.to_string(),
        priority: index as u32 + 1,
        latency_ms: path.latency_ms,
        available: path.available,
        active: path.if_index == active_if,
    }).collect()
}

/// Carrega wintun.dll de vários paths possíveis:
/// - relativo ao .exe (dev e bundle)
/// - diretório de resources do bundle (Tauri)
/// - PATH do sistema
#[cfg(target_os = "windows")]
fn load_wintun() -> Result<wintun::Wintun> {
    // 1. tenta no mesmo dir do exe
    let exe_dir = std::env::current_exe()
        .map_err(|e| anyhow!("current_exe: {}", e))?
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_default();
    let candidates = [
        exe_dir.join("wintun.dll"),
        exe_dir.join("resources").join("wintun.dll"),
        std::path::PathBuf::from("wintun.dll"),
    ];
    for path in &candidates {
        if path.exists() {
            return unsafe { wintun::load_from_path(path) }
                .map_err(|e| anyhow!("wintun::load_from_path({:?}): {}", path, e));
        }
    }
    // 2. fallback: deixa a crate procurar no PATH
    unsafe { wintun::load() }
        .map_err(|e| anyhow!("wintun::load (PATH): {}. Coloque wintun.dll junto do .exe", e))
}

/// Mede latência direta (sem túnel) pro servidor do Albion — TCP connect 10x.
async fn measure_albion_latency_direct(shutdown: &AtomicBool) -> Option<f64> {
    let host = "gameinfo.albiononline.com:443";
    let mut samples = Vec::new();
    for _ in 0..10 {
        if shutdown.load(AtomicOrdering::Relaxed) { return None; }
        let start = Instant::now();
        match tokio::time::timeout(
            Duration::from_secs(2),
            tokio::net::TcpStream::connect(host),
        ).await {
            Ok(Ok(_stream)) => {
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
            _ => {}
        }
        tokio::time::sleep(Duration::from_millis(80)).await;
    }
    if samples.is_empty() {
        return None;
    }
    Some(samples.iter().sum::<f64>() / samples.len() as f64)
}

/// Converte hex (64 chars) ou base64 (44 chars) → 32 bytes.
fn parse_key(s: &str) -> Option<[u8; 32]> {
    let s = s.trim();
    if s.len() == 64 {
        // hex
        let mut out = [0u8; 32];
        for i in 0..32 {
            out[i] = u8::from_str_radix(&s[i*2..i*2+2], 16).ok()?;
        }
        Some(out)
    } else if s.len() == 44 {
        // base64
        use base64::Engine;
        let decoded = base64::engine::general_purpose::STANDARD.decode(s).ok()?;
        if decoded.len() != 32 {
            return None;
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&decoded);
        Some(out)
    } else {
        None
    }
}

/// Gera par de chaves WireGuard (privada + pública) pra configurar o client.
pub fn generate_keypair() -> (String, String) {
    use boringtun::x25519::{StaticSecret, PublicKey};
    use base64::Engine;
    use rand_core::OsRng;
    let secret = StaticSecret::random_from_rng(OsRng);
    let public = PublicKey::from(&secret);
    let priv_b64 = base64::engine::general_purpose::STANDARD.encode(secret.to_bytes());
    let pub_b64 = base64::engine::general_purpose::STANDARD.encode(public.to_bytes());
    (priv_b64, pub_b64)
}

#[cfg(all(test, target_os = "windows"))]
mod tests {
    use super::*;

    fn path(index: u32, metric: u32) -> PathCandidate {
        PathCandidate {
            if_index: index,
            name: format!("net-{index}"),
            local_ip: Ipv4Addr::new(192, 168, index as u8, 2),
            metric,
            latency_ms: Some(index as f64 * 10.0),
            available: true,
        }
    }

    #[test]
    fn merge_preserva_prioridade_e_acrescenta_conexao_nova() {
        let previous = vec![path(2, 20), path(1, 10)];
        let current = vec![path(1, 10), path(3, 5), path(2, 20)];
        let merged = merge_path_priority(&previous, current);
        assert_eq!(merged.iter().map(|p| p.if_index).collect::<Vec<_>>(), vec![2, 1, 3]);
    }

    #[test]
    fn status_marca_apenas_a_conexao_ativa() {
        let statuses = path_statuses(&[path(2, 20), path(1, 10)], 1);
        assert!(!statuses[0].active);
        assert!(statuses[1].active);
        assert_eq!(statuses[1].priority, 2);
    }

    #[test]
    fn limpeza_remove_toda_rota_pelo_gateway_do_tunel() {
        let script = stale_route_cleanup_script();
        assert!(script.contains("-NextHop '10.99.0.1'"));
        assert!(script.contains("Remove-NetRoute"));
    }
}
