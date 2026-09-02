// HTTP client for the Ziggs backend. No auth (public/companion APIs).
// All anonymous: scan claim/report — backend validates against Albion public API.
// Lootlog is local, never sent to backend.

use anyhow::{anyhow, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};

pub struct ApiClient {
    base_url: String,
    client: Client,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ScanClaim {
    pub task_id: i64,
    pub battle_id_start: i64,
    pub battle_id_end: i64,
    pub server: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ScanReportIn {
    pub task_id: i64,
    pub region: String,
    /// IDs that returned a valid battle; backend fetches the official payload.
    pub found: Vec<i64>,
    /// Probed IDs that returned 404 (don't exist).
    pub missing: Vec<i64>,
    /// IDs that failed (timeout/5xx) — retry later.
    pub errors: Vec<i64>,
    /// Configured nickname — backend credits new battles (found_by) for
    /// public page attribution. None = anonymous.
    pub character_name: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ScanReportOut {
    pub accepted: usize,
    pub rejected: usize,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct KillScanClaim {
    pub region: String,
    pub event_id_start: i64,
    pub event_id_end: i64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct KillScanReportIn {
    pub region: String,
    pub event_id_start: i64,
    pub event_id_end: i64,
    pub found: Vec<i64>,
    pub missing: Vec<i64>,
    pub errors: Vec<i64>,
}

#[derive(Debug, Deserialize)]
pub struct KillScanReportOut {
    pub accepted: usize,
    pub rejected: usize,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct DnsTarget {
    pub region: String,
    pub hostname: String,
}

/// Spell table entry. `id` = uniquename from the dump (e.g. "HEROICSTRIKE"),
/// useful for calibration checks; `name` = human-readable name.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct SpellName {
    pub id: String,
    pub name: String,
    /// Localized name. Absent when the dump doesn't localize the spell
    /// (half are internal sub-spells) or when it equals English.
    #[serde(default)]
    pub pt: Option<String>,
    #[serde(default)]
    pub es: Option<String>,
    /// Parent spell's uniquename — inherited when this is an internal sub-spell.
    /// The CDN has art for the sub-spell but it's a generic passive icon;
    /// the parent's icon is the correct one. Absent = use own id.
    #[serde(default)]
    pub icon: Option<String>,
    /// Weapon family of the owning spell (bow, dagger, sword, …).
    /// Absent for mob/consumable spells (not from any weapon).
    #[serde(default)]
    pub fam: Option<String>,
}

/// Game item by loot index. `i` = ao-bin-dump document index, the same number
/// that arrives in loot packets; `id` = UniqueName with enchant
/// (T7_HEAD_PLATE_SET3@1). Missing `pt`/`es` means same as English.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ItemName {
    pub i: i32,
    pub id: String,
    pub en: String,
    #[serde(default)]
    pub pt: Option<String>,
    #[serde(default)]
    pub es: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct DnsTargetsOut {
    /// Albion server hostnames by region.
    pub servers: Vec<DnsTarget>,
}

/// Clear error when the response is not JSON.
///
/// The backend serves the SPA as a catch-all: an unregistered route returns
/// **200 with index.html**, not 404. Without this check the companion would
/// only fail at parse time with a confusing deserialization error.
fn ensure_json(resp: &reqwest::Response, what: &str) -> Result<()> {
    if !resp.status().is_success() {
        return Err(anyhow!("{what} failed: HTTP {}", resp.status()));
    }
    let ct = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if !ct.contains("json") {
        return Err(anyhow!(
            "{what}: backend returned {ct:?} instead of JSON — route not registered? \
             (backend out of date, needs restart)"
        ));
    }
    Ok(())
}

impl ApiClient {
    pub fn new(base_url: &str) -> Self {
        // X-Ziggs-Install is sent on every request as a stable install identity.
        // As a default header, no call site needs to set it manually.
        let mut headers = reqwest::header::HeaderMap::new();
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&crate::config::install_id()) {
            headers.insert("X-Ziggs-Install", v);
        }
        let client = Client::builder()
            .user_agent("ziggs-companion/0.1")
            .default_headers(headers)
            .timeout(std::time::Duration::from_secs(60))
            .build()
            .unwrap_or_default();
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            client,
        }
    }

    pub async fn report_crash(&self, payload: &crate::crash_report::CrashReport) -> Result<()> {
        let url = format!("{}/companion/crash-report", self.base_url);
        let resp = self.client.post(&url).json(payload).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("crash report failed: HTTP {}", resp.status()));
        }
        Ok(())
    }

    pub async fn claim_scan(&self) -> Result<ScanClaim> {
        let url = format!("{}/companion/scan/claim", self.base_url);
        let resp = self.client.post(&url).send().await?;
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Err(anyhow!("no work available"));
        }
        if !resp.status().is_success() {
            return Err(anyhow!("claim failed: HTTP {}", resp.status()));
        }
        let out: ScanClaim = resp.json().await?;
        Ok(out)
    }

    pub async fn report_scan(&self, payload: &ScanReportIn) -> Result<ScanReportOut> {
        let url = format!("{}/companion/scan/report", self.base_url);
        let resp = self.client.post(&url).json(payload).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("report failed: HTTP {}", resp.status()));
        }
        let out: ScanReportOut = resp.json().await?;
        Ok(out)
    }

    pub async fn claim_kill_scan(&self) -> Result<KillScanClaim> {
        let url = format!("{}/companion/kill-scan/claim", self.base_url);
        let resp = self.client.post(&url).send().await?;
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Err(anyhow!("no work available"));
        }
        if !resp.status().is_success() {
            return Err(anyhow!("kill-scan claim failed: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    pub async fn report_kill_scan(&self, payload: &KillScanReportIn) -> Result<KillScanReportOut> {
        let url = format!("{}/companion/kill-scan/report", self.base_url);
        let resp = self.client.post(&url).json(payload).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("kill-scan report failed: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    /// Names the user's own character to keep the profile warm on the site.
    /// Only the NAME is sent — the backend fetches data from Albion (see /companion/warm).
    pub async fn warm_profile(&self, name: &str, region: &str) -> Result<WarmProfileOut> {
        let url = format!("{}/companion/warm", self.base_url);
        let body = serde_json::json!({ "name": name, "region": region });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("warm failed: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    /// Phase 2: report players seen in-game to keep them warm. The backend is
    /// refresh-only (only re-warms who we already know and is stale); unknown
    /// names are ignored, no bootstrap (see /companion/warm/seen).
    pub async fn warm_seen(&self, names: &[String], region: &str) -> Result<()> {
        let url = format!("{}/companion/warm/seen", self.base_url);
        let body = serde_json::json!({ "region": region, "names": names });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("warm/seen failed: HTTP {}", resp.status()));
        }
        Ok(())
    }

    pub async fn dns_targets(&self) -> Result<DnsTargetsOut> {
        let url = format!("{}/companion/dns/targets", self.base_url);
        let resp = self.client.get(&url).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("dns targets failed: HTTP {}", resp.status()));
        }
        let out: DnsTargetsOut = resp.json().await?;
        Ok(out)
    }

    /// Spell name table indexed by position. Downloaded once and cached on
    /// disk by the caller; changes only when Albion patches.
    pub async fn spell_names(&self) -> Result<Vec<SpellName>> {
        let url = format!("{}/companion/spells", self.base_url);
        let resp = self.client.get(&url).send().await?;
        ensure_json(&resp, "spells")?;
        Ok(resp.json().await?)
    }

    /// Item catalog by loot index. Same pattern as spell names: download once,
    /// cache on disk. ~1.5 MB.
    pub async fn items(&self) -> Result<Vec<ItemName>> {
        let url = format!("{}/companion/items", self.base_url);
        let resp = self.client.get(&url).send().await?;
        ensure_json(&resp, "items")?;
        Ok(resp.json().await?)
    }

    /// UniqueName → in-game English name mapping. Used by the sniffer to
    /// translate packet ItemTypeId before uploading. ~500 KB.
    pub async fn items_map(&self) -> Result<std::collections::HashMap<String, String>> {
        let url = format!("{}/companion/items-map", self.base_url);
        let resp = self.client.get(&url).send().await?;
        ensure_json(&resp, "items-map")?;
        Ok(resp.json().await?)
    }

    pub async fn submit_prices(&self, rows: &[serde_json::Value]) -> Result<()> {
        let url = format!("{}/companion/prices/submit", self.base_url);
        let body = serde_json::json!({ "rows": rows });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("prices submit failed: HTTP {}", resp.status()));
        }
        Ok(())
    }

    pub async fn submit_market_history(&self, rows: &[serde_json::Value]) -> Result<()> {
        let url = format!("{}/companion/market-history/submit", self.base_url);
        let body = serde_json::json!({ "rows": rows });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!(
                "market-history submit failed: HTTP {}",
                resp.status()
            ));
        }
        Ok(())
    }

    /// Rough silver value estimate for loot captured this session. Feeds the
    /// Lootlog tab badge only — not used for payouts/reconcile. Prices come
    /// from the backend price cache (1h, median across 6 cities); a new item
    /// costs one HTTP, then is cached.
    pub async fn loot_silver_estimate(&self, items: &[(String, i64)]) -> Result<i64> {
        // Backend limits 200 items per request — chunk at 190 (margin) and sum.
        let chunk_size = 190;
        let mut total: i64 = 0;
        for chunk in items.chunks(chunk_size) {
            let url = format!("{}/companion/lootlog/silver-estimate", self.base_url);
            let body = serde_json::json!({
                "items": chunk.iter().map(|(id, q)| serde_json::json!({
                    "item_id": id, "quantity": q,
                })).collect::<Vec<_>>()
            });
            let resp = self.client.post(&url).json(&body).send().await?;
            if !resp.status().is_success() {
                return Err(anyhow!("silver-estimate failed: HTTP {}", resp.status()));
            }
            let out: serde_json::Value = resp.json().await?;
            total += out
                .get("silver_total")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
        }
        Ok(total)
    }

}

/// Response from POST /companion/warm.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct WarmProfileOut {
    pub status: String,
}
