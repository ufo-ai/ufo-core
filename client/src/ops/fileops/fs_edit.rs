#[cfg(unix)]
use std::path::Path;

use serde_json::json;

#[cfg(unix)]
use crate::guard;
#[cfg(unix)]
use crate::ops::fileops::lock;
use crate::ops::fileops::text::{
    capped, decode_lossy, exists, failed, number_lines, points, read_bytes, refused, str_param,
    OpError, OpResult,
};
#[cfg(unix)]
use crate::ops::fileops::text::{guarded, required};

const EDIT_SNIPPET_CONTEXT: usize = 4;
const EDIT_SNIPPET_MAX_CHARS: usize = 2000;

struct Edit {
    old_string: String,
    new_string: String,
    replace_all: bool,
}

fn base64_value(character: char) -> Option<u32> {
    match character {
        'A'..='Z' => Some(character as u32 - 'A' as u32),
        'a'..='z' => Some(character as u32 - 'a' as u32 + 26),
        '0'..='9' => Some(character as u32 - '0' as u32 + 52),
        '+' | '-' => Some(62),
        '/' | '_' => Some(63),
        _ => None,
    }
}

fn decode_text(encoded: Option<&serde_json::Value>) -> Result<String, OpError> {
    let Some(encoded) = encoded.and_then(|value| value.as_str()) else {
        return Err(refused("text must be base64 encoded"));
    };
    if !points(encoded).is_multiple_of(4) {
        return Err(refused("text must be base64 encoded"));
    }
    let padding = if encoded.ends_with("==") {
        2
    } else if encoded.ends_with('=') {
        1
    } else {
        0
    };
    let body = &encoded[..encoded.len() - padding];
    let mut bytes = Vec::new();
    let mut word: u32 = 0;
    let mut held: u32 = 0;
    for character in body.chars() {
        let Some(value) = base64_value(character) else {
            return Err(refused("text must be base64 encoded"));
        };
        word = (word << 6) | value;
        held += 6;
        if held >= 8 {
            held -= 8;
            bytes.push(((word >> held) & 0xff) as u8);
        }
    }
    if padding > 0 && points(body).is_multiple_of(4) {
        return Err(refused("text must be base64 encoded"));
    }
    Ok(decode_lossy(&bytes))
}

fn edit_list(params: &serde_json::Value) -> Result<Vec<Edit>, OpError> {
    let edits = params.get("edits").and_then(|value| value.as_array());
    let Some(edits) = edits.filter(|list| !list.is_empty()) else {
        return Err(refused("edits must be a non-empty list"));
    };
    edits
        .iter()
        .map(|edit| {
            Ok(Edit {
                old_string: decode_text(edit.get("old_string_b64"))?,
                new_string: decode_text(edit.get("new_string_b64"))?,
                replace_all: edit
                    .get("replace_all")
                    .map(|value| match value {
                        serde_json::Value::Bool(flag) => *flag,
                        serde_json::Value::Number(number) => number.as_f64() != Some(0.0),
                        serde_json::Value::String(text) => !text.is_empty(),
                        serde_json::Value::Null => false,
                        _ => true,
                    })
                    .unwrap_or(false),
            })
        })
        .collect()
}

fn occurrences(text: &str, old: &str) -> usize {
    if old.is_empty() {
        return points(text) + 1;
    }
    text.matches(old).count()
}

fn replace_all(text: &str, old: &str, replacement: &str) -> String {
    if old.is_empty() {
        if text.is_empty() {
            return replacement.to_string();
        }
        let interleaved: Vec<String> = text.chars().map(String::from).collect();
        return format!(
            "{replacement}{}{replacement}",
            interleaved.join(replacement)
        );
    }
    text.replace(old, replacement)
}

fn replace_first(text: &str, old: &str, replacement: &str) -> String {
    if old.is_empty() {
        return format!("{replacement}{text}");
    }
    text.replacen(old, replacement, 1)
}

fn apply_edit(text: &str, edit: &Edit) -> Result<(String, usize), OpError> {
    let count = occurrences(text, &edit.old_string);
    if count == 0 {
        return Err(refused("old_string not found"));
    }
    if count > 1 && !edit.replace_all {
        return Err(refused(format!(
            "search text found {count} times. Provide more context or use replace_all=true."
        )));
    }
    if edit.replace_all {
        Ok((replace_all(text, &edit.old_string, &edit.new_string), count))
    } else {
        Ok((replace_first(text, &edit.old_string, &edit.new_string), 1))
    }
}

fn edit_snippet(text: &str, marker: &str) -> String {
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    let (hit_line, span) = match text.find(marker) {
        Some(index) => (
            text[..index].matches('\n').count(),
            marker.matches('\n').count(),
        ),
        None => (0, 0),
    };
    let start = hit_line
        .saturating_sub(EDIT_SNIPPET_CONTEXT)
        .min(lines.len());
    let end = (hit_line + span + EDIT_SNIPPET_CONTEXT + 1)
        .min(lines.len())
        .max(start);
    let width = end.max(1).to_string().len();
    number_lines(&lines[start..end], start + 1, width).join("\n")
}

fn applied(text: &str, edits: &[Edit]) -> Result<(String, usize), OpError> {
    let mut edited = text.to_string();
    let mut total = 0;
    for edit in edits {
        let (next, count) = apply_edit(&edited, edit)?;
        edited = next;
        total += count;
    }
    Ok((edited, total))
}

fn result(path: &str, text: &str, total: usize, marker: &str) -> serde_json::Value {
    json!({
        "path": path,
        "message": format!("{path}: {total} replacements"),
        "replacements": total,
        "snippet": capped(&edit_snippet(text, marker), EDIT_SNIPPET_MAX_CHARS),
    })
}

/// The same edit with its path taken through the containment guard: the text is read off the pinned
/// parent fd and written back to a staged sibling that is renamed onto the name, so neither a link
/// at the target nor a directory swapped under it takes the edit.
///
/// The whole read-modify-write runs under the target's cross-process lock, so a second editor in
/// another process reads this edit's text rather than the text it replaced.
#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, root: &Path) -> OpResult {
    let path = required(params, "path")?;
    let target = guarded(guard::contained_file(path, root, false))?;
    let named = target.path().display().to_string();
    let _held = lock::exclusive(target.path())?;
    let Some(entry) = guarded(target.lstat())? else {
        return Err(refused(format!("{named} not found")));
    };
    let edits = edit_list(params)?;
    let text = decode_lossy(&guarded(target.read_bytes(entry.size + 1))?);
    let (edited, total) = applied(&text, &edits)?;
    guarded(target.replace_text(&edited, entry.mode))?;
    Ok(result(&named, &edited, total, &edits[0].new_string))
}

pub fn run(params: &serde_json::Value) -> OpResult {
    let path = str_param(params, "path")?;
    if !exists(path) {
        return Err(refused(format!("{path} not found")));
    }
    let edits = edit_list(params)?;
    let (text, total) = applied(&decode_lossy(&read_bytes(path)?), &edits)?;
    std::fs::write(path, text.as_bytes())
        .map_err(|_| failed(format!("Could not write file: {path}")))?;
    Ok(result(path, &text, total, &edits[0].new_string))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode(text: &str) -> String {
        use base64::Engine as _;
        base64::engine::general_purpose::STANDARD.encode(text.as_bytes())
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-edit-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn applies_one_edit_with_snippet() {
        let dir = scratch("one");
        let path = dir.join("a.txt");
        std::fs::write(&path, "alpha\nbeta\ngamma\n").unwrap();
        let params = json!({
            "path": path.to_str().unwrap(),
            "edits": [{"old_string_b64": encode("beta"), "new_string_b64": encode("BETA")}],
        });
        let result = run(&params).unwrap();
        assert_eq!(result["replacements"], 1);
        assert_eq!(
            result["message"],
            format!("{}: 1 replacements", path.to_str().unwrap())
        );
        assert_eq!(result["snippet"], "1\talpha\n2\tBETA\n3\tgamma");
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "alpha\nBETA\ngamma\n"
        );
    }

    #[test]
    fn refuses_ambiguous_without_replace_all() {
        let dir = scratch("ambiguous");
        let path = dir.join("a.txt");
        std::fs::write(&path, "x x x").unwrap();
        let params = json!({
            "path": path.to_str().unwrap(),
            "edits": [{"old_string_b64": encode("x"), "new_string_b64": encode("y")}],
        });
        match run(&params) {
            Err(OpError::Refused(message)) => assert_eq!(
                message,
                "search text found 3 times. Provide more context or use replace_all=true."
            ),
            _ => panic!("expected refusal"),
        }
    }

    #[test]
    fn replace_all_counts_every_hit() {
        let dir = scratch("all");
        let path = dir.join("a.txt");
        std::fs::write(&path, "x x x").unwrap();
        let params = json!({
            "path": path.to_str().unwrap(),
            "edits": [{"old_string_b64": encode("x"), "new_string_b64": encode("y"), "replace_all": true}],
        });
        let result = run(&params).unwrap();
        assert_eq!(result["replacements"], 3);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "y y y");
    }

    #[test]
    fn empty_old_string_interleaves() {
        assert_eq!(occurrences("ab", ""), 3);
        assert_eq!(replace_all("ab", "", "-"), "-a-b-");
        assert_eq!(replace_all("", "", "-"), "-");
        assert_eq!(replace_first("ab", "", "-"), "-ab");
    }

    #[test]
    fn base64_quirks_match_the_js() {
        assert!(decode_text(Some(&json!("AQ=="))).is_ok());
        assert_eq!(decode_text(Some(&json!("aGk="))).unwrap(), "hi");
        assert_eq!(
            decode_text(Some(&json!("aGk"))).unwrap_err_message(),
            "text must be base64 encoded"
        );
        assert_eq!(
            decode_text(Some(&json!("aG-_"))).unwrap(),
            decode_lossy(&[0x68, 0x6f, 0xbf])
        );
        assert!(decode_text(None).is_err());
        assert!(decode_text(Some(&json!("a!bc"))).is_err());
    }

    #[test]
    fn missing_old_string_refuses() {
        let dir = scratch("missing");
        let path = dir.join("a.txt");
        std::fs::write(&path, "abc").unwrap();
        let params = json!({
            "path": path.to_str().unwrap(),
            "edits": [{"old_string_b64": encode("zzz"), "new_string_b64": encode("y")}],
        });
        match run(&params) {
            Err(OpError::Refused(message)) => assert_eq!(message, "old_string not found"),
            _ => panic!("expected refusal"),
        }
    }

    trait ErrMessage {
        fn unwrap_err_message(self) -> String;
    }

    impl<T> ErrMessage for Result<T, OpError> {
        fn unwrap_err_message(self) -> String {
            match self {
                Err(OpError::Refused(message)) | Err(OpError::Failed(message)) => message,
                Ok(_) => panic!("expected an error"),
            }
        }
    }
}
