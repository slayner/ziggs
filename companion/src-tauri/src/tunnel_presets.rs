// VPS manifest fetched from the site at runtime — no hardcoded endpoints.
// The companion fetches https://ziggs.xyz/vps-manifest.json, pings each VPS,
// and shows only the ones that respond. Adding/removing a VPS = editing the
// JSON on the site, no app release needed.

use std::sync::Arc;
use tokio::sync::RwLock;

const MANIFEST_URL: &str = "https://ziggs.xyz/vps-manifest.json";

#[derive(Clone, Debug, serde::Deserialize)]
pub struct VpsEntry {
    pub id: String,
    pub label: String,
    pub country: String,
    pub endpoint: String,
    pub server_pubkey: String,
    pub ping_url: String,
}

#[derive(Clone, Debug, serde::Deserialize)]
struct Manifest {
    vps: Vec<VpsEntry>,
}

static CACHE: std::sync::OnceLock<Arc<RwLock<Option<(Manifest, std::time::Instant)>>>> =
    std::sync::OnceLock::new();

fn cache() -> &'static Arc<RwLock<Option<(Manifest, std::time::Instant)>>> {
    CACHE.get_or_init(|| Arc::new(RwLock::new(None)))
}

const TTL: std::time::Duration = std::time::Duration::from_secs(300);

/// Fetch the VPS manifest from the site, with a 5-minute cache.
pub async fn fetch_manifest() -> Vec<VpsEntry> {
    {
        let g = cache().read().await;
        if let Some((m, at)) = g.as_ref() {
            if at.elapsed() < TTL {
                return m.vps.clone();
            }
        }
    }
    match reqwest::get(MANIFEST_URL).await {
        Ok(resp) => match resp.json::<Manifest>().await {
            Ok(manifest) => {
                let mut g = cache().write().await;
                *g = Some((manifest.clone(), std::time::Instant::now()));
                manifest.vps
            }
            Err(e) => {
                tracing::warn!("VPS manifest parse error: {e}");
                cache()
                    .read()
                    .await
                    .as_ref()
                    .map(|(m, _)| m.vps.clone())
                    .unwrap_or_default()
            }
        },
        Err(e) => {
            tracing::warn!("VPS manifest fetch failed: {e}");
            cache()
                .read()
                .await
                .as_ref()
                .map(|(m, _)| m.vps.clone())
                .unwrap_or_default()
        }
    }
}

/// Find a VPS by id from the (cached) manifest.
pub async fn for_id(id: &str) -> Option<VpsEntry> {
    fetch_manifest().await.into_iter().find(|v| v.id == id)
}

// Hosts to ping per region for the "Direct" row. Americas game server blocks
// ICMP, so we use a nearby Washington DC NTT backbone as approximation.
pub const ALBION_GAME_IPS: &[(&str, &str)] = &[
    ("americas", "129.250.6.21"),
    ("asia", "5.45.187.10"),
    ("europe", "193.169.238.10"),
];

pub fn albion_game_ip(region: &str) -> Option<&'static str> {
    ALBION_GAME_IPS
        .iter()
        .find(|(r, _)| *r == region)
        .map(|(_, ip)| *ip)
}
