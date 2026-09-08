use std::path::Path;

#[cfg(unix)]
use crate::guard::GuardError;

#[derive(Debug)]
pub enum OpError {
    Refused(String),
    Failed(String),
}

pub type OpResult = Result<serde_json::Value, OpError>;

pub fn refused(message: impl Into<String>) -> OpError {
    OpError::Refused(message.into())
}

pub fn failed(message: impl Into<String>) -> OpError {
    OpError::Failed(message.into())
}

#[cfg(unix)]
pub fn guarded<T>(outcome: Result<T, GuardError>) -> Result<T, OpError> {
    outcome.map_err(|error| refused(error.message()))
}

#[cfg(unix)]
pub fn required<'a>(params: &'a serde_json::Value, name: &str) -> Result<&'a str, OpError> {
    params
        .get(name)
        .and_then(|value| value.as_str())
        .ok_or_else(|| refused(format!("KeyError: '{name}'")))
}

pub fn str_param<'a>(params: &'a serde_json::Value, name: &str) -> Result<&'a str, OpError> {
    params
        .get(name)
        .and_then(|value| value.as_str())
        .ok_or_else(|| failed(format!("{name} must be a string")))
}

pub fn bool_param(params: &serde_json::Value, name: &str) -> bool {
    params
        .get(name)
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
}

pub fn number_param(params: &serde_json::Value, name: &str, fallback: f64) -> f64 {
    match params.get(name).and_then(|value| value.as_f64()) {
        Some(value) if value != 0.0 => value,
        _ => fallback,
    }
}

pub const LINE_CHAR_CAP: usize = 2000;
pub const BINARY_SNIFF_BYTES: usize = 8192;

pub const BINARY_EXTENSIONS: &[&str] = &[
    ".7z", ".a", ".avi", ".bin", ".bmp", ".bz2", ".class", ".dat", ".db", ".dll", ".dylib", ".eot",
    ".exe", ".flac", ".gif", ".gz", ".heic", ".ico", ".jar", ".jpeg", ".jpg", ".m4a", ".mkv",
    ".mov", ".mp3", ".mp4", ".o", ".obj", ".ogg", ".otf", ".pdf", ".png", ".pyc", ".pyo", ".rar",
    ".so", ".sqlite", ".sqlite3", ".svgz", ".tar", ".tiff", ".ttf", ".war", ".wasm", ".wav",
    ".webm", ".webp", ".woff", ".woff2", ".xz", ".zip",
];

pub fn binary_extension(suffix: &str) -> bool {
    BINARY_EXTENSIONS.contains(&suffix)
}

pub fn decode_lossy(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

pub fn points(text: &str) -> usize {
    text.chars().count()
}

pub fn over(text: &str, cap: usize) -> bool {
    points(text) > cap
}

pub fn capped(text: &str, cap: usize) -> String {
    if points(text) <= cap {
        text.to_string()
    } else {
        text.chars().take(cap).collect()
    }
}

pub fn cap_line(line: &str) -> String {
    if !over(line, LINE_CHAR_CAP) {
        return line.to_string();
    }
    format!(
        "{}... [+{} chars]",
        capped(line, LINE_CHAR_CAP),
        points(line) - LINE_CHAR_CAP
    )
}

pub fn number_lines(lines: &[&str], start: usize, width: usize) -> Vec<String> {
    lines
        .iter()
        .enumerate()
        .map(|(index, line)| format!("{:>width$}\t{}", start + index, cap_line(line)))
        .collect()
}

pub fn basename(path: &str) -> &str {
    &path[path.rfind('/').map_or(0, |cut| cut + 1)..]
}

pub fn parent(path: &str) -> &str {
    match path.rfind('/') {
        None | Some(0) => "/",
        Some(cut) => &path[..cut],
    }
}

pub fn suffix(path: &str) -> String {
    let name = basename(path);
    match name.rfind('.') {
        Some(dot) if dot > 0 && dot < name.len() - 1 => name[dot..].to_lowercase(),
        _ => String::new(),
    }
}

pub fn exists(path: &str) -> bool {
    std::fs::symlink_metadata(path).is_ok()
}

pub fn read_bytes(path: &str) -> Result<Vec<u8>, OpError> {
    std::fs::read(path).map_err(|_| failed(format!("Could not open file: {path}")))
}

pub fn workdir_text(workdir: &Path, name: &str) -> Result<String, OpError> {
    let bytes = std::fs::read(workdir.join(name))
        .map_err(|error| failed(format!("could not read {name}: {error}")))?;
    Ok(decode_lossy(&bytes))
}

pub fn listing(workdir: &Path, name: &str) -> Result<Vec<String>, OpError> {
    let text = workdir_text(workdir, name)?;
    let mut items: Vec<String> = text.split('\0').map(str::to_string).collect();
    if items.last().is_some_and(|last| last.is_empty()) {
        items.pop();
    }
    Ok(items)
}

fn regex_literal(character: char) -> bool {
    matches!(
        character,
        '.' | '*' | '+' | '?' | '^' | '$' | '{' | '}' | '(' | ')' | '|' | '[' | ']' | '\\' | '/'
    )
}

pub fn fnmatch_expression(pattern: &str) -> String {
    let chars: Vec<char> = pattern.chars().collect();
    let mut expression = String::new();
    let mut index = 0;
    while index < chars.len() {
        let character = chars[index];
        index += 1;
        match character {
            '*' => expression.push_str(".*"),
            '?' => expression.push('.'),
            '[' => {
                let mut end = index;
                if chars.get(end) == Some(&'!') {
                    end += 1;
                }
                if chars.get(end) == Some(&']') {
                    end += 1;
                }
                while end < chars.len() && chars[end] != ']' {
                    end += 1;
                }
                if end >= chars.len() {
                    expression.push_str("\\[");
                } else {
                    let mut inside = chars[index..end]
                        .iter()
                        .collect::<String>()
                        .replace('\\', "\\\\");
                    index = end + 1;
                    if inside == "!" {
                        expression.push('.');
                    } else {
                        if let Some(rest) = inside.strip_prefix('!') {
                            inside = format!("^{rest}");
                        } else if inside.starts_with('^') || inside.starts_with('[') {
                            inside = format!("\\{inside}");
                        }
                        expression.push('[');
                        expression.push_str(&inside);
                        expression.push(']');
                    }
                }
            }
            other => {
                if regex_literal(other) {
                    expression.push('\\');
                }
                expression.push(other);
            }
        }
    }
    expression
}

pub fn fnmatch(pattern: &str) -> Result<fancy_regex::Regex, OpError> {
    fancy_regex::Regex::new(&format!("(?s)^{}$", fnmatch_expression(pattern)))
        .map_err(|error| failed(format!("invalid glob pattern: {error}")))
}

pub fn matches(regex: &fancy_regex::Regex, text: &str) -> Result<bool, OpError> {
    regex
        .is_match(text)
        .map_err(|error| failed(format!("regex match failed: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lossy_decode_replaces_truncated_sequences() {
        assert_eq!(decode_lossy(&[0x61, 0xe2, 0x82]), "a\u{fffd}");
        assert_eq!(decode_lossy(&[0xe0, 0x80, 0x61]), "\u{fffd}\u{fffd}a");
        assert_eq!(decode_lossy(&[0xf0, 0x9f, 0x92, 0xa9]), "💩");
    }

    #[test]
    fn cap_line_appends_overflow() {
        let line = "x".repeat(2005);
        assert_eq!(
            cap_line(&line),
            format!("{}... [+5 chars]", "x".repeat(2000))
        );
        assert_eq!(cap_line("short"), "short");
    }

    #[test]
    fn number_lines_pads_to_width() {
        assert_eq!(number_lines(&["a", "b"], 9, 3), vec!["  9\ta", " 10\tb"]);
    }

    #[test]
    fn suffix_skips_hidden_and_trailing_dots() {
        assert_eq!(suffix("/a/b/x.PY"), ".py");
        assert_eq!(suffix("/a/.env"), "");
        assert_eq!(suffix("/a/tail."), "");
        assert_eq!(suffix("/a/none"), "");
    }

    #[test]
    fn fnmatch_ports_classes_and_negation() {
        let glob = fnmatch("*.rs").unwrap();
        assert!(matches(&glob, "main.rs").unwrap());
        assert!(!matches(&glob, "main.rss").unwrap());
        let negated = fnmatch("[!a]bc").unwrap();
        assert!(matches(&negated, "xbc").unwrap());
        assert!(!matches(&negated, "abc").unwrap());
        let class = fnmatch("a[b-d]e").unwrap();
        assert!(matches(&class, "ace").unwrap());
        assert!(!matches(&class, "aze").unwrap());
        let unterminated = fnmatch("a[b").unwrap();
        assert!(matches(&unterminated, "a[b").unwrap());
        let star_newline = fnmatch("a*z").unwrap();
        assert!(matches(&star_newline, "a\nz").unwrap());
    }

    #[test]
    fn parent_and_basename_match_the_js() {
        assert_eq!(parent("/a/b/c"), "/a/b");
        assert_eq!(parent("/a"), "/");
        assert_eq!(parent("rel"), "/");
        assert_eq!(basename("/a/b/c.txt"), "c.txt");
        assert_eq!(basename("bare"), "bare");
    }
}
