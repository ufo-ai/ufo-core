use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::Duration;

const DEFAULT_MAX_INPUT_MB: u64 = 100;
const DEFAULT_MAX_OUTPUT_BYTES: u64 = 20 * 1024 * 1024;
const DEFAULT_MAX_BOX_PX: u32 = 4096;
const DEFAULT_MAX_PAGES: u32 = 20;
const DEFAULT_CONVERT_TIMEOUT_SECS: u64 = 120;
const DEFAULT_RASTER_TIMEOUT_SECS: u64 = 30;
const DEFAULT_FETCH_TIMEOUT_SECS: u64 = 60;
const DEFAULT_SITE_TIMEOUT_SECS: u64 = 30;
const DEFAULT_CONCURRENCY_CEILING: usize = 4;
const DEFAULT_REQUEST_TIMEOUT_SECS: u64 = 300;

/// Service configuration, read once from the environment at boot. Fail loud on anything missing
/// or unparseable — the service is launched by the deploy, not a human.
#[derive(Clone, Debug)]
pub struct Config {
    pub listen: SocketAddr,
    pub token: String,
    pub pdfium_lib: String,
    pub soffice_bin: String,
    pub ffmpeg_bin: String,
    pub browser_bin: String,
    pub python_bin: String,
    pub site_driver: String,
    pub site_host: Option<String>,
    pub max_input_bytes: u64,
    pub max_output_bytes: u64,
    pub max_box_px: u32,
    pub max_pages: u32,
    pub convert_timeout: Duration,
    pub raster_timeout: Duration,
    pub fetch_timeout: Duration,
    pub site_timeout: Duration,
    /// The whole `/render` request, permit hold included — bounds a bearer-less `put_url` caller
    /// trickling a body from pinning a permit forever. Must exceed every phase it wraps (fetch +
    /// convert + raster + PUT headroom), so the per-phase timeouts fire first.
    pub request_timeout: Duration,
    pub concurrency: usize,
    /// Admits http and loopback/private targets — the dev rig and tests only.
    pub allow_local: bool,
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        Self::from_map(&std::env::vars().collect())
    }

    pub fn from_map(m: &HashMap<String, String>) -> Result<Self, String> {
        Ok(Self {
            listen: req(m, "UFO_PREVIEW_LISTEN")?
                .parse()
                .map_err(|e| format!("UFO_PREVIEW_LISTEN: {e}"))?,
            token: req(m, "UFO_PREVIEW_TOKEN")?,
            pdfium_lib: req(m, "UFO_PREVIEW_PDFIUM_LIB")?,
            soffice_bin: opt(m, "UFO_PREVIEW_SOFFICE_BIN").unwrap_or_else(|| "soffice".into()),
            ffmpeg_bin: opt(m, "UFO_PREVIEW_FFMPEG_BIN").unwrap_or_else(|| "ffmpeg".into()),
            browser_bin: opt(m, "UFO_PREVIEW_BROWSER_BIN")
                .unwrap_or_else(|| "/usr/bin/chromium".into()),
            python_bin: opt(m, "UFO_PREVIEW_PYTHON_BIN")
                .unwrap_or_else(|| "/usr/bin/python3".into()),
            site_driver: opt(m, "UFO_PREVIEW_SITE_DRIVER")
                .unwrap_or_else(|| "/usr/local/libexec/ufo-site-preview.py".into()),
            site_host: opt(m, "UFO_PREVIEW_SITE_HOST"),
            max_input_bytes: parse_or(m, "UFO_PREVIEW_MAX_INPUT_MB", DEFAULT_MAX_INPUT_MB)?
                .saturating_mul(1024 * 1024),
            max_output_bytes: parse_or(
                m,
                "UFO_PREVIEW_MAX_OUTPUT_BYTES",
                DEFAULT_MAX_OUTPUT_BYTES,
            )?,
            max_box_px: parse_or(m, "UFO_PREVIEW_MAX_BOX_PX", DEFAULT_MAX_BOX_PX)?,
            max_pages: parse_or(m, "UFO_PREVIEW_MAX_PAGES", DEFAULT_MAX_PAGES)?,
            convert_timeout: Duration::from_secs(parse_or(
                m,
                "UFO_PREVIEW_CONVERT_TIMEOUT_SECS",
                DEFAULT_CONVERT_TIMEOUT_SECS,
            )?),
            raster_timeout: Duration::from_secs(parse_or(
                m,
                "UFO_PREVIEW_RASTER_TIMEOUT_SECS",
                DEFAULT_RASTER_TIMEOUT_SECS,
            )?),
            fetch_timeout: Duration::from_secs(parse_or(
                m,
                "UFO_PREVIEW_FETCH_TIMEOUT_SECS",
                DEFAULT_FETCH_TIMEOUT_SECS,
            )?),
            site_timeout: Duration::from_secs(parse_or(
                m,
                "UFO_PREVIEW_SITE_TIMEOUT_SECS",
                DEFAULT_SITE_TIMEOUT_SECS,
            )?),
            request_timeout: Duration::from_secs(parse_or(
                m,
                "UFO_PREVIEW_REQUEST_TIMEOUT_SECS",
                DEFAULT_REQUEST_TIMEOUT_SECS,
            )?),
            concurrency: match opt(m, "UFO_PREVIEW_CONCURRENCY") {
                Some(v) => v
                    .parse()
                    .map_err(|e| format!("UFO_PREVIEW_CONCURRENCY: {e}"))?,
                // LibreOffice's UNO bridge aborts under high parallel soffice launches.
                None => std::thread::available_parallelism()
                    .map(|n| n.get().min(DEFAULT_CONCURRENCY_CEILING))
                    .unwrap_or(2),
            },
            allow_local: opt(m, "UFO_PREVIEW_ALLOW_LOCAL").as_deref() == Some("1"),
        })
    }
}

fn req(m: &HashMap<String, String>, key: &str) -> Result<String, String> {
    match m.get(key) {
        Some(v) if !v.is_empty() => Ok(v.clone()),
        _ => Err(format!("{key} is required")),
    }
}

fn opt(m: &HashMap<String, String>, key: &str) -> Option<String> {
    m.get(key).cloned()
}

fn parse_or<T: std::str::FromStr>(
    m: &HashMap<String, String>,
    key: &str,
    default: T,
) -> Result<T, String>
where
    T::Err: std::fmt::Display,
{
    match m.get(key) {
        Some(v) => v.parse().map_err(|e| format!("{key}: {e}")),
        None => Ok(default),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_required_env_fails() {
        let err = Config::from_map(&std::collections::HashMap::new()).unwrap_err();
        assert!(err.contains("UFO_PREVIEW_LISTEN"));
    }

    #[test]
    fn empty_required_env_fails() {
        let mut m = std::collections::HashMap::new();
        m.insert("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:8930".into());
        m.insert("UFO_PREVIEW_TOKEN".into(), "".into());
        m.insert(
            "UFO_PREVIEW_PDFIUM_LIB".into(),
            "/usr/lib/libpdfium.so".into(),
        );
        let err = Config::from_map(&m).unwrap_err();
        assert!(err.contains("UFO_PREVIEW_TOKEN"));
    }

    #[test]
    fn defaults_fill_optionals() {
        let mut m = std::collections::HashMap::new();
        m.insert("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:8930".into());
        m.insert("UFO_PREVIEW_TOKEN".into(), "t".into());
        m.insert(
            "UFO_PREVIEW_PDFIUM_LIB".into(),
            "/usr/lib/libpdfium.so".into(),
        );
        let c = Config::from_map(&m).unwrap();
        assert_eq!(c.max_output_bytes, 20 * 1024 * 1024);
        assert_eq!(c.max_pages, 20);
        assert_eq!(c.soffice_bin, "soffice");
        assert_eq!(c.ffmpeg_bin, "ffmpeg");
        assert_eq!(c.browser_bin, "/usr/bin/chromium");
        assert_eq!(c.python_bin, "/usr/bin/python3");
        assert_eq!(c.site_driver, "/usr/local/libexec/ufo-site-preview.py");
        assert_eq!(c.site_host, None);
        assert!(!c.allow_local);
        assert!(
            c.concurrency <= 4,
            "default concurrency must not exceed 4: {}",
            c.concurrency
        );
        assert_eq!(c.request_timeout, Duration::from_secs(300));
    }
}
