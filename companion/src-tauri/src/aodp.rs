// Albion Online Data Project (AODP) feed: returns market data to the community
// using the public albiondata-client ingest protocol (proof-of-work + POST).
//
// Flow: GET /pow → solve the SHA-256 challenge → POST /pow/{topic} with the
// solution and the NATS payload (MarketUpload JSON). Market orders are forwarded
// verbatim.

use anyhow::{anyhow, Result};
use rand::RngCore;
use sha2::{Digest, Sha256};

/// AODP server (region) inferred from the Albion server IP.
#[derive(Clone, Debug, PartialEq)]
pub struct AodpServer {
    /// AODP server id: 1=west (Americas), 2=east (Asia), 3=europe.
    pub id: i32,
    /// Ingest endpoint base URL (no trailing slash).
    pub base_url: String,
}

impl AodpServer {
    /// Region name matching the website server selector and AODP subdomain.
    pub fn region(&self) -> &'static str {
        match self.id {
            2 => "east",
            3 => "europe",
            _ => "west",
        }
    }
}

/// Map an Albion packet source/destination IP to an AODP region.
/// Class-C ranges mirror the albiondata-client.
pub fn server_for_ip(ip: [u8; 4]) -> Option<AodpServer> {
    match [ip[0], ip[1], ip[2]] {
        [5, 188, 125] => Some(AodpServer {
            id: 1,
            base_url: "https://pow.west.albion-online-data.com".into(),
        }),
        [5, 45, 187] => Some(AodpServer {
            id: 2,
            base_url: "https://pow.east.albion-online-data.com".into(),
        }),
        [193, 169, 238] => Some(AodpServer {
            id: 3,
            base_url: "https://pow.europe.albion-online-data.com".into(),
        }),
        _ => None,
    }
}

/// A ready-to-send AODP batch: serialized NATS payload + target server.
#[derive(Clone, Debug)]
pub struct AodpBatch {
    pub server_id: i32,
    pub base_url: String,
    /// NATS topic, e.g. "marketorders.ingest".
    pub topic: String,
    /// MarketUpload JSON ({"Orders":[...]}).
    pub natsmsg: String,
}

#[derive(serde::Deserialize)]
struct PowChallenge {
    key: String,
    wanted: String,
}

/// Reject challenges that would burn too much CPU.
///
/// `wanted` is ASCII expansion of the hex digest: 8 ASCII chars per hex char,
/// so entropy ≈ want_len/2 bits. Real server challenges run ~20 bits (~123 ms
/// in release at 3.2 Mhash/s). Reject above 28 bits (~1.5 min).
fn too_hard(want_len: usize) -> bool {
    want_len > 56
}

/// Solve the PoW: find a nonce hex so the first `wanted.len()` ASCII-expanded
/// bits of the SHA-256 digest match `wanted`. Bit-compatible with the
/// albiondata-client algorithm. Returns None if `too_hard`.
fn solve_pow(key: &str, wanted: &str) -> Option<String> {
    let want = wanted.as_bytes();
    let want_len = want.len();
    if want_len == 0 || too_hard(want_len) {
        return None;
    }
    let mut rng = rand::thread_rng();
    let mut nonce = [0u8; 16];
    // ~67M attempts covers challenges up to ~26 bits.
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

        // Expand each hex char to 8 bits (MSB first) and compare prefix with `wanted`.
        let mut ok = true;
        let mut idx = 0usize;
        'chars: for &c in hexdigest.as_bytes() {
            for j in (0..8).rev() {
                let bit = if (c >> j) & 1 == 1 { b'1' } else { b'0' };
                if bit != want[idx] {
                    ok = false;
                    break 'chars;
                }
                idx += 1;
                if idx >= want_len {
                    break 'chars;
                }
            }
        }
        if ok {
            return Some(randhex);
        }
    }
    None
}

/// UUID-v4-style identifier for AODP-side logging/dedup.
fn random_identifier() -> String {
    let mut b = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = hex::encode(b);
    format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    )
}

/// Upload a batch: fetch PoW, solve it, then POST the payload.
pub async fn upload(client: &reqwest::Client, batch: &AodpBatch) -> Result<()> {
    let pow: PowChallenge = client
        .get(format!("{}/pow", batch.base_url))
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;

    // CPU-bound PoW solving runs off the async executor.
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
        .send()
        .await?;
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

    // The real server sends 41-char `wanted`; old cutoff `> 40` silently broke the feed.
    #[test]
    fn dificuldade_real_do_servidor_nao_pode_ser_rejeitada() {
        for wanted_len in [41usize, 41, 41] {
            assert!(
                !too_hard(wanted_len),
                "want_len={wanted_len} is the real server challenge"
            );
        }
        assert!(!too_hard(48), "48 chars = 24 bits, still seconds");
        assert!(too_hard(80), "80 chars = 40 bits: abort");
    }

    #[test]
    fn test_solve_pow_short() {
        // 4-bit wanted: any hash starting with those bits works.
        let key = "abc123";
        let sol = solve_pow(key, "0").expect("must find prefix '0'");
        let mut h = Sha256::new();
        h.update(b"aod^");
        h.update(sol.as_bytes());
        h.update(b"^");
        h.update(key.as_bytes());
        let hd = hex::encode(h.finalize());
        let first_bit = (hd.as_bytes()[0] >> 7) & 1;
        assert_eq!(first_bit, 0);
    }

    #[test]
    fn test_identifier_format() {
        let id = random_identifier();
        assert_eq!(id.len(), 36);
        assert_eq!(id.as_bytes()[14], b'4'); // version 4
    }
}
