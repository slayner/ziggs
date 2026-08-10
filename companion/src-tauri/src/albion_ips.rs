// Resolve Albion server hostnames to IPs and caches them for 5 minutes.
// IPs change as datacenters rotate, so periodic re-resolution is required.

use std::net::{IpAddr, Ipv4Addr, ToSocketAddrs};
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

// Game server hostnames (REST API — gameinfo, not game traffic).
// These resolve to Cloudflare/CloudFront. Kept for albion_server_ips()
// callers that need the gameinfo endpoints (scanner, warmer).
const ALBION_HOSTNAMES: &[&str] = &[
    "gameinfo.albiononline.com",     // Americas
    "gameinfo-ams.albiononline.com", // Europe
    "gameinfo-sgp.albiononline.com", // Asia
];

// Login/account/status hostnames — the game connects to these over TCP 443.
// They resolve to CloudFront/Cloudflare IPs that rotate frequently.
// We resolve them dynamically and add /32 routes so login and server
// status work through the tunnel (like ExitLag does).
const ALBION_LOGIN_HOSTNAMES: &[&str] = &[
    "albiononline.com",
    "api.albiononline.com",
    "status.albiononline.com",
];

// Photon /24 game networks (UDP game servers).
pub const ALBION_GAME_NETWORKS: &[[u8; 3]] = &[[5, 188, 125], [5, 45, 187], [193, 169, 238]];

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
    })
    .await
    .unwrap_or_default();
    let mut g = c.lock().await;
    *g = Some(IpCache {
        ips: ips.clone(),
        resolved_at: Instant::now(),
    });
    ips
}

/// Map Albion region name to its /24 game network prefix.
pub fn region_network(region: &str) -> Option<[u8; 3]> {
    match region {
        "americas" => Some([5, 188, 125]),
        "asia" => Some([5, 45, 187]),
        "europe" => Some([193, 169, 238]),
        _ => None,
    }
}

/// Split-tunnel destinations for a specific region:
/// 1. That region's Photon /24 game network (UDP game server).
/// 2. Login/status/API hostnames resolved to /32 — CloudFront/Cloudflare IPs.
/// Only the selected region's game network is routed, so routing Asia
/// doesn't disrupt an active Americas connection.
pub async fn albion_route_targets_for(region: &str) -> Vec<(Ipv4Addr, Ipv4Addr)> {
    let mut routes: Vec<(Ipv4Addr, Ipv4Addr)> = Vec::new();
    // Only route the selected region's game /24.
    if let Some(p) = region_network(region) {
        routes.push((
            Ipv4Addr::new(p[0], p[1], p[2], 0),
            Ipv4Addr::new(255, 255, 255, 0),
        ));
    }
    // Resolve login/status hostnames and add /32 routes (shared across regions).
    let login_ips = tokio::task::spawn_blocking(|| {
        let mut ips = Vec::new();
        for host in ALBION_LOGIN_HOSTNAMES {
            if let Ok(iter) = (*host, 443u16).to_socket_addrs() {
                for addr in iter {
                    ips.push(addr.ip());
                }
            }
        }
        ips.sort();
        ips.dedup();
        ips
    })
    .await
    .unwrap_or_default();
    for ip in login_ips {
        if let IpAddr::V4(v4) = ip {
            routes.push((v4, Ipv4Addr::new(255, 255, 255, 255)));
        }
    }
    routes.sort();
    routes.dedup();
    routes
}

/// Split-tunnel destinations: ALL game networks + login IPs.
/// Used when the tunnel is the only routing option (no region selected yet).
pub async fn albion_route_targets() -> Vec<(Ipv4Addr, Ipv4Addr)> {
    let mut routes: Vec<(Ipv4Addr, Ipv4Addr)> = ALBION_GAME_NETWORKS
        .iter()
        .map(|p| {
            (
                Ipv4Addr::new(p[0], p[1], p[2], 0),
                Ipv4Addr::new(255, 255, 255, 0),
            )
        })
        .collect();
    let login_ips = tokio::task::spawn_blocking(|| {
        let mut ips = Vec::new();
        for host in ALBION_LOGIN_HOSTNAMES {
            if let Ok(iter) = (*host, 443u16).to_socket_addrs() {
                for addr in iter {
                    ips.push(addr.ip());
                }
            }
        }
        ips.sort();
        ips.dedup();
        ips
    })
    .await
    .unwrap_or_default();
    for ip in login_ips {
        if let IpAddr::V4(v4) = ip {
            routes.push((v4, Ipv4Addr::new(255, 255, 255, 255)));
        }
    }
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
