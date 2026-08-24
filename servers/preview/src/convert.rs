use std::path::{Path, PathBuf};

use crate::admit::Kind;
use crate::child::{self, ChildError, Limits};
use crate::config::Config;
use crate::refusal::Refusal;

const SOFFICE_MEMORY_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const SOFFICE_FILE_SIZE_BYTES: u64 = 512 * 1024 * 1024;
const SOFFICE_CPU_SECS: u64 = 120;
const FFMPEG_MEMORY_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const FFMPEG_FILE_SIZE_BYTES: u64 = 512 * 1024 * 1024;
const FFMPEG_CPU_SECS: u64 = 120;
const VIDEO_THUMBNAIL_FRAMES: u32 = 10;
const CSV_MAX_ROWS: usize = 200;
const CSV_MAX_COLS: usize = 50;

/// Convert `input` to a PDF in `workdir`; a `pdf` input passes through untouched. All parsing
/// happens inside one bounded soffice child with a per-request profile.
pub async fn to_pdf(
    kind: Kind,
    input: &Path,
    workdir: &Path,
    cfg: &Config,
) -> Result<PathBuf, Refusal> {
    let source = match kind {
        Kind::Pdf => return Ok(input.to_path_buf()),
        Kind::Md => {
            let text = tokio::fs::read_to_string(input)
                .await
                .map_err(|e| Refusal::UnsupportedType(format!("md read: {e}")))?;
            let html_path = workdir.join("input.html");
            tokio::fs::write(&html_path, markdown_to_html(&text))
                .await
                .map_err(|e| Refusal::RenderTimeout(format!("workdir write: {e}")))?;
            html_path
        }
        Kind::Csv => {
            let bytes = tokio::fs::read(input)
                .await
                .map_err(|e| Refusal::UnsupportedType(format!("csv read: {e}")))?;
            let html_path = workdir.join("input.html");
            tokio::fs::write(&html_path, csv_to_html(&bytes))
                .await
                .map_err(|e| Refusal::RenderTimeout(format!("workdir write: {e}")))?;
            html_path
        }
        _ => input.to_path_buf(),
    };
    let workdir_str = workdir
        .to_str()
        .ok_or_else(|| Refusal::UnsupportedType("workdir is not valid utf-8".into()))?;
    let profile = workdir.join("profile");
    let mut cmd = tokio::process::Command::new(&cfg.soffice_bin);
    cmd.arg("--headless")
        .arg("--norestore")
        .arg(format!(
            "-env:UserInstallation=file://{}",
            profile.display()
        ))
        .arg("--convert-to")
        .arg("pdf")
        .arg("--outdir")
        .arg(workdir)
        .arg(&source)
        .current_dir(workdir);
    let limits = Limits {
        deadline: cfg.convert_timeout,
        memory_bytes: Some(SOFFICE_MEMORY_BYTES),
        file_size_bytes: SOFFICE_FILE_SIZE_BYTES,
        cpu_secs: SOFFICE_CPU_SECS,
    };
    match child::run(cmd, &limits, &[("HOME", workdir_str)]).await {
        Ok(_) => {}
        Err(ChildError::Timeout) => {
            return Err(Refusal::RenderTimeout(format!(
                "convert exceeded {:?}",
                cfg.convert_timeout
            )))
        }
        Err(ChildError::Failed(out)) => {
            return Err(Refusal::UnsupportedType(format!(
                "soffice: {}",
                String::from_utf8_lossy(&out.stderr)
                    .chars()
                    .take(500)
                    .collect::<String>()
            )))
        }
        Err(ChildError::Spawn(e)) => {
            return Err(Refusal::RenderTimeout(format!("soffice spawn: {e}")))
        }
    }
    let pdf = source.with_extension("pdf");
    let produced = tokio::fs::metadata(&pdf)
        .await
        .map(|m| m.is_file())
        .unwrap_or(false);
    if !produced {
        return Err(Refusal::UnsupportedType("soffice produced no pdf".into()));
    }
    Ok(pdf)
}

/// Renders markdown into a standalone HTML document soffice can load as its conversion source.
pub fn markdown_to_html(text: &str) -> String {
    let mut body = String::new();
    pulldown_cmark::html::push_html(&mut body, pulldown_cmark::Parser::new(text));
    format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"></head><body style=\"font-family: sans-serif; margin: 48px;\">{body}</body></html>"
    )
}

/// Renders a CSV into a standalone HTML document holding a bordered `<table>` soffice can load —
/// a controlled parse and grid, not Calc's delimiter-guessing gridless print. The first row is the
/// header; rendering caps at the first `CSV_MAX_ROWS` rows and `CSV_MAX_COLS` columns, so a huge
/// CSV is silently but boundedly truncated. The input is bytes, not text: a CSV carries no declared
/// encoding and an export from Excel is Windows-1252, so a byte that is not UTF-8 is replaced in
/// its cell and the file still renders.
pub fn csv_to_html(bytes: &[u8]) -> String {
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .has_headers(false)
        .from_reader(bytes);
    let mut rows = String::new();
    for (r, record) in reader
        .byte_records()
        .flatten()
        .take(CSV_MAX_ROWS)
        .enumerate()
    {
        let cell = if r == 0 { "th" } else { "td" };
        rows.push_str("<tr>");
        for field in record.iter().take(CSV_MAX_COLS) {
            let field = String::from_utf8_lossy(field);
            rows.push_str(&format!("<{cell}>{}</{cell}>", escape_html(&field)));
        }
        rows.push_str("</tr>");
    }
    format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>@page{{margin: 6mm;}} body{{font-family: sans-serif; margin: 0;}} table{{border-collapse: collapse; width: 100%;}} td,th{{border: 1px solid #999; padding: 4px 8px;}} th{{background: #eee; text-align: left;}}</style></head><body><table border=\"1\" cellspacing=\"0\" width=\"100%\">{rows}</table></body></html>"
    )
}

fn escape_html(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

/// Extract one representative frame from a video into `workdir/out/page-01.png`, scaled to fit the
/// box while preserving aspect. The frame is the most representative of the first
/// `VIDEO_THUMBNAIL_FRAMES` frames. ffmpeg runs as one bounded child, its environment cleared but
/// for a workdir `HOME`.
pub async fn video_frame(
    input: &Path,
    workdir: &Path,
    max_w: u32,
    max_h: u32,
    cfg: &Config,
) -> Result<(), Refusal> {
    let out = workdir.join("out");
    tokio::fs::create_dir_all(&out)
        .await
        .map_err(|e| Refusal::RenderTimeout(format!("outdir: {e}")))?;
    let frame = out.join("page-01.png");
    let workdir_str = workdir
        .to_str()
        .ok_or_else(|| Refusal::UnsupportedType("workdir is not valid utf-8".into()))?;
    // `thumbnail` buffers its whole window of decoded frames at source resolution before the
    // scale runs, so the window sets the peak, not the output box: the filter's default 100
    // frames of 3840x2160 yuv420p is 1.2 GiB and the child dies on FFMPEG_MEMORY_BYTES with no
    // frame written.
    let vf = format!(
        "thumbnail=n={VIDEO_THUMBNAIL_FRAMES},scale='min({max_w},iw)':'min({max_h},ih)':force_original_aspect_ratio=decrease"
    );
    let mut cmd = tokio::process::Command::new(&cfg.ffmpeg_bin);
    cmd.arg("-nostdin")
        .arg("-y")
        .arg("-i")
        .arg(input)
        .arg("-frames:v")
        .arg("1")
        .arg("-vf")
        .arg(&vf)
        .arg("-f")
        .arg("image2")
        .arg(&frame)
        .current_dir(workdir);
    let limits = Limits {
        deadline: cfg.convert_timeout,
        memory_bytes: Some(FFMPEG_MEMORY_BYTES),
        file_size_bytes: FFMPEG_FILE_SIZE_BYTES,
        cpu_secs: FFMPEG_CPU_SECS,
    };
    match child::run(cmd, &limits, &[("HOME", workdir_str)]).await {
        Ok(_) => {}
        Err(ChildError::Timeout) => {
            return Err(Refusal::RenderTimeout(format!(
                "convert exceeded {:?}",
                cfg.convert_timeout
            )))
        }
        Err(ChildError::Failed(out)) => {
            return Err(Refusal::UnsupportedType(format!(
                "ffmpeg: {}",
                String::from_utf8_lossy(&out.stderr)
                    .chars()
                    .take(500)
                    .collect::<String>()
            )))
        }
        Err(ChildError::Spawn(e)) => {
            return Err(Refusal::RenderTimeout(format!("ffmpeg spawn: {e}")))
        }
    }
    let produced = tokio::fs::metadata(&frame)
        .await
        .map(|m| m.is_file())
        .unwrap_or(false);
    if !produced {
        return Err(Refusal::UnsupportedType("ffmpeg produced no frame".into()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn markdown_becomes_html_document() {
        let html = super::markdown_to_html("# Title\n\nbody **bold**\n");
        assert!(html.contains("<h1>Title</h1>"));
        assert!(html.contains("<strong>bold</strong>"));
        assert!(html.starts_with("<!doctype html>"));
    }

    #[test]
    fn csv_becomes_a_bordered_table_with_a_header_row() {
        let html = super::csv_to_html(b"name,city\nAlice,Boston\nBob,Reno\n");
        assert!(html.contains("<table"));
        assert!(html.contains("border-collapse: collapse"));
        assert!(html.contains("<th>name</th>"));
        assert!(html.contains("<th>city</th>"));
        assert!(html.contains("<td>Alice</td>"));
        assert!(html.contains("<td>Bob</td>"));
    }

    #[test]
    fn csv_cells_are_html_escaped() {
        let html = super::csv_to_html(b"expr\na<b & c\n");
        assert!(html.contains("<td>a&lt;b &amp; c</td>"));
        assert!(!html.contains("<td>a<b & c</td>"));
    }

    #[test]
    fn csv_quoted_field_keeps_its_embedded_comma_in_one_cell() {
        let html = super::csv_to_html(b"a,b\n\"x,y\",z\n");
        assert!(html.contains("<td>x,y</td>"));
        assert!(html.contains("<td>z</td>"));
    }

    #[test]
    fn csv_bytes_outside_utf8_still_render_their_rows() {
        let html = super::csv_to_html(b"name,city\nJos\xe9,Gen\xe8ve\n");
        assert!(html.contains("<th>name</th>"));
        assert!(html.contains("<td>Jos\u{fffd}</td>"));
        assert!(html.contains("<td>Gen\u{fffd}ve</td>"));
    }
}
