use sha2::Digest;
use url::Host;

use crate::child::{self, ChildError, Limits};
use crate::config::Config;
use crate::refusal::Refusal;
use crate::render::Rendered;

const WORKER_FILE_SIZE_BYTES: u64 = 32 * 1024 * 1024;
const WORKER_CPU_SECS: u64 = 30;
const PNG_MEDIA_TYPE: &str = "image/png";

pub async fn capture(
    source_url: &str,
    width: u32,
    height: u32,
    cfg: &Config,
) -> Result<Rendered, Refusal> {
    let host = admitted_host(source_url, cfg)?;
    let work = tempfile::tempdir().map_err(|e| Refusal::RenderTimeout(format!("tmpdir: {e}")))?;
    let shot = work.path().join("site.png");
    let profile = work.path().join("profile");
    let mut cmd = tokio::process::Command::new(&cfg.python_bin);
    cmd.arg(&cfg.site_driver)
        .arg(&cfg.browser_bin)
        .arg(source_url)
        .arg(host)
        .arg(width.to_string())
        .arg(height.to_string())
        .arg(&shot)
        .arg(&profile)
        .current_dir(work.path());
    let limits = Limits {
        deadline: cfg.site_timeout,
        memory_bytes: None,
        file_size_bytes: WORKER_FILE_SIZE_BYTES,
        cpu_secs: WORKER_CPU_SECS,
    };
    match child::run(
        cmd,
        &limits,
        &[
            ("HOME", work.path().to_str().unwrap_or("/tmp")),
            ("PATH", "/usr/bin:/bin"),
        ],
    )
    .await
    {
        Ok(_) => {}
        Err(ChildError::Timeout) => {
            return Err(Refusal::RenderTimeout(format!(
                "site capture exceeded {:?}",
                cfg.site_timeout
            )))
        }
        Err(ChildError::Failed(output)) => {
            return Err(Refusal::UnsupportedType(format!(
                "site capture: {}",
                String::from_utf8_lossy(&output.stderr)
                    .chars()
                    .take(500)
                    .collect::<String>()
            )))
        }
        Err(ChildError::Spawn(error)) => {
            return Err(Refusal::RenderTimeout(format!(
                "site capture spawn: {error}"
            )))
        }
    }
    let bytes = tokio::fs::read(&shot)
        .await
        .map_err(|e| Refusal::RenderTimeout(format!("read site capture: {e}")))?;
    if bytes.len() as u64 > cfg.max_output_bytes {
        return Err(Refusal::TooLarge(format!(
            "output exceeds {} bytes",
            cfg.max_output_bytes
        )));
    }
    let (bytes, actual_width, actual_height, is_flat) = tokio::task::spawn_blocking(move || {
        let image = image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
            .map_err(|e| Refusal::UnsupportedType(format!("site capture is not png: {e}")))?
            .to_rgb8();
        let result = (bytes, image.width(), image.height(), flat(&image));
        Ok::<_, Refusal>(result)
    })
    .await
    .map_err(|e| Refusal::RenderTimeout(format!("inspect site capture: {e}")))??;
    if actual_width != width || actual_height != height {
        return Err(Refusal::RenderTimeout(format!(
            "site capture returned {}x{} for {width}x{height}",
            actual_width, actual_height
        )));
    }
    if is_flat {
        return Err(Refusal::UnsupportedType("site drew one flat colour".into()));
    }
    let sha256 = hex::encode(sha2::Sha256::digest(&bytes));
    Ok(Rendered {
        bytes,
        media_type: PNG_MEDIA_TYPE,
        width,
        height,
        page_count: 1,
        sha256,
    })
}

fn admitted_host(source_url: &str, cfg: &Config) -> Result<String, Refusal> {
    let parsed =
        url::Url::parse(source_url).map_err(|e| Refusal::FetchRefused(format!("site url: {e}")))?;
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(Refusal::FetchRefused("site url credentials refused".into()));
    }
    let host = match parsed
        .host()
        .ok_or_else(|| Refusal::FetchRefused("site url has no host".into()))?
    {
        Host::Domain(host) => host.to_string(),
        Host::Ipv4(address) if cfg.allow_local && address.is_loopback() => address.to_string(),
        Host::Ipv6(address) if cfg.allow_local && address.is_loopback() => address.to_string(),
        _ => return Err(Refusal::FetchRefused("site host refused".into())),
    };
    if cfg.allow_local && matches!(parsed.scheme(), "http" | "https") {
        return Ok(host);
    }
    if parsed.scheme() != "https" {
        return Err(Refusal::FetchRefused("site scheme refused".into()));
    }
    let suffix = cfg
        .site_host
        .as_deref()
        .ok_or_else(|| Refusal::FetchRefused("site capture is unconfigured".into()))?;
    if !host.ends_with(&format!(".{suffix}")) {
        return Err(Refusal::FetchRefused("site host refused".into()));
    }
    Ok(host)
}

fn flat(image: &image::RgbImage) -> bool {
    let Some(first) = image.pixels().next() else {
        return true;
    };
    image.pixels().all(|pixel| pixel == first)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Arc;

    fn config(allow_local: bool) -> Arc<Config> {
        let mut values = HashMap::from([
            ("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:0".into()),
            ("UFO_PREVIEW_TOKEN".into(), "token".into()),
            ("UFO_PREVIEW_PDFIUM_LIB".into(), "/missing".into()),
            ("UFO_PREVIEW_SITE_HOST".into(), "testing.ufo.ai".into()),
        ]);
        if allow_local {
            values.insert("UFO_PREVIEW_ALLOW_LOCAL".into(), "1".into());
        }
        Arc::new(Config::from_map(&values).unwrap())
    }

    #[test]
    fn admits_only_the_configured_site_origin() {
        let cfg = config(false);
        assert_eq!(
            admitted_host("https://abc.testing.ufo.ai/~t/token", &cfg).unwrap(),
            "abc.testing.ufo.ai"
        );
        assert!(admitted_host("https://testing.ufo.ai/", &cfg).is_err());
        assert!(admitted_host("https://abc.example.com/", &cfg).is_err());
        assert!(admitted_host("http://abc.testing.ufo.ai/", &cfg).is_err());
    }

    #[test]
    fn local_mode_admits_loopback_http() {
        assert_eq!(
            admitted_host("http://127.0.0.1:8000/", &config(true)).unwrap(),
            "127.0.0.1"
        );
    }
}
