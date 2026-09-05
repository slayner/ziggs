use anyhow::{anyhow, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};

pub struct ApiClient {
    base_url: String,
    client: Client,
}
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct SpellName {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub pt: Option<String>,
    #[serde(default)]
    pub es: Option<String>,
    #[serde(default)]
    pub icon: Option<String>,
    #[serde(default)]
    pub fam: Option<String>,
}
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
fn ensure_json(response: &reqwest::Response, label: &str) -> Result<()> {
    if !response.status().is_success() {
        return Err(anyhow!("{label} falhou: HTTP {}", response.status()));
    }
    if !response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .contains("json")
    {
        return Err(anyhow!("{label}: resposta não é JSON"));
    }
    Ok(())
}
impl ApiClient {
    pub fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            client: Client::builder()
                .user_agent("ziggs-companion/0.1")
                .timeout(std::time::Duration::from_secs(60))
                .build()
                .unwrap_or_default(),
        }
    }
    pub async fn report_crash(&self, payload: &crate::crash_report::CrashReport) -> Result<()> {
        let response = self
            .client
            .post(format!("{}/companion/crash-report", self.base_url))
            .json(payload)
            .send()
            .await?;
        if response.status().is_success() {
            Ok(())
        } else {
            Err(anyhow!("relatório de falha: HTTP {}", response.status()))
        }
    }
    pub async fn spell_names(&self) -> Result<Vec<SpellName>> {
        let response = self
            .client
            .get(format!("{}/companion/spells", self.base_url))
            .send()
            .await?;
        ensure_json(&response, "habilidades")?;
        Ok(response.json().await?)
    }
    pub async fn items(&self) -> Result<Vec<ItemName>> {
        let response = self
            .client
            .get(format!("{}/companion/items", self.base_url))
            .send()
            .await?;
        ensure_json(&response, "itens")?;
        Ok(response.json().await?)
    }
}
