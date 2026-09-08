#[cfg(unix)]
use std::collections::HashSet;
#[cfg(unix)]
use std::io::Read as _;
#[cfg(unix)]
use std::path::Path;
#[cfg(unix)]
use std::time::Duration;

use base64::Engine as _;
#[cfg(unix)]
use serde::Deserialize;
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
#[cfg(unix)]
const DOCUMENT_MAX_PAGES: i64 = 20;
#[cfg(unix)]
const DOCUMENT_MAX_INPUT_BYTES: u64 = 100 * 1024 * 1024;
#[cfg(unix)]
const DOCUMENT_MAX_BUNDLE_BYTES: u64 = 20 * 1024 * 1024;
#[cfg(unix)]
const DOCUMENT_MAX_MANIFEST_BYTES: u64 = 1024 * 1024;
#[cfg(unix)]
const DOCUMENT_MAX_IMAGE_BYTES: u64 = 5 * 1024 * 1024;
#[cfg(unix)]
const DOCUMENT_MAX_DECODED_IMAGE_BYTES: u64 = 20 * 1024 * 1024;
#[cfg(unix)]
const DOCUMENT_MAX_BASE64_BYTES: u64 = DOCUMENT_MAX_DECODED_IMAGE_BYTES.div_ceil(3) * 4;
#[cfg(unix)]
const DOCUMENT_RENDER_TIMEOUT: Duration = Duration::from_secs(330);
#[cfg(unix)]
const DOCUMENT_RENDER_URL: &str = "https://preview.ufo.internal/render";
#[cfg(unix)]
const DOCUMENT_RENDER_HOST: &str = "preview.ufo.internal";
#[cfg(unix)]
const DOCUMENT_RENDER_PORT: u16 = 443;
#[cfg(unix)]
const DOCUMENT_RENDER_PATH: &str = "/render";
#[cfg(unix)]
const DOCUMENT_RENDER_BEARER: &str = "ufo-preview-token-sentinel";
const DOCUMENT_KINDS: &[(&str, &str)] = &[
    (".pdf", "pdf"),
    (".pptx", "pptx"),
    (".docx", "docx"),
    (".xlsx", "xlsx"),
];
const DOCUMENT_QUALITY_REMINDER: &str =
    "CRITICAL: Before sharing, carefully examine each page for \
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

#[cfg(unix)]
#[derive(Deserialize)]
struct DocumentManifest {
    kind: String,
    total_pages: i64,
    requested_range: DocumentRange,
    pages: Vec<DocumentPage>,
}

#[cfg(unix)]
#[derive(Deserialize)]
struct DocumentRange {
    start_page: i64,
    limit: i64,
}

#[cfg(unix)]
#[derive(Deserialize)]
struct DocumentPage {
    number: i64,
    file: String,
    width: u32,
    height: u32,
    text: String,
}

#[cfg(unix)]
fn document_result(named: &str, kind: &str, bytes: &[u8], offset: i64, limit: i64) -> OpResult {
    if bytes.len() as u64 > DOCUMENT_MAX_INPUT_BYTES {
        return Err(refused(format!(
            "{named} is {} bytes; over the {DOCUMENT_MAX_INPUT_BYTES}-byte document read cap",
            bytes.len()
        )));
    }
    let pages = limit.clamp(1, DOCUMENT_MAX_PAGES);
    let request = json!({
        "kind": kind,
        "max_width": 1400,
        "max_height": 1800,
        "start_page": offset.max(1),
        "pages": pages,
        "sink": {"bundle": true},
    });
    let mut random = [0u8; 16];
    getrandom::fill(&mut random).map_err(|error| failed(format!("multipart boundary: {error}")))?;
    let boundary = format!(
        "ufo-document-{}",
        random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    let head = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n\r\n{}\r\n\
         --{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"document.{kind}\"\r\n\
         Content-Type: application/octet-stream\r\n\r\n",
        request
    );
    let tail = format!("\r\n--{boundary}--\r\n");
    let mut body = Vec::with_capacity(head.len() + bytes.len() + tail.len());
    body.extend_from_slice(head.as_bytes());
    body.extend_from_slice(bytes);
    body.extend_from_slice(tail.as_bytes());
    let request_head = format!(
        "POST {DOCUMENT_RENDER_PATH} HTTP/1.1\r\n\
         Host: {DOCUMENT_RENDER_HOST}\r\n\
         authorization: Bearer {DOCUMENT_RENDER_BEARER}\r\n\
         content-type: multipart/form-data; boundary={boundary}\r\n\
         content-length: {}\r\n\
         connection: close\r\n\r\n",
        body.len()
    );
    let (status, bundle) = crate::egress::post(
        DOCUMENT_RENDER_URL,
        DOCUMENT_RENDER_HOST,
        DOCUMENT_RENDER_PORT,
        &request_head,
        &body,
        DOCUMENT_RENDER_TIMEOUT,
        DOCUMENT_MAX_BUNDLE_BYTES as usize,
    )
    .map_err(|error| refused(format!("document render failed: {error}")))?;
    if status != 200 {
        return Err(refused(format!(
            "document render failed: {DOCUMENT_RENDER_URL} -> {status}: {}",
            String::from_utf8_lossy(&bundle)
        )));
    }
    document_result_from_bundle(named, kind, offset.max(1), pages, &bundle)
}

#[cfg(unix)]
fn document_result_from_bundle(
    named: &str,
    kind: &str,
    start_page: i64,
    limit: i64,
    bundle: &[u8],
) -> OpResult {
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bundle))
        .map_err(|error| failed(format!("document render bundle: {error}")))?;
    let manifest_entry = archive
        .by_name("manifest.json")
        .map_err(|error| failed(format!("document render manifest: {error}")))?;
    if manifest_entry.size() > DOCUMENT_MAX_MANIFEST_BYTES {
        return Err(refused(format!(
            "document manifest exceeds the {DOCUMENT_MAX_MANIFEST_BYTES}-byte cap"
        )));
    }
    let manifest: DocumentManifest = serde_json::from_reader(manifest_entry)
        .map_err(|error| failed(format!("document render manifest: {error}")))?;
    if manifest.kind != kind
        || manifest.total_pages < 1
        || manifest.requested_range.start_page != start_page
        || manifest.requested_range.limit != limit
        || manifest.pages.is_empty()
        || manifest.pages.len() as i64 > limit
        || manifest.pages[0].number != start_page.min(manifest.total_pages)
        || manifest.pages.iter().any(|page| {
            page.number < 1
                || page.number > manifest.total_pages
                || page.width == 0
                || page.height == 0
        })
        || manifest
            .pages
            .windows(2)
            .any(|pages| pages[1].number != pages[0].number + 1)
    {
        return Err(failed("document render returned a mismatched manifest"));
    }
    let expected = std::iter::once("manifest.json")
        .chain(manifest.pages.iter().map(|page| page.file.as_str()))
        .collect::<HashSet<_>>();
    let actual = archive.file_names().collect::<Vec<_>>();
    if actual.len() != expected.len() || actual.iter().copied().collect::<HashSet<_>>() != expected
    {
        return Err(failed("document render returned unexpected bundle entries"));
    }
    let mut decoded = 0u64;
    let mut encoded = 0u64;
    let mut images = Vec::with_capacity(manifest.pages.len());
    let mut text = Vec::with_capacity(manifest.pages.len());
    for (offset, page) in manifest.pages.iter().enumerate() {
        if page.file != format!("page-{:02}.png", offset + 1) {
            return Err(failed("document render returned an invalid page name"));
        }
        let mut entry = archive
            .by_name(&page.file)
            .map_err(|error| failed(format!("document render page: {error}")))?;
        if entry.size() > DOCUMENT_MAX_IMAGE_BYTES {
            return Err(refused(format!(
                "document page {} exceeds the {DOCUMENT_MAX_IMAGE_BYTES}-byte image cap",
                page.number
            )));
        }
        decoded += entry.size();
        encoded += entry.size().div_ceil(3) * 4;
        if decoded > DOCUMENT_MAX_DECODED_IMAGE_BYTES || encoded > DOCUMENT_MAX_BASE64_BYTES {
            return Err(refused(
                "document pages exceed the decoded image or base64 cap",
            ));
        }
        let mut bytes = Vec::with_capacity(entry.size() as usize);
        entry
            .read_to_end(&mut bytes)
            .map_err(|error| failed(format!("document render page: {error}")))?;
        if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err(failed("document render returned a non-png page"));
        }
        images.push(json!({
            "page": page.number,
            "width": page.width,
            "height": page.height,
            "media_type": "image/png",
            "data": base64::engine::general_purpose::STANDARD.encode(bytes),
        }));
        if !page.text.trim().is_empty() {
            text.push(page.text.trim());
        }
    }
    let returned = images.len() as i64;
    let actual_start = manifest
        .pages
        .first()
        .map(|page| page.number)
        .unwrap_or(start_page);
    let next = manifest
        .pages
        .last()
        .map(|page| page.number + 1)
        .filter(|next| *next <= manifest.total_pages);
    Ok(json!({
        "path": named,
        "type": kind,
        "text": text.join("\n\n"),
        "total_pages": manifest.total_pages,
        "start_page": actual_start,
        "pages_returned": returned,
        "next_page": next,
        "pages": images,
        "quality_reminder": DOCUMENT_QUALITY_REMINDER,
    }))
}

fn unrendered(path: &str, kind: &str) -> Option<OpError> {
    DOCUMENT_KINDS
        .iter()
        .find(|(extension, _)| *extension == kind)
        .map(|(_, document)| {
            refused(format!(
                "{path} is a {document}; its read runs on the deploy"
            ))
        })
}

fn image_kind(kind: &str) -> bool {
    IMAGE_MEDIA_TYPES
        .iter()
        .any(|(extension, _)| *extension == kind)
}

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
    if let Some((_, document)) = DOCUMENT_KINDS
        .iter()
        .find(|(extension, _)| *extension == kind)
    {
        if entry.size > DOCUMENT_MAX_INPUT_BYTES {
            return Err(refused(format!(
                "{named} is {} bytes; over the {DOCUMENT_MAX_INPUT_BYTES}-byte document read cap",
                entry.size
            )));
        }
        let bytes = guarded(target.read_bytes(entry.size))?;
        return document_result(&named, document, &bytes, offset, limit);
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

    #[cfg(unix)]
    fn document_bundle(kind: &str, start_page: i64, limit: i64, page_file: &str) -> Vec<u8> {
        let mut zip = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored);
        zip.start_file("manifest.json", options).unwrap();
        std::io::Write::write_all(
            &mut zip,
            serde_json::to_string(&json!({
                "kind": kind,
                "total_pages": 3,
                "requested_range": {"start_page": start_page, "limit": limit},
                "pages": [{
                    "number": 2,
                    "file": page_file,
                    "width": 640,
                    "height": 480,
                    "text": "visible page two",
                }],
            }))
            .unwrap()
            .as_bytes(),
        )
        .unwrap();
        zip.start_file(page_file, options).unwrap();
        std::io::Write::write_all(&mut zip, b"\x89PNG\r\n\x1a\nrendered").unwrap();
        zip.finish().unwrap().into_inner()
    }

    #[cfg(unix)]
    #[test]
    fn a_document_bundle_keeps_the_paginated_text_and_image_shape() {
        let bundle = document_bundle("pdf", 2, 1, "page-01.png");
        let result =
            document_result_from_bundle("/workspace/paper.pdf", "pdf", 2, 1, &bundle).unwrap();
        assert_eq!(result["type"], "pdf");
        assert_eq!(result["path"], "/workspace/paper.pdf");
        assert_eq!(result["text"], "visible page two");
        assert_eq!(result["total_pages"], 3);
        assert_eq!(result["start_page"], 2);
        assert_eq!(result["pages_returned"], 1);
        assert_eq!(result["next_page"], 3);
        assert_eq!(result["pages"][0]["page"], 2);
        assert_eq!(result["pages"][0]["width"], 640);
        assert!(base64::engine::general_purpose::STANDARD
            .decode(result["pages"][0]["data"].as_str().unwrap())
            .unwrap()
            .starts_with(b"\x89PNG"));
    }

    #[cfg(unix)]
    #[test]
    fn a_document_bundle_refuses_noncanonical_page_names() {
        let bundle = document_bundle("pdf", 2, 1, "other.png");
        let error =
            document_result_from_bundle("/workspace/paper.pdf", "pdf", 2, 1, &bundle).unwrap_err();
        assert!(matches!(error, OpError::Failed(_)));
    }

    #[test]
    fn the_wire_path_still_refuses_a_document() {
        let dir = scratch("wire-document");
        for name in ["paper.pdf", "deck.pptx", "letter.docx", "sheet.xlsx"] {
            let path = dir.join(name);
            std::fs::write(&path, b"body").unwrap();
            match run(&json!({"path": path.to_str().unwrap()})) {
                Err(OpError::Refused(said)) => {
                    assert!(said.ends_with("its read runs on the deploy"))
                }
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
