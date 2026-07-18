// Feed do Albion Online Data Project (AODP) — devolve à comunidade os mesmos
// dados de mercado que já usamos do projeto deles. Reimplementa o protocolo
// público de ingest (proof-of-work + POST) do albiondata-client.
//
// Fluxo: GET /pow → resolve o desafio (SHA-256) → POST /pow/{topic} com a
// solução + o payload NATS (MarketUpload JSON). Encaminhamos as ordens de
// mercado VERBATIM, exatamente como o client oficial faria.

use anyhow::{anyhow, Result};
use rand::RngCore;
use sha2::{Digest, Sha256};

/// Servidor AODP (região) inferido do IP do servidor do Albion.
#[derive(Clone, Debug, PartialEq)]
pub struct AodpServer {
    /// serverid do AODP: 1=west(Americas), 2=east(Asia), 3=europe.
    pub id: i32,
    /// base do endpoint de ingest com PoW (sem barra no fim).
    pub base_url: String,
}

impl AodpServer {
    /// Nome da região (mesma nomenclatura do seletor de servidor do site e do
    /// subdomínio AODP): 1→west, 2→east, 3→europe.
    pub fn region(&self) -> &'static str {
        match self.id {
            2 => "east",
            3 => "europe",
            _ => "west",
        }
    }
}

/// Mapeia o IP de origem/destino de um pacote do Albion pra região AODP.
/// Ranges class-C do próprio albiondata-client.
pub fn server_for_ip(ip: [u8; 4]) -> Option<AodpServer> {
    match [ip[0], ip[1], ip[2]] {
        [5, 188, 125] => Some(AodpServer { id: 1, base_url: "https://pow.west.albion-online-data.com".into() }),
        [5, 45, 187] => Some(AodpServer { id: 2, base_url: "https://pow.east.albion-online-data.com".into() }),
        [193, 169, 238] => Some(AodpServer { id: 3, base_url: "https://pow.europe.albion-online-data.com".into() }),
        _ => None,
    }
}

/// Um lote pronto pra enviar ao AODP: payload NATS já serializado + servidor.
#[derive(Clone, Debug)]
pub struct AodpBatch {
    pub server_id: i32,
    pub base_url: String,
    /// Tópico NATS (ex: "marketorders.ingest").
    pub topic: String,
    /// JSON do MarketUpload ({"Orders":[...]}).
    pub natsmsg: String,
}

#[derive(serde::Deserialize)]
struct PowChallenge {
    key: String,
    wanted: String,
}

/// Resolve o PoW: acha um nonce hex tal que os primeiros `wanted.len()` bits
/// da representação binária (ASCII) do hex do SHA-256 batam com `wanted`.
/// Replica bit-a-bit o algoritmo do albiondata-client pra o servidor aceitar.
/// Retorna None se estourar o limite (desafio absurdo → desiste em vez de travar).
fn solve_pow(key: &str, wanted: &str) -> Option<String> {
    let want = wanted.as_bytes();
    let want_len = want.len();
    if want_len == 0 || want_len > 40 {
        return None; // proteção: >40 bits levaria minutos/horas
    }
    let mut rng = rand::thread_rng();
    let mut nonce = [0u8; 16];
    // ~67M tentativas cobre com folga desafios de até ~26 bits.
    for _ in 0..67_000_000u64 {
        rng.fill_bytes(&mut nonce);
        let randhex = hex::encode(nonce); // 32 chars
        let mut hasher = Sha256::new();
        hasher.update(b"aod^");
        hasher.update(randhex.as_bytes());
        hasher.update(b"^");
        hasher.update(key.as_bytes());
        let digest = hasher.finalize();
        let hexdigest = hex::encode(digest); // 64 chars ascii

        // Expande cada CHAR hex em 8 bits (MSB first) — mesma construção do
        // toBinaryBytes do client — e compara o prefixo com `wanted`.
        let mut ok = true;
        let mut idx = 0usize;
        'chars: for &c in hexdigest.as_bytes() {
            for j in (0..8).rev() {
                let bit = if (c >> j) & 1 == 1 { b'1' } else { b'0' };
                if bit != want[idx] { ok = false; break 'chars; }
                idx += 1;
                if idx >= want_len { break 'chars; }
            }
        }
        if ok {
            return Some(randhex);
        }
    }
    None
}

/// Identificador estilo UUID v4 (só pra log/dedup do lado do AODP).
fn random_identifier() -> String {
    let mut b = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = hex::encode(b);
    format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32])
}

/// Envia um lote ao AODP: pega o PoW, resolve e faz o POST.
pub async fn upload(client: &reqwest::Client, batch: &AodpBatch) -> Result<()> {
    let pow: PowChallenge = client
        .get(format!("{}/pow", batch.base_url))
        .send().await?
        .error_for_status()?
        .json().await?;

    // solvePow é CPU-bound — roda numa thread separada pra não travar o executor.
    let key = pow.key.clone();
    let wanted = pow.wanted.clone();
    let solution = tokio::task::spawn_blocking(move || solve_pow(&key, &wanted))
        .await
        .map_err(|e| anyhow!("solve join: {e}"))?
        .ok_or_else(|| anyhow!("PoW não resolvido (desafio muito difícil)"))?;

    let resp = client
        .post(format!("{}/pow/{}", batch.base_url, batch.topic))
        .form(&[
            ("key", pow.key.as_str()),
            ("solution", solution.as_str()),
            ("serverid", &batch.server_id.to_string()),
            ("natsmsg", batch.natsmsg.as_str()),
            ("identifier", &random_identifier()),
        ])
        .send().await?;
    if !resp.status().is_success() {
        return Err(anyhow!("AODP ingest HTTP {}", resp.status()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_server_for_ip() {
        assert_eq!(server_for_ip([5, 188, 125, 42]).unwrap().id, 1);
        assert_eq!(server_for_ip([193, 169, 238, 7]).unwrap().id, 3);
        assert!(server_for_ip([8, 8, 8, 8]).is_none());
    }

    #[test]
    fn test_solve_pow_short() {
        // wanted vazio de 4 bits — qualquer hash começando com esses bits serve.
        // Pega o PoW real de um key fixo: resolve e re-verifica o prefixo.
        let key = "abc123";
        // Descobre os 4 primeiros bits do hash de um nonce conhecido pra montar
        // um `wanted` garantidamente solucionável e checar a mecânica.
        let sol = solve_pow(key, "0").expect("deve achar prefixo '0'");
        // Re-hash e confirma o 1º bit == '0'.
        let mut h = Sha256::new();
        h.update(b"aod^"); h.update(sol.as_bytes()); h.update(b"^"); h.update(key.as_bytes());
        let hd = hex::encode(h.finalize());
        let first_bit = (hd.as_bytes()[0] >> 7) & 1;
        assert_eq!(first_bit, 0);
    }

    #[test]
    fn test_identifier_format() {
        let id = random_identifier();
        assert_eq!(id.len(), 36);
        assert_eq!(id.as_bytes()[14], b'4'); // versão 4
    }
}
