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

use std::net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket};
use std::sync::Arc;
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
const WG_PORT: u16 = 51820;
const KEEPALIVE: Option<u16> = Some(25); // segundos

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
pub struct TunnelStatus {
    pub running: bool,
    pub connected: bool,
    pub direct_latency_ms: Option<f64>,
    pub tunnel_latency_ms: Option<f64>,
    pub using_tunnel: bool,
    pub last_error: Option<String>,
    pub bytes_sent: u64,
    pub bytes_received: u64,
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
        }
    }
}

pub struct Tunnel {
    pub status: Arc<Mutex<TunnelStatus>>,
    shutdown: Arc<Mutex<bool>>,
}

impl Tunnel {
    pub fn new() -> Self {
        Self {
            status: Arc::new(Mutex::new(TunnelStatus::default())),
            shutdown: Arc::new(Mutex::new(false)),
        }
    }

    pub async fn stop(&self) {
        *self.shutdown.lock().await = true;
        // remove rotas estáticas pra não deixar órfãs ao desligar
        self.remove_albion_routes().await.ok();
        let mut s = self.status.lock().await;
        s.using_tunnel = false;
        s.connected = false;
        s.running = false;
    }

    /// Remove as rotas estáticas do Albion (fallback pra rota direta).
    async fn remove_albion_routes(&self) -> Result<()> {
        #[cfg(target_os = "windows")]
        {
            let ips = albion_ips::albion_server_ips().await;
            for ip in ips {
                let gateway = TUNNEL_IPV4_GW.to_string();
                let _ = std::process::Command::new("route")
                    .args(["delete", &ip.to_string(), &gateway])
                    .output();
            }
        }
        Ok(())
    }

    /// Loop principal do túnel. Roda numa task separada.
    pub async fn run(&self, cfg: TunnelConfig) {
        *self.shutdown.lock().await = false;
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

        let mut s = self.status.lock().await;
        s.running = false;
    }

    /// Sobe túnel, mede latência (informativo), ativa rotas e roda.
    /// O usuário escolhe ligar — sem auto-fallback.
    async fn evaluate_and_run(&self, cfg: TunnelConfig) -> Result<()> {
        // 1. mede latência direta (informativo)
        let direct = measure_albion_latency_direct().await;
        {
            let mut s = self.status.lock().await;
            s.direct_latency_ms = direct;
        }

        // 2. sobe túnel SEM rotas ainda, mede latência via túnel
        let tunnel_lat = self.start_tunnel_no_routes(&cfg).await?;
        {
            let mut s = self.status.lock().await;
            s.tunnel_latency_ms = tunnel_lat;
        }

        // 3. ativa rotas — usuário escolheu ligar, respeitamos
        self.add_albion_routes().await?;
        {
            let mut s = self.status.lock().await;
            s.using_tunnel = true;
            s.connected = true;
        }

        // 4. loop de packets
        self.packet_loop(cfg).await?;
        Ok(())
    }

    /// Sobe o túnel WireGuard sem adicionar rotas — só pra medir latência.
    async fn start_tunnel_no_routes(&self, cfg: &TunnelConfig) -> Result<Option<f64>> {
        let privkey = parse_key(&cfg.client_privkey)
            .ok_or_else(|| anyhow!("chave privada inválida"))?;
        let server_pub = parse_key(&cfg.server_pubkey)
            .ok_or_else(|| anyhow!("chave pública do servidor inválida"))?;
        let endpoint: SocketAddr = cfg.endpoint.parse()
            .map_err(|e| anyhow!("endpoint inválido: {}", e))?;

        // Cria interface wintun
        #[cfg(target_os = "windows")]
        {
            let wintun = load_wintun()
                .map_err(|e| anyhow!("falha ao carregar wintun.dll: {}", e))?;
            let adapter = Adapter::create(&wintun, TUNNEL_ADAPTER_NAME, TUNNEL_ADAPTER_TYPE, None)
                .map_err(|e| anyhow!("falha ao criar adapter wintun (precisa admin): {}", e))?;
            adapter.set_address(TUNNEL_IPV4_ADDR).ok();
            adapter.set_netmask(TUNNEL_NETMASK).ok();
            adapter.set_gateway(Some(TUNNEL_IPV4_GW)).ok();
            adapter.set_mtu(TUNNEL_MTU).ok();

            let session = Arc::new(adapter.start_session(wintun::MAX_RING_CAPACITY)
                .map_err(|e| anyhow!("falha ao iniciar sessão wintun: {}", e))?);

            // Cria túnel WireGuard (boringtun)
            let secret = StaticSecret::from(privkey);
            let pub_key = PublicKey::from(&secret);
            let _ = pub_key;
            let peer_pub = PublicKey::from(server_pub);
            let mut tunn = Tunn::new(secret, peer_pub, None, KEEPALIVE, 0, None);

            // UDP socket pra falar com servidor WireGuard
            let udp = UdpSocket::bind("0.0.0.0:0")?;
            udp.connect(endpoint)?;

            // Mede latência via túnel: manda keepalive e espera resposta
            let tunnel_lat = measure_tunnel_latency(&mut tunn, &udp, &session, endpoint).await;

            Ok(tunnel_lat)
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = (privkey, server_pub, endpoint);
            Err(anyhow!("túnel WireGuard só suportado no Windows nesta versão"))
        }
    }

    /// Adiciona rotas estáticas pros IPs do Albion apontando pro túnel.
    async fn add_albion_routes(&self) -> Result<()> {
        #[cfg(target_os = "windows")]
        {
            let ips = albion_ips::albion_server_ips().await;
            for ip in ips {
                let gateway = TUNNEL_IPV4_GW.to_string();
                // route add <ip> mask 255.255.255.255 <gateway> metric 1
                let _ = std::process::Command::new("route")
                    .args(["add", &ip.to_string(), "mask", "255.255.255.255", &gateway, "metric", "1"])
                    .output();
            }
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
            let endpoint: SocketAddr = cfg.endpoint.parse()?;

            let wintun = load_wintun()
                .map_err(|e| anyhow!("wintun.dll: {}", e))?;
            let adapter = Adapter::open(&wintun, TUNNEL_ADAPTER_NAME)
                .or_else(|_| Adapter::create(&wintun, TUNNEL_ADAPTER_NAME, TUNNEL_ADAPTER_TYPE, None))
                .map_err(|e| anyhow!("adapter: {}", e))?;
            let session = Arc::new(adapter.start_session(wintun::MAX_RING_CAPACITY)?);

            let secret = StaticSecret::from(privkey);
            let peer_pub = PublicKey::from(server_pub);
            let mut tunn = Tunn::new(secret, peer_pub, None, KEEPALIVE, 0, None);

            let udp = UdpSocket::bind("0.0.0.0:0")?;
            udp.set_nonblocking(true)?;
            udp.connect(endpoint)?;
            udp.set_read_timeout(Some(Duration::from_millis(10)))?;

            let mut send_buf = vec![0u8; WG_BUFFER_SIZE];
            let mut recv_buf = vec![0u8; WG_BUFFER_SIZE];
            let mut tun_out_buf = vec![0u8; WG_BUFFER_SIZE];

            loop {
                if *self.shutdown.lock().await {
                    break;
                }

                let mut did_work = false;

                // 1. Recebe pacote da interface wintun → encrypta → manda UDP
                if let Ok(Some(packet)) = session.try_receive() {
                    let bytes = packet.bytes();
                    match tunn.encapsulate(bytes, &mut send_buf) {
                        TunnResult::WriteToNetwork(encrypted) => {
                            udp.send(encrypted)?;
                            let mut s = self.status.lock().await;
                            s.bytes_sent += encrypted.len() as u64;
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
                        match tunn.decapsulate(None, &recv_buf[..n], &mut tun_out_buf) {
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
                                udp.send(resp)?;
                            }
                            _ => {}
                        }
                        did_work = true;
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                    Err(e) => {
                        tracing::warn!("udp recv error: {}", e);
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
pub async fn measure_albion_latency_direct() -> Option<f64> {
    let host = "gameinfo.albiononline.com:443";
    let mut samples = Vec::new();
    for _ in 0..10 {
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

/// Mede latência via túnel — manda keepalive WireGuard e espera handshake.
async fn measure_tunnel_latency(
    tunn: &mut Tunn,
    udp: &UdpSocket,
    _session: &Arc<wintun::Session>,
    endpoint: SocketAddr,
) -> Option<f64> {
    // ponytail: mede handshake completo (init → response → first data)
    // Retorna None se não conseguir handshake em 5s.
    let mut send_buf = vec![0u8; WG_BUFFER_SIZE];
    let mut recv_buf = vec![0u8; WG_BUFFER_SIZE];
    let mut tun_out_buf = vec![0u8; WG_BUFFER_SIZE];
    udp.set_read_timeout(Some(Duration::from_millis(500))).ok()?;

    let mut samples = Vec::new();
    for _ in 0..3 {
        let start = Instant::now();
        // força handshake initiation
        match tunn.encapsulate(&[], &mut send_buf) {
            TunnResult::WriteToNetwork(init) => {
                if udp.send(init).is_ok() {
                    // espera resposta (handshake response ou keepalive)
                    match udp.recv(&mut recv_buf) {
                        Ok(n) => {
                        match tunn.decapsulate(None, &recv_buf[..n], &mut tun_out_buf) {
                                TunnResult::WriteToNetwork(_) => {
                                    // handshake response recebido
                                    samples.push(start.elapsed().as_secs_f64() * 1000.0);
                                }
                                _ => {}
                            }
                        }
                        Err(_) => {}
                    }
                }
            }
            _ => {}
        }
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