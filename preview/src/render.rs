use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::Deserialize;
use sha2::Digest;

use crate::admit::Kind;
use crate::child::{self, ChildError, Limits};
use crate::config::Config;
use crate::convert;
use crate::fetch;
use crate::refusal::Refusal;

const WORKER_MEMORY_BYTES: u64 = 1024 * 1024 * 1024;
const WORKER_FILE_SIZE_BYTES: u64 = 256 * 1024 * 1024;
const WORKER_CPU_SECS: u64 = 30;
const PNG_MEDIA_TYPE: &str = "image/png";
const ZIP_MEDIA_TYPE: &str = "application/zip";

#[derive(Deserialize)]
pub struct RenderRequest {
    pub kind: String,
    #[serde(default)]
    pub source_url: Option<String>,
    pub max_width: u32,
    pub max_height: u32,
    #[serde(default = "default_pages")]
    pub pages: u32,
    pub sink: SinkSpec,
}

fn default_pages() -> u32 {
    1
}

#[derive(Deserialize)]
#[serde(untagged)]
pub enum SinkSpec {
    Inline { inline: bool },
    PutUrl { put_url: String },
}

pub struct Rendered {
    pub bytes: Vec<u8>,
    pub media_type: &'static str,
    pub width: u32,
    pub height: u32,
    pub page_count: u32,
    pub sha256: String,
}

#[derive(serde::Deserialize)]
struct WorkerMeta {
    page_count: u32,
    pages: Vec<WorkerPage>,
}

#[derive(serde::Deserialize)]
struct WorkerPage {
    width: u32,
    height: u32,
}

/// The render workflow: admit → obtain bytes → convert → rasterize → package. One instance
/// serves the process; per-request state lives on the stack and in a fresh tmpdir.
#[derive(Clone)]
pub struct Render {
    cfg: Arc<Config>,
    permits: Arc<tokio::sync::Semaphore>,
}

impl Render {
    pub fn new(cfg: Arc<Config>) -> Self {
        let permits = Arc::new(tokio::sync::Semaphore::new(cfg.concurrency));
        Self { cfg, permits }
    }

    /// The sole admission gate: the route acquires an owned permit before touching any request
    /// bytes, and holds it across parsing, rendering, and delivery.
    pub fn semaphore(&self) -> &Arc<tokio::sync::Semaphore> {
        &self.permits
    }

    pub async fn handle(
        &self,
        req: &RenderRequest,
        file: Option<Vec<u8>>,
    ) -> Result<Rendered, Refusal> {
        let kind = Kind::parse(&req.kind)
            .ok_or_else(|| Refusal::UnsupportedType(format!("kind {}", req.kind)))?;
        let pages = req.pages.clamp(1, self.cfg.max_pages);
        let max_w = req.max_width.clamp(16, self.cfg.max_box_px);
        let max_h = req.max_height.clamp(16, self.cfg.max_box_px);
        let bytes = self.obtain(req, file).await?;
        self.admit(kind, &bytes)?;
        let work =
            tempfile::tempdir().map_err(|e| Refusal::RenderTimeout(format!("tmpdir: {e}")))?;
        let input = work.path().join(format!("input.{}", kind.extension()));
        tokio::fs::write(&input, &bytes)
            .await
            .map_err(|e| Refusal::RenderTimeout(format!("spool: {e}")))?;
        drop(bytes);
        if kind.is_video() {
            convert::video_frame(&input, work.path(), max_w, max_h, &self.cfg).await?;
            let (width, height) = png_size(&work.path().join("out").join("page-01.png")).await?;
            let meta = WorkerMeta {
                page_count: 1,
                pages: vec![WorkerPage { width, height }],
            };
            return self.package(work.path(), 1, meta).await;
        }
        let pdf = convert::to_pdf(kind, &input, work.path(), &self.cfg).await?;
        let meta = self
            .rasterize(
                &pdf,
                work.path(),
                max_w,
                max_h,
                pages,
                kind.is_spreadsheet(),
            )
            .await?;
        self.package(work.path(), pages, meta).await
    }

    async fn obtain(&self, req: &RenderRequest, file: Option<Vec<u8>>) -> Result<Vec<u8>, Refusal> {
        match (file, &req.source_url) {
            (Some(bytes), None) => {
                if bytes.len() as u64 > self.cfg.max_input_bytes {
                    return Err(Refusal::TooLarge(format!(
                        "input exceeds {} bytes",
                        self.cfg.max_input_bytes
                    )));
                }
                Ok(bytes)
            }
            (None, Some(url)) => {
                fetch::fetch_source(
                    url,
                    self.cfg.max_input_bytes,
                    self.cfg.allow_local,
                    self.cfg.fetch_timeout,
                )
                .await
            }
            (Some(_), Some(_)) => Err(Refusal::UnsupportedType(
                "both file and source_url given".into(),
            )),
            (None, None) => Err(Refusal::UnsupportedType(
                "no file part and no source_url".into(),
            )),
        }
    }

    fn admit(&self, kind: Kind, bytes: &[u8]) -> Result<(), Refusal> {
        if bytes.is_empty() {
            return Err(Refusal::UnsupportedType("empty input".into()));
        }
        if !kind.check_magic(bytes) {
            return Err(Refusal::KindMismatch(format!(
                "bytes are not {}",
                kind.extension()
            )));
        }
        Ok(())
    }

    async fn rasterize(
        &self,
        pdf: &Path,
        workdir: &Path,
        max_w: u32,
        max_h: u32,
        pages: u32,
        crop: bool,
    ) -> Result<WorkerMeta, Refusal> {
        let out = workdir.join("out");
        tokio::fs::create_dir(&out)
            .await
            .map_err(|e| Refusal::RenderTimeout(format!("outdir: {e}")))?;
        let worker = worker_exe().await?;
        let mut cmd = tokio::process::Command::new(worker);
        cmd.arg(&self.cfg.pdfium_lib)
            .arg(pdf)
            .arg(&out)
            .arg(max_w.to_string())
            .arg(max_h.to_string())
            .arg(pages.to_string())
            .arg(if crop { "1" } else { "0" })
            .current_dir(workdir);
        let limits = Limits {
            deadline: self.cfg.raster_timeout,
            memory_bytes: WORKER_MEMORY_BYTES,
            file_size_bytes: WORKER_FILE_SIZE_BYTES,
            cpu_secs: WORKER_CPU_SECS,
        };
        let output = match child::run(cmd, &limits, &[]).await {
            Ok(o) => o,
            Err(ChildError::Timeout) => {
                return Err(Refusal::RenderTimeout(format!(
                    "raster exceeded {:?}",
                    self.cfg.raster_timeout
                )))
            }
            Err(ChildError::Failed(o)) => {
                return Err(Refusal::UnsupportedType(format!(
                    "raster: {}",
                    String::from_utf8_lossy(&o.stderr)
                        .chars()
                        .take(500)
                        .collect::<String>()
                )))
            }
            Err(ChildError::Spawn(e)) => {
                return Err(Refusal::RenderTimeout(format!("worker spawn: {e}")))
            }
        };
        serde_json::from_slice(&output.stdout)
            .map_err(|e| Refusal::RenderTimeout(format!("worker meta: {e}")))
    }

    async fn package(
        &self,
        workdir: &Path,
        requested_pages: u32,
        meta: WorkerMeta,
    ) -> Result<Rendered, Refusal> {
        let out = workdir.join("out");
        let rendered = meta.pages.len() as u32;
        if rendered == 0 {
            return Err(Refusal::UnsupportedType("no pages rendered".into()));
        }
        let mut page_bytes: u64 = 0;
        for i in 1..=rendered {
            let stat = tokio::fs::metadata(out.join(format!("page-{i:02}.png")))
                .await
                .map_err(|e| Refusal::RenderTimeout(format!("stat page {i}: {e}")))?;
            page_bytes += stat.len();
        }
        if page_bytes > self.cfg.max_output_bytes {
            return Err(Refusal::TooLarge(format!(
                "output exceeds {} bytes",
                self.cfg.max_output_bytes
            )));
        }
        let bytes = if requested_pages == 1 {
            tokio::fs::read(out.join("page-01.png"))
                .await
                .map_err(|e| Refusal::RenderTimeout(format!("read png: {e}")))?
        } else {
            let out = out.clone();
            tokio::task::spawn_blocking(move || zip_pages(&out, rendered))
                .await
                .map_err(|e| Refusal::RenderTimeout(format!("zip: {e}")))?
                .map_err(|e| Refusal::RenderTimeout(format!("zip: {e}")))?
        };
        if bytes.len() as u64 > self.cfg.max_output_bytes {
            return Err(Refusal::TooLarge(format!(
                "output exceeds {} bytes",
                self.cfg.max_output_bytes
            )));
        }
        let media_type = if requested_pages == 1 {
            PNG_MEDIA_TYPE
        } else {
            ZIP_MEDIA_TYPE
        };
        let sha256 = hex::encode(sha2::Sha256::digest(&bytes));
        Ok(Rendered {
            bytes,
            media_type,
            width: meta.pages.first().map(|p| p.width).unwrap_or(0),
            height: meta.pages.first().map(|p| p.height).unwrap_or(0),
            page_count: meta.page_count,
            sha256,
        })
    }
}

// Locates the `preview-worker` binary beside the running process. A deployed image places both
// binaries in one directory, but `cargo test` builds the test binary one level deeper (in
// `target/debug/deps/`) than the `[[bin]]` targets, so the parent directory is checked too.
async fn worker_exe() -> Result<PathBuf, Refusal> {
    let exe =
        std::env::current_exe().map_err(|e| Refusal::RenderTimeout(format!("current_exe: {e}")))?;
    let sibling = exe.with_file_name("preview-worker");
    if is_file(&sibling).await {
        return Ok(sibling);
    }
    if let Some(uncle) = exe.parent().and_then(Path::parent) {
        let uncle = uncle.join("preview-worker");
        if is_file(&uncle).await {
            return Ok(uncle);
        }
    }
    Err(Refusal::RenderTimeout(
        "preview-worker binary not found beside current_exe".into(),
    ))
}

async fn is_file(path: &Path) -> bool {
    tokio::fs::metadata(path)
        .await
        .map(|m| m.is_file())
        .unwrap_or(false)
}

const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";
const PNG_IHDR_END: usize = 24;

/// Reads a PNG's pixel dimensions straight from its IHDR (width and height are the two big-endian
/// u32s at bytes 16..24), without decoding the image.
async fn png_size(path: &Path) -> Result<(u32, u32), Refusal> {
    let head = tokio::fs::read(path)
        .await
        .map_err(|e| Refusal::RenderTimeout(format!("read frame: {e}")))?;
    if head.len() < PNG_IHDR_END || !head.starts_with(PNG_SIGNATURE) {
        return Err(Refusal::UnsupportedType("frame is not a png".into()));
    }
    let w = u32::from_be_bytes([head[16], head[17], head[18], head[19]]);
    let h = u32::from_be_bytes([head[20], head[21], head[22], head[23]]);
    Ok((w, h))
}

fn zip_pages(dir: &Path, count: u32) -> std::io::Result<Vec<u8>> {
    let mut zip = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for i in 1..=count {
        let name = format!("page-{i:02}.png");
        zip.start_file(&name, options)?;
        std::io::copy(&mut std::fs::File::open(dir.join(&name))?, &mut zip)?;
    }
    Ok(zip.finish()?.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn test_config() -> Arc<Config> {
        let mut m = HashMap::new();
        m.insert("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:0".into());
        m.insert("UFO_PREVIEW_TOKEN".into(), "t".into());
        m.insert("UFO_PREVIEW_PDFIUM_LIB".into(), "/nonexistent".into());
        Arc::new(Config::from_map(&m).unwrap())
    }

    fn request(source_url: Option<String>) -> RenderRequest {
        RenderRequest {
            kind: "pdf".into(),
            source_url,
            max_width: 800,
            max_height: 800,
            pages: 1,
            sink: SinkSpec::Inline { inline: true },
        }
    }

    #[tokio::test]
    async fn obtain_refuses_both_file_and_source_url() {
        let render = Render::new(test_config());
        let req = request(Some("https://example.com/doc.pdf".into()));
        let err = render
            .obtain(&req, Some(b"data".to_vec()))
            .await
            .unwrap_err();
        assert!(matches!(err, Refusal::UnsupportedType(_)));
    }

    #[tokio::test]
    async fn obtain_refuses_neither_file_nor_source_url() {
        let render = Render::new(test_config());
        let req = request(None);
        let err = render.obtain(&req, None).await.unwrap_err();
        assert!(matches!(err, Refusal::UnsupportedType(_)));
    }

    #[test]
    fn admit_refuses_empty_input() {
        let render = Render::new(test_config());
        let err = render.admit(Kind::Pdf, &[]).unwrap_err();
        assert!(matches!(err, Refusal::UnsupportedType(_)));
    }

    #[tokio::test]
    async fn worker_exe_resolves_via_parent_dir_fallback_under_cargo_test() {
        let path = worker_exe().await.unwrap();
        assert_eq!(path.file_name().unwrap(), "preview-worker");
        assert!(path.is_file(), "{path:?} must exist");
    }
}
