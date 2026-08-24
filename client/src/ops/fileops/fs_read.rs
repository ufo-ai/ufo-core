#[cfg(unix)]
use std::path::Path;

use base64::Engine as _;
use serde_json::json;

#[cfg(unix)]
use crate::guard;
use crate::ops::fileops::text::{
    binary_extension, decode_lossy, exists, number_lines, number_param, read_bytes, refused,
    str_param, suffix, OpError, OpResult, BINARY_SNIFF_BYTES,
};
#[cfg(unix)]
use crate::ops::fileops::text::{failed, guarded, required};

const READ_DEFAULT_LIMIT: f64 = 2000.0;
pub const IMAGE_MAX_BYTES: usize = 5 * 1024 * 1024;
const PDF_DEFAULT_MAX_PAGES: i64 = 20;
const PDF_RENDER_DPI: u32 = 100;
const PDF_QUALITY_REMINDER: &str = "CRITICAL: Before sharing, carefully examine each page for \
     quality issues (e.g. overlapping text, hidden/cut-off text, text squished together). These \
     are common and must be fixed.";

pub const IMAGE_MEDIA_TYPES: &[(&str, &str)] = &[
    (".png", "image/png"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".gif", "image/gif"),
    (".webp", "image/webp"),
];

const IMAGE_MAGIC: &[(&str, &[u8])] = &[
    (
        "image/png",
        &[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
    ),
    ("image/jpeg", &[0xff, 0xd8, 0xff]),
    ("image/gif", b"GIF87a"),
    ("image/gif", b"GIF89a"),
];

fn python_bytes_repr(bytes: &[u8]) -> String {
    let single = bytes.contains(&0x27);
    let double = bytes.contains(&0x22);
    let quote = if single && !double { '"' } else { '\'' };
    let mut parts = String::new();
    for &byte in bytes {
        if byte == 0x5c {
            parts.push_str("\\\\");
        } else if byte == quote as u8 {
            parts.push('\\');
            parts.push(quote);
        } else if byte == 0x09 {
            parts.push_str("\\t");
        } else if byte == 0x0a {
            parts.push_str("\\n");
        } else if byte == 0x0d {
            parts.push_str("\\r");
        } else if (0x20..0x7f).contains(&byte) {
            parts.push(byte as char);
        } else {
            parts.push_str(&format!("\\x{byte:02x}"));
        }
    }
    format!("b{quote}{parts}{quote}")
}

fn image_media_type(bytes: &[u8]) -> Option<&'static str> {
    for (media_type, magic) in IMAGE_MAGIC {
        if bytes.starts_with(magic) {
            return Some(media_type);
        }
    }
    if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" {
        return Some("image/webp");
    }
    None
}

fn image_cap(path: &str, size: usize) -> Result<(), OpError> {
    if size > IMAGE_MAX_BYTES {
        return Err(refused(format!(
            "{path} is {size} bytes; over the {IMAGE_MAX_BYTES}-byte image read cap. Resize it \
             (e.g. with a bash tool) before reading."
        )));
    }
    Ok(())
}

fn image_result(path: &str, bytes: &[u8]) -> OpResult {
    image_cap(path, bytes.len())?;
    let Some(media_type) = image_media_type(bytes) else {
        return Err(refused(format!(
            "{path} has an image extension but its bytes are not png/jpeg/gif/webp (they start \
             with {}). Re-export it as a real image, then read it again.",
            python_bytes_repr(&bytes[..bytes.len().min(8)])
        )));
    };
    Ok(json!({
        "path": path,
        "type": "image",
        "media_type": media_type,
        "data": base64::engine::general_purpose::STANDARD.encode(bytes),
        "size_bytes": bytes.len(),
    }))
}

fn empty_result(path: &str, offset: i64) -> serde_json::Value {
    json!({
        "path": path,
        "content": "",
        "total_lines": 0,
        "start_line": offset,
        "lines_returned": 0,
        "remaining_lines": 0,
        "next_offset": null,
        "truncated": false,
        "is_empty": true,
    })
}

fn text_result(path: &str, bytes: &[u8], offset: i64, limit: i64) -> serde_json::Value {
    let text = decode_lossy(bytes);
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    let total = lines.len();
    let start = ((offset - 1) as usize).min(total);
    let end = if limit <= 0 {
        start
    } else {
        start.saturating_add(limit as usize).min(total)
    };
    let window = &lines[start..end];
    let returned = window.len();
    let consumed = (offset - 1) as usize + returned;
    let remaining = total.saturating_sub(consumed);
    let width = total.to_string().len();
    json!({
        "path": path,
        "content": number_lines(window, offset as usize, width).join("\n"),
        "total_lines": total,
        "start_line": offset,
        "lines_returned": returned,
        "remaining_lines": remaining,
        "next_offset": if remaining > 0 { json!(consumed + 1) } else { json!(null) },
        "truncated": remaining > 0,
        "is_empty": false,
    })
}

/// Whether an executable of this name is on `PATH` — `shutil.which`, for the converters a document
/// read shells out to. The sandbox image bakes poppler and LibreOffice; a client machine may have
/// neither, and the read degrades instead of failing.
#[cfg(unix)]
fn on_path(program: &str) -> bool {
    let Some(paths) = std::env::var_os("PATH") else {
        return false;
    };
    std::env::split_paths(&paths).any(|directory| {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(directory.join(program))
            .is_ok_and(|found| found.is_file() && found.permissions().mode() & 0o111 != 0)
    })
}

/// A directory of its own for one render's output, removed when this value drops: `pdftoppm` writes
/// one file per page and `soffice` writes beside its input, and neither may land in the workspace.
#[cfg(unix)]
struct Scratch(std::path::PathBuf);

#[cfg(unix)]
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[cfg(unix)]
impl Scratch {
    fn new() -> Result<Scratch, OpError> {
        use std::os::unix::fs::DirBuilderExt;
        let mut bytes = [0u8; 8];
        getrandom::fill(&mut bytes).expect("os randomness is available");
        let tag: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        let path = std::env::temp_dir().join(format!("ufo-fs-render-{tag}"));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .map_err(|error| failed(format!("{}: {error}", path.display())))?;
        Ok(Scratch(path))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

#[cfg(unix)]
fn ran(program: &str, args: &[&std::ffi::OsStr]) -> Result<std::process::Output, OpError> {
    std::process::Command::new(program)
        .args(args)
        .output()
        .map_err(|error| failed(format!("{program}: {error}")))
}

#[cfg(unix)]
fn said(output: &std::process::Output, fallback: String) -> OpError {
    let complaint = String::from_utf8_lossy(&output.stderr).trim().to_string();
    refused(if complaint.is_empty() {
        fallback
    } else {
        complaint
    })
}

#[cfg(unix)]
fn pdf_total_pages(path: &Path) -> Result<i64, OpError> {
    let output = ran("pdfinfo", &[path.as_os_str()])?;
    if !output.status.success() {
        return Err(said(
            &output,
            format!("pdfinfo exited {}", exit_code(&output.status)),
        ));
    }
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        if let Some(count) = line.strip_prefix("Pages:") {
            if let Ok(total) = count.trim().parse::<i64>() {
                return Ok(total);
            }
        }
    }
    Err(refused(format!(
        "pdfinfo gave no page count for {}",
        path.display()
    )))
}

#[cfg(unix)]
fn pdf_text(path: &Path, start: i64, end: i64) -> String {
    if !on_path("pdftotext") {
        return String::new();
    }
    let (first, last) = (start.to_string(), end.to_string());
    let args = [
        "-f".as_ref(),
        first.as_ref(),
        "-l".as_ref(),
        last.as_ref(),
        path.as_os_str(),
        "-".as_ref(),
    ];
    match ran("pdftotext", &args) {
        Ok(output) if output.status.success() => decode_lossy(&output.stdout),
        _ => String::new(),
    }
}

#[cfg(unix)]
fn pdf_pages(path: &Path, start: i64, end: i64) -> Result<Vec<serde_json::Value>, OpError> {
    let scratch = Scratch::new()?;
    let prefix = scratch.path().join("page");
    let dpi = PDF_RENDER_DPI.to_string();
    let (first, last) = (start.to_string(), end.to_string());
    let args = [
        "-png".as_ref(),
        "-r".as_ref(),
        dpi.as_ref(),
        "-f".as_ref(),
        first.as_ref(),
        "-l".as_ref(),
        last.as_ref(),
        path.as_os_str(),
        prefix.as_os_str(),
    ];
    let output = ran("pdftoppm", &args)?;
    if !output.status.success() {
        return Err(said(
            &output,
            format!("pdftoppm exited {}", exit_code(&output.status)),
        ));
    }
    let mut rendered: Vec<std::path::PathBuf> = std::fs::read_dir(scratch.path())
        .map_err(|error| failed(format!("{}: {error}", scratch.path().display())))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|entry| {
            let name = entry.file_name().unwrap_or_default().to_string_lossy();
            name.starts_with("page") && name.ends_with(".png")
        })
        .collect();
    rendered.sort();
    let mut pages = Vec::new();
    for page in rendered {
        let data =
            std::fs::read(&page).map_err(|error| failed(format!("{}: {error}", page.display())))?;
        if data.len() > IMAGE_MAX_BYTES {
            return Err(refused(format!(
                "{} page render is {} bytes; over the {IMAGE_MAX_BYTES}-byte image cap. Re-read a \
                 smaller page range or a lower-DPI export.",
                path.display(),
                data.len()
            )));
        }
        pages.push(json!({
            "media_type": "image/png",
            "data": base64::engine::general_purpose::STANDARD.encode(&data),
        }));
    }
    Ok(pages)
}

/// What Python's `returncode` reads for the same run: the exit status, or the negated signal that
/// ended the process.
#[cfg(unix)]
fn exit_code(status: &std::process::ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    status
        .code()
        .or_else(|| status.signal().map(|signal| -signal))
        .unwrap_or(-1)
}

/// A pdf read: page count from `pdfinfo`, text from `pdftotext`, one png per page from `pdftoppm`.
/// Absent poppler the read still answers, with the text it can get and the pages it cannot render.
#[cfg(unix)]
fn read_pdf(named: &str, path: &Path, offset: i64, limit: i64) -> OpResult {
    if !on_path("pdfinfo") || !on_path("pdftoppm") {
        return Ok(json!({
            "path": named,
            "type": "pdf",
            "text": pdf_text(path, 1, PDF_DEFAULT_MAX_PAGES),
            "pages": [],
            "total_pages": null,
            "render_unavailable": true,
            "note": "page rendering requires poppler-utils",
        }));
    }
    let total = pdf_total_pages(path)?;
    let start = offset.max(1).min(total.max(1));
    let count = limit.min(PDF_DEFAULT_MAX_PAGES);
    let end = (start + count - 1).min(total);
    let pages = pdf_pages(path, start, end)?;
    let returned = pages.len() as i64;
    Ok(json!({
        "path": named,
        "type": "pdf",
        "text": pdf_text(path, start, end),
        "total_pages": total,
        "start_page": start,
        "pages_returned": returned,
        "next_page": if start + returned <= total { json!(start + returned) } else { json!(null) },
        "pages": pages,
        "quality_reminder": PDF_QUALITY_REMINDER,
    }))
}

/// A presentation read: LibreOffice converts it to pdf in a scratch directory and the pdf page path
/// answers it, so slides come back as images — the same shape as a pdf read, relabelled to the deck.
#[cfg(unix)]
fn read_pptx(named: &str, path: &Path, offset: i64, limit: i64) -> OpResult {
    if !on_path("soffice") {
        return Ok(json!({
            "path": named,
            "type": "pptx",
            "pages": [],
            "total_pages": null,
            "render_unavailable": true,
            "note": "slide rendering requires libreoffice (soffice)",
        }));
    }
    let scratch = Scratch::new()?;
    let profile = format!(
        "-env:UserInstallation=file://{}/profile",
        scratch.path().display()
    );
    let args = [
        "--headless".as_ref(),
        profile.as_ref(),
        "--convert-to".as_ref(),
        "pdf".as_ref(),
        "--outdir".as_ref(),
        scratch.path().as_os_str(),
        path.as_os_str(),
    ];
    let output = ran("soffice", &args)?;
    let stem = path.file_stem().unwrap_or_default().to_string_lossy();
    let pdf = scratch.path().join(format!("{stem}.pdf"));
    if !output.status.success() || !pdf.exists() {
        return Err(said(
            &output,
            format!("soffice could not render {}", path.display()),
        ));
    }
    let mut result = read_pdf(named, &pdf, offset, limit)?;
    result["type"] = json!("pptx");
    Ok(result)
}

fn unrendered(path: &str, kind: &str) -> Option<OpError> {
    match kind {
        ".pdf" => Some(refused(format!(
            "{path} is a pdf; a pdf read runs on the deploy"
        ))),
        ".pptx" => Some(refused(format!(
            "{path} is a pptx; a pptx read runs on the deploy"
        ))),
        _ => None,
    }
}

fn image_kind(kind: &str) -> bool {
    IMAGE_MEDIA_TYPES
        .iter()
        .any(|(extension, _)| *extension == kind)
}

/// The same read with its path taken through the containment guard: the bytes come off the pinned
/// parent fd, so a link planted at the target — or at any directory on the way to it — is refused
/// rather than followed.
#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, root: &Path) -> OpResult {
    let path = required(params, "path")?;
    let offset = (number_param(params, "offset", 1.0).trunc() as i64).max(1);
    let limit = number_param(params, "limit", READ_DEFAULT_LIMIT).trunc() as i64;
    let target = guarded(guard::contained_file(path, root, false))?;
    let named = target.path().display().to_string();
    let Some(entry) = guarded(target.lstat())? else {
        return Err(refused(format!("{named} not found")));
    };
    let kind = suffix(&named);
    if image_kind(&kind) {
        image_cap(&named, entry.size as usize)?;
        return image_result(&named, &guarded(target.read_bytes(entry.size))?);
    }
    // The converters take a filename, not an fd, so they get the canonical path the descent proved:
    // every component of it is link-free and the target itself was `lstat`ed as a regular file.
    if kind == ".pdf" {
        return read_pdf(&named, target.path(), offset, limit);
    }
    if kind == ".pptx" {
        return read_pptx(&named, target.path(), offset, limit);
    }
    if entry.size == 0 {
        return Ok(empty_result(&named, offset));
    }
    if binary_extension(&kind) {
        return Err(refused(format!("{named} is a binary file")));
    }
    let bytes = guarded(target.read_bytes(entry.size))?;
    if bytes[..bytes.len().min(BINARY_SNIFF_BYTES)].contains(&0) {
        return Err(refused(format!("{named} is a binary file")));
    }
    Ok(text_result(&named, &bytes, offset, limit))
}

pub fn run(params: &serde_json::Value) -> OpResult {
    let path = str_param(params, "path")?;
    let offset = (number_param(params, "offset", 1.0).trunc() as i64).max(1);
    let limit = number_param(params, "limit", READ_DEFAULT_LIMIT).trunc() as i64;
    if !exists(path) {
        return Err(refused(format!("{path} not found")));
    }
    let kind = suffix(path);
    if image_kind(&kind) {
        return image_result(path, &read_bytes(path)?);
    }
    if let Some(error) = unrendered(path, &kind) {
        return Err(error);
    }
    let bytes = read_bytes(path)?;
    if bytes.is_empty() {
        return Ok(empty_result(path, offset));
    }
    if binary_extension(&kind) {
        return Err(refused(format!("{path} is a binary file")));
    }
    if bytes[..bytes.len().min(BINARY_SNIFF_BYTES)].contains(&0) {
        return Err(refused(format!("{path} is a binary file")));
    }
    Ok(text_result(path, &bytes, offset, limit))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ops::fileops::text::OpError;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-read-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn windows_lines_with_offsets() {
        let dir = scratch("window");
        let path = dir.join("a.txt");
        std::fs::write(&path, "one\ntwo\nthree\nfour\n").unwrap();
        let params = json!({"path": path.to_str().unwrap(), "offset": 2, "limit": 2});
        let result = run(&params).unwrap();
        assert_eq!(result["total_lines"], 4);
        assert_eq!(result["lines_returned"], 2);
        assert_eq!(result["remaining_lines"], 1);
        assert_eq!(result["next_offset"], 4);
        assert_eq!(result["truncated"], true);
        assert_eq!(result["content"], "2\ttwo\n3\tthree");
    }

    #[test]
    fn empty_file_shape() {
        let dir = scratch("empty");
        let path = dir.join("empty.txt");
        std::fs::write(&path, "").unwrap();
        let result = run(&json!({"path": path.to_str().unwrap()})).unwrap();
        assert_eq!(result["is_empty"], true);
        assert_eq!(result["total_lines"], 0);
        assert_eq!(result["next_offset"], serde_json::Value::Null);
    }

    #[test]
    fn refuses_binary_and_missing() {
        let dir = scratch("binary");
        let path = dir.join("blob.dat");
        std::fs::write(&path, b"ab\0cd").unwrap();
        let text_path = path.to_str().unwrap();
        match run(&json!({"path": text_path})) {
            Err(OpError::Refused(message)) => {
                assert_eq!(message, format!("{text_path} is a binary file"))
            }
            _ => panic!("expected refusal"),
        }
        match run(&json!({"path": "/nope/never"})) {
            Err(OpError::Refused(message)) => assert_eq!(message, "/nope/never not found"),
            _ => panic!("expected refusal"),
        }
    }

    #[test]
    fn nul_sniff_refuses_extensionless_binary() {
        let dir = scratch("sniff");
        let path = dir.join("core");
        std::fs::write(&path, b"a\0b").unwrap();
        assert!(matches!(
            run(&json!({"path": path.to_str().unwrap()})),
            Err(OpError::Refused(_))
        ));
    }

    #[test]
    fn reads_png_as_image() {
        let dir = scratch("image");
        let path = dir.join("dot.png");
        let png = [0x89u8, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3];
        std::fs::write(&path, png).unwrap();
        let result = run(&json!({"path": path.to_str().unwrap()})).unwrap();
        assert_eq!(result["type"], "image");
        assert_eq!(result["media_type"], "image/png");
        assert_eq!(result["size_bytes"], 11);
        assert_eq!(
            result["data"],
            base64::engine::general_purpose::STANDARD.encode(png)
        );
    }

    #[test]
    fn fake_image_names_its_bytes() {
        let dir = scratch("fake");
        let path = dir.join("fake.png");
        std::fs::write(&path, b"not an image").unwrap();
        match run(&json!({"path": path.to_str().unwrap()})) {
            Err(OpError::Refused(message)) => assert!(message.contains("b'not an i'"), "{message}"),
            _ => panic!("expected refusal"),
        }
    }

    /// One page, one line of text, hand-written so the render test needs no fixture file.
    const MINI_PDF: &[u8] = b"%PDF-1.4\n\
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n\
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n\
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n\
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n\
5 0 obj<</Length 44>>stream\n\
BT /F1 24 Tf 20 40 Td (hello pdf) Tj ET\n\
endstream\n\
endobj\n\
trailer<</Root 1 0 R/Size 6>>\n\
%%EOF\n";

    /// A flat-XML presentation, which LibreOffice converts to a real `.pptx` for the read to answer.
    const MINI_FODP: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:body>
  <office:presentation>
   <draw:page draw:name="page1">
    <draw:frame svg:width="10cm" svg:height="2cm" svg:x="2cm" svg:y="2cm">
     <draw:text-box><text:p>hello slide</text:p></draw:text-box>
    </draw:frame>
   </draw:page>
  </office:presentation>
 </office:body>
</office:document>
"#;

    #[cfg(unix)]
    fn png_page(result: &serde_json::Value) -> Vec<u8> {
        let pages = result["pages"]
            .as_array()
            .expect("the read names its pages");
        assert!(!pages.is_empty(), "{result}");
        assert_eq!(pages[0]["media_type"], "image/png");
        base64::engine::general_purpose::STANDARD
            .decode(pages[0]["data"].as_str().unwrap())
            .expect("a page render is base64")
    }

    /// `sbxfs`'s pdf read, on the verb: poppler counts the pages, extracts the text and renders one
    /// png per page. The sandbox image bakes poppler; a machine without it is skipped rather than
    /// asserted against, because the degraded shape is what that machine answers.
    #[cfg(unix)]
    #[test]
    fn read_renders_a_pdf_through_poppler() {
        if !on_path("pdfinfo") || !on_path("pdftoppm") {
            return;
        }
        let root = std::fs::canonicalize(scratch("pdf")).unwrap();
        let path = root.join("mini.pdf");
        std::fs::write(&path, MINI_PDF).unwrap();
        let result = run_contained(&json!({"path": "mini.pdf"}), &root).unwrap();
        assert_eq!(result["type"], "pdf");
        assert_eq!(result["path"], path.to_str().unwrap());
        assert_eq!(result["total_pages"], 1);
        assert_eq!(result["start_page"], 1);
        assert_eq!(result["pages_returned"], 1);
        assert_eq!(result["next_page"], serde_json::Value::Null);
        assert!(
            result["text"].as_str().unwrap().contains("hello pdf"),
            "{result}"
        );
        assert_eq!(
            result["quality_reminder"],
            "CRITICAL: Before sharing, carefully examine each page for quality issues (e.g. \
             overlapping text, hidden/cut-off text, text squished together). These are common and \
             must be fixed."
        );
        assert!(png_page(&result).starts_with(&[0x89, 0x50, 0x4e, 0x47]));
    }

    /// `sbxfs`'s pptx read, on the verb: LibreOffice converts the deck to pdf and the pdf path
    /// answers it, so the result is a pdf read relabelled to the deck's own path and type.
    #[cfg(unix)]
    #[test]
    fn read_renders_a_pptx_through_libreoffice() {
        if !on_path("soffice") || !on_path("pdfinfo") || !on_path("pdftoppm") {
            return;
        }
        let root = std::fs::canonicalize(scratch("pptx")).unwrap();
        std::fs::write(root.join("deck.fodp"), MINI_FODP).unwrap();
        let converted = std::process::Command::new("soffice")
            .arg("--headless")
            .arg(format!(
                "-env:UserInstallation=file://{}/profile",
                root.display()
            ))
            .args(["--convert-to", "pptx", "--outdir"])
            .arg(&root)
            .arg(root.join("deck.fodp"))
            .output()
            .expect("soffice runs");
        let deck = root.join("deck.pptx");
        assert!(deck.exists(), "{converted:?}");
        let result = run_contained(&json!({"path": "deck.pptx"}), &root).unwrap();
        assert_eq!(result["type"], "pptx");
        assert_eq!(result["path"], deck.to_str().unwrap());
        assert_eq!(result["total_pages"], 1);
        assert!(png_page(&result).starts_with(&[0x89, 0x50, 0x4e, 0x47]));
    }

    /// The wire path is unchanged: the host renders a document on the deploy, so the op it drives
    /// still refuses one rather than shelling out on the user's own machine.
    #[test]
    fn the_wire_path_still_refuses_a_document() {
        let dir = scratch("wire-document");
        for (name, message) in [
            ("paper.pdf", "is a pdf; a pdf read runs on the deploy"),
            ("deck.pptx", "is a pptx; a pptx read runs on the deploy"),
        ] {
            let path = dir.join(name);
            std::fs::write(&path, b"body").unwrap();
            match run(&json!({"path": path.to_str().unwrap()})) {
                Err(OpError::Refused(said)) => assert_eq!(
                    said,
                    format!("{} {message}", path.to_str().unwrap()),
                    "{name}"
                ),
                other => panic!("expected refusal for {name}: {other:?}"),
            }
        }
    }

    #[test]
    fn python_bytes_repr_matches() {
        assert_eq!(python_bytes_repr(b"a'b"), "b\"a'b\"");
        assert_eq!(python_bytes_repr(b"a\"b'c"), "b'a\"b\\'c'");
        assert_eq!(
            python_bytes_repr(&[0x00, 0x09, 0x0a, 0x7f]),
            "b'\\x00\\t\\n\\x7f'"
        );
    }
}
