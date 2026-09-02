use serde::{Deserialize, Serialize};
use std::backtrace::Backtrace;
use std::cell::Cell;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Instant;
use tracing_subscriber::fmt::writer::MakeWriter;
#[cfg(debug_assertions)]
use tracing_subscriber::fmt::writer::MakeWriterExt;

const MAX_MESSAGE_CHARS: usize = 4_000;
const MAX_DETAIL_CHARS: usize = 24_000;
const MAX_PENDING_BYTES: u64 = 256 * 1024;

static STARTED: OnceLock<Instant> = OnceLock::new();
static VERSION: OnceLock<String> = OnceLock::new();

thread_local! {
    static SUPPRESS_REPORT: Cell<bool> = const { Cell::new(false) };
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrashReport {
    pub kind: String,
    pub version: String,
    pub os: String,
    pub arch: String,
    pub created_at: String,
    pub uptime_ms: u64,
    pub process_id: u32,
    pub thread: String,
    pub message: String,
    pub location: String,
    pub backtrace: String,
    pub logs: String,
}

pub fn install_hook() {
    let _ = STARTED.set(Instant::now());
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let suppressed = SUPPRESS_REPORT.with(Cell::get);
        if !suppressed {
            let message = info
                .payload()
                .downcast_ref::<&str>()
                .map(|s| (*s).to_owned())
                .or_else(|| info.payload().downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "panic without message".into());
            let location = info
                .location()
                .map(|p| format!("{}:{}:{}", p.file(), p.line(), p.column()))
                .unwrap_or_default();
            let report = new_report(
                "rust_panic",
                message,
                location,
                Backtrace::force_capture().to_string(),
            );
            let _ = save(&report);
        }
        previous(info);
    }));
}

pub fn set_version(version: String) {
    let _ = VERSION.set(version);
}

pub fn init_logging() {
    #[cfg(debug_assertions)]
    let writer = (std::io::stderr as fn() -> std::io::Stderr).and(LogMakeWriter);
    #[cfg(not(debug_assertions))]
    let writer = LogMakeWriter;
    tracing_subscriber::fmt()
        .with_ansi(cfg!(debug_assertions))
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,tauri=info".into()),
        )
        .with_writer(writer)
        .init();
}

pub fn catch_unwind_silent<F, R>(f: F) -> std::thread::Result<R>
where
    F: FnOnce() -> R,
{
    struct Reset;
    impl Drop for Reset {
        fn drop(&mut self) {
            SUPPRESS_REPORT.with(|v| v.set(false));
        }
    }

    SUPPRESS_REPORT.with(|v| v.set(true));
    let _reset = Reset;
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(f))
}

pub fn save_frontend(message: String, stack: String) -> anyhow::Result<()> {
    save(&new_report("frontend", message, String::new(), stack))
}

pub async fn send_pending_once() -> anyhow::Result<bool> {
    let path = pending_path();
    let metadata = match std::fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(e) => return Err(e.into()),
    };
    if metadata.len() > MAX_PENDING_BYTES {
        quarantine(&path);
        anyhow::bail!("local crash report exceeds {} bytes", MAX_PENDING_BYTES);
    }

    let bytes = std::fs::read(&path)?;
    let report: CrashReport = match serde_json::from_slice(&bytes) {
        Ok(report) => report,
        Err(e) => {
            quarantine(&path);
            return Err(e.into());
        }
    };
    crate::api::ApiClient::new(crate::config::API_BASE_URL)
        .report_crash(&report)
        .await?;

    // A second crash may have replaced the file while this request was in flight.
    if std::fs::read(&path).ok().as_deref() == Some(bytes.as_slice()) {
        std::fs::remove_file(path)?;
    }
    Ok(true)
}

fn new_report(kind: &str, message: String, location: String, backtrace: String) -> CrashReport {
    CrashReport {
        kind: kind.into(),
        version: VERSION
            .get()
            .cloned()
            .unwrap_or_else(|| env!("CARGO_PKG_VERSION").into()),
        os: std::env::consts::OS.into(),
        arch: std::env::consts::ARCH.into(),
        created_at: chrono::Utc::now().to_rfc3339(),
        uptime_ms: STARTED
            .get()
            .map(Instant::elapsed)
            .unwrap_or_default()
            .as_millis() as u64,
        process_id: std::process::id(),
        thread: std::thread::current()
            .name()
            .unwrap_or("unnamed")
            .to_string(),
        message: clip(message, MAX_MESSAGE_CHARS),
        location: clip(location, 512),
        backtrace: clip(backtrace, MAX_DETAIL_CHARS),
        logs: collect_logs(),
    }
}

fn save(report: &CrashReport) -> anyhow::Result<()> {
    let path = pending_path();
    let bytes = serde_json::to_vec(report)?;
    crate::config::atomic_write(&path, &bytes)?;
    Ok(())
}

fn collect_logs() -> String {
    let app_log = tail_file(&log_path(), 12_000);
    let sniffer_log = dirs::document_dir()
        .map(|d| {
            tail_file(
                &d.join("ziggs-companion").join("companion-debug.log"),
                12_000,
            )
        })
        .unwrap_or_default();
    clip(
        format!("=== companion.log ===\n{app_log}\n=== companion-debug.log ===\n{sniffer_log}"),
        MAX_DETAIL_CHARS,
    )
}

fn tail_file(path: &std::path::Path, max_bytes: u64) -> String {
    let Ok(mut file) = std::fs::File::open(path) else {
        return String::new();
    };
    let Ok(len) = file.metadata().map(|m| m.len()) else {
        return String::new();
    };
    if file
        .seek(SeekFrom::Start(len.saturating_sub(max_bytes)))
        .is_err()
    {
        return String::new();
    }
    let mut bytes = Vec::new();
    if file.read_to_end(&mut bytes).is_err() {
        return String::new();
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn clip(value: String, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        value
    } else {
        value.chars().take(max_chars).collect()
    }
}

fn app_dir() -> PathBuf {
    let dir = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("ziggs-companion");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn pending_path() -> PathBuf {
    app_dir().join("pending-crash-report.json")
}

fn log_path() -> PathBuf {
    app_dir().join("companion.log")
}

fn quarantine(path: &std::path::Path) {
    let invalid = app_dir().join("invalid-crash-report.json");
    let _ = std::fs::remove_file(&invalid);
    let _ = std::fs::rename(path, invalid);
}

struct LogMakeWriter;

impl<'a> MakeWriter<'a> for LogMakeWriter {
    type Writer = Box<dyn Write>;

    fn make_writer(&'a self) -> Self::Writer {
        match OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_path())
        {
            Ok(file) => Box::new(file),
            Err(_) => Box::new(std::io::sink()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_file_keeps_only_the_tail() {
        let path =
            std::env::temp_dir().join(format!("ziggs-crash-tail-{}.txt", std::process::id()));
        std::fs::write(&path, b"0123456789").unwrap();
        assert_eq!(tail_file(&path, 4), "6789");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn frontend_report_clamps_fields_controlled_by_the_webview() {
        let report = new_report(
            "frontend",
            "x".repeat(5_000),
            String::new(),
            "y".repeat(30_000),
        );
        assert_eq!(report.message.len(), MAX_MESSAGE_CHARS);
        assert_eq!(report.backtrace.len(), MAX_DETAIL_CHARS);
    }
}
