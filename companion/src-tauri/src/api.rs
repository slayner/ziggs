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
fn crash_report_request(
    client: &Client,
    base_url: &str,
    install_id: String,
    payload: &crate::crash_report::CrashReport,
) -> reqwest::RequestBuilder {
    client
        .post(format!(
            "{}/companion/crash-report",
            base_url.trim_end_matches('/')
        ))
        .header("X-Ziggs-Install", install_id)
        .json(payload)
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
        let response = crash_report_request(
            &self.client,
            &self.base_url,
            crate::config::install_id(),
            payload,
        )
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

#[cfg(test)]
mod tests {
    use super::crash_report_request;
    use reqwest::Client;

    #[test]
    fn crash_report_request_includes_a_valid_installation_header() {
        let payload = crate::crash_report::CrashReport {
            kind: "frontend".into(),
            version: "0.2.15".into(),
            os: "windows".into(),
            arch: "x86_64".into(),
            created_at: "2026-09-10T00:00:00Z".into(),
            uptime_ms: 0,
            process_id: 0,
            thread: "test".into(),
            message: "test".into(),
            location: String::new(),
            backtrace: String::new(),
            logs: String::new(),
        };

        let request = crash_report_request(
            &Client::new(),
            "https://ziggs.example/",
            "a".repeat(32),
            &payload,
        )
        .build()
        .expect("a requisição de crash deve ser construída");

        assert_eq!(
            request.headers().get("X-Ziggs-Install").unwrap(),
            "a".repeat(32).as_str()
        );
        assert_eq!(
            request.url().as_str(),
            "https://ziggs.example/companion/crash-report"
        );
    }
}
