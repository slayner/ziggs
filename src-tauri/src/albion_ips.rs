// Resolve Albion server hostnames to IPs and caches them for 5 minutes.
// IPs change as datacenters rotate, so periodic re-resolution is required.

use std::net::{IpAddr, Ipv4Addr, ToSocketAddrs};
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

// Albion server hostnames (must match player_tracker.HOSTS)
const ALBION_HOSTNAMES: &[&str] = &[
    "gameinfo.albiononline.com",        // Americas
    "gameinfo-ams.albiononline.com",    // Europe
    "gameinfo-sgp.albiononline.com",    // Asia
];

const ALBION_GAME_NETWORKS: &[[u8; 3]] = &[
    [5, 188, 125],
    [5, 45, 187],
    [193, 169, 238],
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

const TTL: Duration = Duration::from_secs(300); // 5 minutes

/// Resolve Albion hostnames to IPs, using a 5-minute cache.
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
    // Re-resolve on a blocking thread because ToSocketAddrs is synchronous.
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

/// Split-tunnel destinations: Photon /24 game networks and gameinfo /32 hosts.
pub async fn albion_route_targets() -> Vec<(Ipv4Addr, Ipv4Addr)> {
    let mut routes = ALBION_GAME_NETWORKS.iter()
        .map(|p| (Ipv4Addr::new(p[0], p[1], p[2], 0), Ipv4Addr::new(255, 255, 255, 0)))
        .collect::<Vec<_>>();
    routes.extend(albion_server_ips().await.into_iter().filter_map(|ip| match ip {
        IpAddr::V4(ip) => Some((ip, Ipv4Addr::new(255, 255, 255, 255))),
        IpAddr::V6(_) => None,
    }));
    routes.sort();
    routes.dedup();
    routes
}

/// Force a cache refresh.
pub async fn refresh() {
    let c = cache().await;
    let mut g = c.lock().await;
    *g = None;
}
