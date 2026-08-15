use base64::Engine as _;
use serde_json::json;

use crate::ops::fileops::text::{
    binary_extension, decode_lossy, exists, number_lines, number_param, read_bytes, refused,
    str_param, suffix, OpResult, BINARY_SNIFF_BYTES,
};

const READ_DEFAULT_LIMIT: f64 = 2000.0;
pub const IMAGE_MAX_BYTES: usize = 5 * 1024 * 1024;

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

fn read_image(path: &str) -> OpResult {
    let bytes = read_bytes(path)?;
    if bytes.len() > IMAGE_MAX_BYTES {
        return Err(refused(format!(
            "{path} is {} bytes; over the {IMAGE_MAX_BYTES}-byte image read cap. Resize it (e.g. \
             with a bash tool) before reading.",
            bytes.len()
        )));
    }
    let Some(media_type) = image_media_type(&bytes) else {
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
        "data": base64::engine::general_purpose::STANDARD.encode(&bytes),
        "size_bytes": bytes.len(),
    }))
}

pub fn run(params: &serde_json::Value) -> OpResult {
    let path = str_param(params, "path")?;
    let offset = (number_param(params, "offset", 1.0).trunc() as i64).max(1);
    let limit = number_param(params, "limit", READ_DEFAULT_LIMIT).trunc() as i64;
    if !exists(path) {
        return Err(refused(format!("{path} not found")));
    }
    let kind = suffix(path);
    if IMAGE_MEDIA_TYPES
        .iter()
        .any(|(extension, _)| *extension == kind)
    {
        return read_image(path);
    }
    if kind == ".pdf" {
        return Err(refused(format!(
            "{path} is a pdf; a pdf read runs on the deploy"
        )));
    }
    if kind == ".pptx" {
        return Err(refused(format!(
            "{path} is a pptx; a pptx read runs on the deploy"
        )));
    }
    let bytes = read_bytes(path)?;
    if bytes.is_empty() {
        return Ok(json!({
            "path": path,
            "content": "",
            "total_lines": 0,
            "start_line": offset,
            "lines_returned": 0,
            "remaining_lines": 0,
            "next_offset": null,
            "truncated": false,
            "is_empty": true,
        }));
    }
    if binary_extension(&kind) {
        return Err(refused(format!("{path} is a binary file")));
    }
    if bytes[..bytes.len().min(BINARY_SNIFF_BYTES)].contains(&0) {
        return Err(refused(format!("{path} is a binary file")));
    }
    let text = decode_lossy(&bytes);
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
    Ok(json!({
        "path": path,
        "content": number_lines(window, offset as usize, width).join("\n"),
        "total_lines": total,
        "start_line": offset,
        "lines_returned": returned,
        "remaining_lines": remaining,
        "next_offset": if remaining > 0 { json!(consumed + 1) } else { json!(null) },
        "truncated": remaining > 0,
        "is_empty": false,
    }))
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
