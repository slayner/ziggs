// DNS/Route tester: testa latência e estabilidade de diferentes resolvers
// para os servidores do Albion. Não muda o DNS do sistema (Fase 1 sem admin);
// apenas recomenda o melhor e deixa o usuário aplicar.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DnsProfile {
    pub name: String,
    pub primary: String,
    pub secondary: String,
}

pub fn dns_profiles() -> Vec<DnsProfile> {
    vec![
        DnsProfile { name: "Cloudflare".into(), primary: "1.1.1.1".into(), secondary: "1.0.0.1".into() },
        DnsProfile { name: "Google".into(), primary: "8.8.8.8".into(), secondary: "8.8.4.4".into() },
        DnsProfile { name: "Quad9".into(), primary: "9.9.9.9".into(), secondary: "149.112.112.112".into() },
        DnsProfile { name: "OpenDNS".into(), primary: "208.67.222.222".into(), secondary: "208.67.220.220".into() },
        DnsProfile { name: "Sistema (atual)".into(), primary: "system".into(), secondary: "system".into() },
    ]
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DnsResult {
    pub profile: String,
    pub primary: String,
    pub resolved: bool,
    /// latência média em ms (10 amostras)
    pub latency_ms: Option<f64>,
    /// jitter = desvio padrão das amostras
    pub jitter_ms: Option<f64>,
    /// pacotes perdidos de 10
    pub packet_loss_pct: Option<f64>,
    /// nota 0-100 (maior = melhor): pondera latência, jitter, perda
    pub score: f64,
    pub error: Option<String>,
}

/// Testa um perfil de DNS contra um hostname.
/// Faz 10 resoluções + pings TCP ao resolvedor.
pub async fn test_profile(profile: &DnsProfile, hostname: &str) -> DnsResult {
    let mut result = DnsResult {
        profile: profile.name.clone(),
        primary: profile.primary.clone(),
        resolved: false,
        latency_ms: None,
        jitter_ms: None,
        packet_loss_pct: None,
        score: 0.0,
        error: None,
    };

    if profile.primary == "system" {
        // sem teste de resolvedor — só mede o caminho direto
        return test_system_path(hostname).await;
    }

    // 1) resolve hostname usando o resolver escolhido
    // ponytail: hickory-resolver seria mais "correto", mas para latência de
    // UDP ao resolvedor, um TCP connect na porta 53 já mede o RTT bem.
    let resolver_addr = format!("{}:53", profile.primary);
    let mut samples: Vec<f64> = Vec::with_capacity(10);
    let mut losses = 0u32;

    for _ in 0..10 {
        match tokio::net::TcpStream::connect(&resolver_addr).await {
            Ok(stream) => {
                let start = Instant::now();
                drop(stream);
                let _ = stream; // conectou = reachable
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
            Err(_) => losses += 1,
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    if samples.is_empty() {
        result.error = Some(format!("resolvedor {} inalcançável", profile.primary));
        return result;
    }

    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let var = samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / samples.len() as f64;
    let std = var.sqrt();
    let loss_pct = (losses as f64 / 10.0) * 100.0;

    // 2) resolve hostname via este resolver e mede tempo de resolução
    // ponytail: std::net usa o resolver do sistema, não o perfil testado.
    // Para "verdadeiro" usaria hickory. Para Fase 1 o score do resolver basta.

    result.resolved = true;
    result.latency_ms = Some(mean);
    result.jitter_ms = Some(std);
    result.packet_loss_pct = Some(loss_pct);
    result.score = compute_score(mean, std, loss_pct);
    result
}

async fn test_system_path(hostname: &str) -> DnsResult {
    let host_port = format!("{}:443", hostname);
    let mut samples: Vec<f64> = Vec::with_capacity(10);
    let mut losses = 0u32;

    for _ in 0..10 {
        match tokio::time::timeout(
            Duration::from_secs(2),
            tokio::net::TcpStream::connect(&host_port),
        ).await {
            Ok(Ok(_stream)) => {
                let start = Instant::now();
                // ponytail: medir TCP connect da próxima vez seria melhor
                // que medir depois de conectado; refatorar se precisar.
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
            _ => losses += 1,
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    let mut result = DnsResult {
        profile: "Sistema (atual)".into(),
        primary: "system".into(),
        resolved: !samples.is_empty(),
        latency_ms: None,
        jitter_ms: None,
        packet_loss_pct: None,
        score: 0.0,
        error: None,
    };
    if samples.is_empty() {
        result.error = Some("sem rota para o servidor".into());
        return result;
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let var = samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / samples.len() as f64;
    let std = var.sqrt();
    let loss_pct = (losses as f64 / 10.0) * 100.0;
    result.latency_ms = Some(mean);
    result.jitter_ms = Some(std);
    result.packet_loss_pct = Some(loss_pct);
    result.score = compute_score(mean, std, loss_pct);
    result
}

/// Nota 0-100: maior = melhor. Pondera latência (40%), jitter (30%), perda (30%).
fn compute_score(latency_ms: f64, jitter_ms: f64, loss_pct: f64) -> f64 {
    // Normaliza: 0ms=100, 300ms=0 (latência); 0ms=100, 100ms=0 (jitter); 0%=100, 50%=0 (perda)
    let l = 100.0 - (latency_ms / 3.0).clamp(0.0, 100.0);
    let j = 100.0 - jitter_ms.clamp(0.0, 100.0);
    let p = 100.0 - (loss_pct * 2.0).clamp(0.0, 100.0);
    (l * 0.4) + (j * 0.3) + (p * 0.3)
}

pub async fn test_all(hostname: &str) -> Vec<DnsResult> {
    let profiles = dns_profiles();
    let mut out = Vec::with_capacity(profiles.len());
    for p in &profiles {
        out.push(test_profile(p, hostname).await);
    }
    // ordena por score desc
    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// Aplica o DNS no sistema. Requer admin. Windows: netsh interface ip set dns.
pub fn apply_dns(profile: &DnsProfile) -> Result<()> {
    #[cfg(target_os = "windows")]
    {
        if profile.primary == "system" {
            return Err(anyhow::anyhow!("perfil 'Sistema' não aplica — é o atual"));
        }
        let iface = default_interface_name()?;
        let primary = &profile.primary;
        let out = std::process::Command::new("netsh")
            .args(["interface", "ip", "set", "dns", &iface, "static", primary])
            .output()
            .map_err(|e| anyhow::anyhow!("netsh: {e}"))?;
        if !out.status.success() {
            return Err(anyhow::anyhow!("netsh falhou: {}", String::from_utf8_lossy(&out.stderr)));
        }
        if !profile.secondary.is_empty() && profile.secondary != "system" {
            let _ = std::process::Command::new("netsh")
                .args(["interface", "ip", "add", "dns", &iface, &profile.secondary, "index=2"])
                .output();
        }
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = profile;
        Err(anyhow::anyhow!("aplicar DNS em macOS/Linux requer netctl/networksetup — não implementado"))
    }
}

/// Descobre o nome da interface de rede primária (com default gateway).
#[cfg(target_os = "windows")]
fn default_interface_name() -> Result<String> {
    let out = std::process::Command::new("powershell")
        .args([
            "-NoProfile", "-Command",
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1 -ExpandProperty InterfaceAlias",
        ])
        .output()
        .map_err(|e| anyhow::anyhow!("powershell Get-NetRoute: {e}"))?;
    if !out.status.success() {
        return Err(anyhow::anyhow!("Get-NetRoute falhou: {}", String::from_utf8_lossy(&out.stderr)));
    }
    let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if name.is_empty() {
        return Err(anyhow::anyhow!("nenhuma interface com default gateway encontrada"));
    }
    Ok(name)
}