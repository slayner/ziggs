// Descoberta de IPs dos servidores do Albion — precisa resolver os hostnames
// periodicamente porque os IPs mudam (rotação de datacenters).
//
// Cache simples: resolve na primeira chamada e re-resolve a cada 5 min.

use std::net::{IpAddr, ToSocketAddrs};
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

// Hostnames dos servidores do Albion (batem com player_tracker.HOSTS)
const ALBION_HOSTNAMES: &[&str] = &[
    "gameinfo.albiononline.com",        // Americas
    "gameinfo-ams.albiononline.com",    // Europe
    "gameinfo-sgp.albiononline.com",    // Asia
];

#[derive(Clone)]
struct IpCache {
    ips: Vec<IpAddr>,
    resolved_at: Instant,
}

static CACHE: OnceLock<Mutex<Option<IpCache>>> = OnceLock::new();

async fn cache() -> &'static Mutex<Option<IpCache>> {
    CACHE.get_or_init(|| Mutex::new(None))
}

const TTL: Duration = Duration::from_secs(300); // 5 min

/// Resolve os hostnames do Albion em IPs. Usa cache de 5 min.
pub async fn albion_server_ips() -> Vec<IpAddr> {
    let c = cache().await;
    {
        let g = c.lock().await;
        if let Some(cache) = g.as_ref() {
            if cache.resolved_at.elapsed() < TTL {
                return cache.ips.clone();
            }
        }
    }
    // re-resolve em thread bloqueante (ToSocketAddrs é síncrono)
    let ips = tokio::task::spawn_blocking(|| {
        let mut ips = Vec::new();
        for host in ALBION_HOSTNAMES {
            if let Ok(iter) = (*host, 443u16).to_socket_addrs() {
                for addr in iter {
                    ips.push(addr.ip());
                }
            }
        }
        ips.sort();
        ips.dedup();
        ips
    }).await.unwrap_or_default();
    let mut g = c.lock().await;
    *g = Some(IpCache {
        ips: ips.clone(),
        resolved_at: Instant::now(),
    });
    ips
}

/// Força refresh do cache.
pub async fn refresh() {
    let c = cache().await;
    let mut g = c.lock().await;
    *g = None;
}