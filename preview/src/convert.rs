use std::path::{Path, PathBuf};

use crate::admit::Kind;
use crate::child::{self, ChildError, Limits};
use crate::config::Config;
use crate::refusal::Refusal;

const SOFFICE_MEMORY_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const SOFFICE_FILE_SIZE_BYTES: u64 = 512 * 1024 * 1024;
const SOFFICE_CPU_SECS: u64 = 120;

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
        memory_bytes: SOFFICE_MEMORY_BYTES,
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

#[cfg(test)]
mod tests {
    #[test]
    fn markdown_becomes_html_document() {
        let html = super::markdown_to_html("# Title\n\nbody **bold**\n");
        assert!(html.contains("<h1>Title</h1>"));
        assert!(html.contains("<strong>bold</strong>"));
        assert!(html.starts_with("<!doctype html>"));
    }
}
