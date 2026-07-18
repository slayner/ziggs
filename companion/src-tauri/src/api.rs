// Cliente HTTP que fala com o backend Ziggs. Sem auth (APIs públicas/companion).
// Tudo é anon: scan/claim e scan/report — o backend confia no dado validando
// contra a API pública do Albion. Lootlog é local, nunca vai pro backend.

use anyhow::{anyhow, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};

pub struct ApiClient {
    base_url: String,
    client: Client,
    /// Token de portador (bearer) Discord — None = não logado.
    /// Enviado como header Authorization: Bearer <token> nas rotas /companion/auth/*
    /// e /companion/lootlog/*.
    discord_token: Option<String>,
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
    /// Cada batalha encontrada como JSON cru da API do Albion.
    pub found: Vec<serde_json::Value>,
    /// IDs sondados que voltaram 404 (não existem).
    pub missing: Vec<i64>,
    /// IDs que falharam (timeout/5xx) — re-tentar depois.
    pub errors: Vec<i64>,
    /// Nick configurado — backend credita batalhas novas (found_by) pro
    /// agradecimento na página pública. None = anônimo.
    pub character_name: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ScanReportOut {
    pub accepted: usize,
    pub rejected: usize,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct DnsTarget {
    pub region: String,
    pub hostname: String,
}

/// Uma entrada da tabela de feitiços. `id` = uniquename do dump (ex.
/// "HEROICSTRIKE"), útil pra conferir na calibração; `name` = nome legível.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct SpellName {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Deserialize)]
pub struct DnsTargetsOut {
    /// hostnames dos 3 servidores Albion por região.
    pub servers: Vec<DnsTarget>,
}

impl ApiClient {
    pub fn new(base_url: &str) -> Self {
        // X-Ziggs-Install em TODA request: identidade estável da instalação.
        // Como é default header do Client, nenhum call site precisa saber disso.
        let mut headers = reqwest::header::HeaderMap::new();
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&crate::config::install_id()) {
            headers.insert("X-Ziggs-Install", v);
        }
        let client = Client::builder()
            .user_agent("ziggs-companion/0.1")
            .default_headers(headers)
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .unwrap_or_default();
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            client,
            discord_token: None,
        }
    }

    pub fn with_token(mut self, token: Option<String>) -> Self {
        self.discord_token = token;
        self
    }

    fn auth_header(&self) -> Option<(&str, String)> {
        self.discord_token.as_ref().map(|t| ("Authorization", format!("Bearer {}", t)))
    }

    pub async fn claim_scan(&self) -> Result<ScanClaim> {
        let url = format!("{}/companion/scan/claim", self.base_url);
        let resp = self.client.post(&url).send().await?;
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Err(anyhow!("sem trabalho"));
        }
        if !resp.status().is_success() {
            return Err(anyhow!("claim falhou: HTTP {}", resp.status()));
        }
        let out: ScanClaim = resp.json().await?;
        Ok(out)
    }

    pub async fn report_scan(&self, payload: &ScanReportIn) -> Result<ScanReportOut> {
        let url = format!("{}/companion/scan/report", self.base_url);
        let resp = self.client.post(&url).json(payload).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("report falhou: HTTP {}", resp.status()));
        }
        let out: ScanReportOut = resp.json().await?;
        Ok(out)
    }

    pub async fn dns_targets(&self) -> Result<DnsTargetsOut> {
        let url = format!("{}/companion/dns/targets", self.base_url);
        let resp = self.client.get(&url).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("dns targets falhou: HTTP {}", resp.status()));
        }
        let out: DnsTargetsOut = resp.json().await?;
        Ok(out)
    }

    /// Tabela de nomes de feitiço (índice = posição). Baixada uma vez e
    /// cacheada em disco pelo chamador — muda só quando o Albion patcheia.
    pub async fn spell_names(&self) -> Result<Vec<SpellName>> {
        let url = format!("{}/companion/spells", self.base_url);
        let resp = self.client.get(&url).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("spells falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    pub async fn submit_prices(&self, rows: &[serde_json::Value]) -> Result<()> {
        let url = format!("{}/companion/prices/submit", self.base_url);
        let body = serde_json::json!({ "rows": rows });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("prices submit falhou: HTTP {}", resp.status()));
        }
        Ok(())
    }

    pub async fn submit_market_history(&self, rows: &[serde_json::Value]) -> Result<()> {
        let url = format!("{}/companion/market-history/submit", self.base_url);
        let body = serde_json::json!({ "rows": rows });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("market-history submit falhou: HTTP {}", resp.status()));
        }
        Ok(())
    }

    // ── Discord OAuth pairing ──────────────────────────────────────────────

    pub fn auth_start_url(&self, nonce: &str) -> String {
        format!("{}/companion/auth/start?nonce={}", self.base_url, nonce)
    }

    pub async fn auth_poll(&self, nonce: &str) -> Result<AuthPollResult> {
        let url = format!("{}/companion/auth/poll?nonce={}", self.base_url, nonce);
        let resp = self.client.get(&url).send().await?;
        if resp.status() == reqwest::StatusCode::REQUEST_TIMEOUT {
            return Err(anyhow!("aguardando login"));
        }
        if !resp.status().is_success() {
            return Err(anyhow!("auth poll falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    // ── Lootlog auto-submit ────────────────────────────────────────────────

    pub async fn active_events(&self, guild_id: &str) -> Result<Vec<ActiveEvent>> {
        let (key, val) = self.auth_header().ok_or_else(|| anyhow!("não logado"))?;
        let url = format!("{}/companion/lootlog/active-events?guild_id={}", self.base_url, guild_id);
        let resp = self.client.get(&url).header(key, &val).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("active-events falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    pub async fn submit_lootlog(
        &self, guild_id: i64, event_id: i64, csv_text: &str,
    ) -> Result<LootlogIngestOut> {
        let (key, val) = self.auth_header().ok_or_else(|| anyhow!("não logado"))?;
        let url = format!("{}/companion/lootlog/ingest", self.base_url);
        let body = serde_json::json!({
            "guild_id": guild_id,
            "event_id": event_id,
            "csv_text": csv_text,
            "file_name": "companion-lootlog.csv",
        });
        let resp = self.client.post(&url).header(key, &val).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow!("lootlog ingest falhou: HTTP {} {}", status, text));
        }
        Ok(resp.json().await?)
    }
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct AuthPollResult {
    pub token: String,
    pub user_id: String,
    pub username: String,
    #[serde(default)]
    pub global_name: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ActiveEvent {
    pub event_id: i64,
    pub title: Option<String>,
    pub scheduled_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct LootlogIngestOut {
    pub id: i64,
    pub row_count: i64,
    pub silver_total: i64,
    pub is_update: bool,
}