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
    /// IDs que responderam com batalha válida; o backend busca o payload oficial.
    pub found: Vec<i64>,
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

/// Uma entrada da tabela de feitiços. `id` = uniquename do dump (ex.
/// "HEROICSTRIKE"), útil pra conferir na calibração; `name` = nome legível.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct SpellName {
    pub id: String,
    pub name: String,
    /// Nome traduzido. Ausente quando o dump não localiza o feitiço (metade
    /// deles são sub-feitiços internos) ou quando é igual ao inglês.
    #[serde(default)]
    pub pt: Option<String>,
    #[serde(default)]
    pub es: Option<String>,
    /// uniquename do feitiço-PAI, quando este é um sub-feitiço interno que
    /// herdou o nome. O CDN tem arte pro sub-feitiço, mas é um ícone genérico
    /// de passiva — o da habilidade é o do pai. Ausente = usa o próprio id.
    #[serde(default)]
    pub icon: Option<String>,
    /// Família da arma dona do feitiço (bow, dagger, sword, …). Ausente em
    /// feitiço de mob/consumível, que não vem de arma nenhuma.
    #[serde(default)]
    pub fam: Option<String>,
}

/// Item do jogo por índice. `i` = campo `Index` do ao-bin-dump, o mesmo número
/// que vem no pacote de loot; `id` = UniqueName com encantamento
/// (T7_HEAD_PLATE_SET3@1). `pt`/`es` ausentes = igual ao inglês.
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
    /// hostnames dos 3 servidores Albion por região.
    pub servers: Vec<DnsTarget>,
}

/// Erro claro quando a resposta não é JSON.
///
/// O backend serve o SPA num catch-all: rota que não existe devolve
/// **200 com index.html**, não 404. Sem esta checagem o companion via
/// `is_success()` e só quebrava no parse, com erro de desserialização
/// ilegível — foi exatamente assim que um backend desatualizado (sem
/// `/companion/items` registrado) deixou o lootlog inteiro em `IDX_2958`
/// sem ninguém entender por quê.
fn ensure_json(resp: &reqwest::Response, what: &str) -> Result<()> {
    if !resp.status().is_success() {
        return Err(anyhow!("{what} falhou: HTTP {}", resp.status()));
    }
    let ct = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if !ct.contains("json") {
        return Err(anyhow!(
            "{what}: backend devolveu {ct:?} em vez de JSON — rota não registrada? \
             (backend desatualizado precisa reiniciar)"
        ));
    }
    Ok(())
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
            .timeout(std::time::Duration::from_secs(60))
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

    pub async fn claim_kill_scan(&self) -> Result<KillScanClaim> {
        let url = format!("{}/companion/kill-scan/claim", self.base_url);
        let resp = self.client.post(&url).send().await?;
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Err(anyhow!("sem trabalho"));
        }
        if !resp.status().is_success() {
            return Err(anyhow!("kill-scan claim falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    pub async fn report_kill_scan(&self, payload: &KillScanReportIn) -> Result<KillScanReportOut> {
        let url = format!("{}/companion/kill-scan/report", self.base_url);
        let resp = self.client.post(&url).json(payload).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("kill-scan report falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    /// Nomeia um personagem (o próprio do usuário) pra manter o perfil quente no
    /// site. Só o NOME — o backend busca o dado na Albion (ver /companion/warm).
    pub async fn warm_profile(&self, name: &str, region: &str) -> Result<WarmProfileOut> {
        let url = format!("{}/companion/warm", self.base_url);
        let body = serde_json::json!({ "name": name, "region": region });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("warm falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    /// Fase 2: reporta players vistos em jogo pra mantê-los quentes. O backend é
    /// refresh-only (só re-aquece quem já conhece e está velho); nome
    /// desconhecido é ignorado, não faz bootstrap (ver /companion/warm/seen).
    pub async fn warm_seen(&self, names: &[String], region: &str) -> Result<()> {
        let url = format!("{}/companion/warm/seen", self.base_url);
        let body = serde_json::json!({ "region": region, "names": names });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("warm/seen falhou: HTTP {}", resp.status()));
        }
        Ok(())
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
        ensure_json(&resp, "spells")?;
        Ok(resp.json().await?)
    }

    /// Catálogo de itens por índice. Mesmo padrão dos feitiços: baixa uma vez,
    /// o chamador cacheia em disco. ~1,5 MB.
    pub async fn items(&self) -> Result<Vec<ItemName>> {
        let url = format!("{}/companion/items", self.base_url);
        let resp = self.client.get(&url).send().await?;
        ensure_json(&resp, "items")?;
        Ok(resp.json().await?)
    }

    /// Mapeamento UniqueName → game_name (nome em inglês do jogo).
    /// Usado pelo sniffer pra converter ItemTypeId do pacote em game_name
    /// antes de mandar pro backend. ~500 KB.
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

    /// Estimativa ILUSTRATIVA do valor em prata dos loots capturados nesta
    /// sessão. Só alimenta o badge da aba Lootlog — não é usado em
    /// payout/reconcile. Preço vem do cache DB do backend (1h, mediana 6
    /// cidades); item novo = 1 HTTP, depois cacheado.
    pub async fn loot_silver_estimate(&self, items: &[(String, i64)]) -> Result<i64> {
        // Backend limita 200 itens por request — chunka em 190 (margem) e soma.
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
                return Err(anyhow!("silver-estimate falhou: HTTP {}", resp.status()));
            }
            let out: serde_json::Value = resp.json().await?;
            total += out.get("silver_total").and_then(|v| v.as_i64()).unwrap_or(0);
        }
        Ok(total)
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

    /// Eventos do usuário logado (todas as guildas) em andamento ou revisão.
    /// Sem guild_id — o backend deriva das inscrições.
    pub async fn active_events(&self) -> Result<Vec<ActiveEvent>> {
        let (key, val) = self.auth_header().ok_or_else(|| anyhow!("não logado"))?;
        let url = format!("{}/companion/lootlog/active-events", self.base_url);
        let resp = self.client.get(&url).header(key, &val).send().await?;
        if !resp.status().is_success() {
            return Err(anyhow!("active-events falhou: HTTP {}", resp.status()));
        }
        Ok(resp.json().await?)
    }

    /// A guilda não vai no corpo: o backend a deriva da inscrição do usuário
    /// no evento (e rejeita se não houver inscrição).
    pub async fn submit_lootlog(
        &self, event_id: i64, csv_text: &str,
    ) -> Result<LootlogIngestOut> {
        let (key, val) = self.auth_header().ok_or_else(|| anyhow!("não logado"))?;
        let url = format!("{}/companion/lootlog/ingest", self.base_url);
        let body = serde_json::json!({
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
    pub guild_id: i64,
    pub guild_name: Option<String>,
    pub title: Option<String>,
    pub scheduled_at: Option<String>,
    /// "in_progress" (rolando) ou "review" (fechou, em revisão — gatilho do
    /// auto-submit).
    pub state: String,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct LootlogIngestOut {
    pub id: i64,
    pub row_count: i64,
    pub silver_total: i64,
    pub is_update: bool,
}

/// Resposta de POST /companion/warm.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct WarmProfileOut {
    pub status: String,
}
