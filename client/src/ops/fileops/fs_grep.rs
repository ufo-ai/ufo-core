use std::collections::HashMap;
use std::path::Path;

use serde_json::json;

#[cfg(unix)]
use crate::guard;
#[cfg(unix)]
use crate::ops::fileops::text::guarded;
use crate::ops::fileops::text::{
    basename, binary_extension, bool_param, capped, decode_lossy, failed, fnmatch, listing,
    matches, number_param, over, parent, points, refused, str_param, suffix, OpError, OpResult,
    BINARY_SNIFF_BYTES,
};
#[cfg(unix)]
use crate::ops::fileops::{fs_glob, walk};

const OUTPUT_MODES: &[&str] = &["content", "files_with_matches", "count"];
const GREP_DEFAULT_HEAD: f64 = 100.0;
const GREP_LINE_CHAR_CAP: usize = 2000;

fn type_extensions(kind: &str) -> &'static [&'static str] {
    match kind {
        "py" => &[".py", ".pyi"],
        "js" => &[".js", ".jsx", ".mjs", ".cjs"],
        "ts" => &[".ts", ".tsx"],
        "rust" => &[".rs"],
        "go" => &[".go"],
        "java" => &[".java"],
        "c" => &[".c", ".h"],
        "cpp" => &[".cc", ".cpp", ".cxx", ".hpp", ".hh", ".h"],
        "md" => &[".md", ".markdown"],
        "json" => &[".json", ".jsonl"],
        "yaml" => &[".yaml", ".yml"],
        "html" => &[".html", ".htm"],
        "css" => &[".css", ".scss", ".sass", ".less"],
        "sh" => &[".sh", ".bash", ".zsh"],
        _ => &[],
    }
}

fn grep_regex(
    pattern: &str,
    multiline: bool,
    ignore_case: bool,
) -> Result<fancy_regex::Regex, OpError> {
    let mut flags = String::new();
    if multiline {
        flags.push_str("sm");
    }
    if ignore_case {
        flags.push('i');
    }
    let wrapped = if flags.is_empty() {
        pattern.to_string()
    } else {
        format!("(?{flags}){pattern}")
    };
    fancy_regex::Regex::new(&wrapped).map_err(|error| refused(format!("invalid regex: {error}")))
}

fn contained_text(path: &str) -> Option<String> {
    if binary_extension(&suffix(path)) {
        return None;
    }
    let bytes = std::fs::read(path).ok()?;
    if bytes[..bytes.len().min(BINARY_SNIFF_BYTES)].contains(&0) {
        return None;
    }
    Some(decode_lossy(&bytes))
}

fn admitted(
    entries: &[String],
    glob: Option<&str>,
    kind: Option<&str>,
) -> Result<Vec<String>, OpError> {
    if let Some(glob) = glob {
        if !glob.is_empty() {
            let glob = fnmatch(glob)?;
            let mut kept = Vec::new();
            for path in entries {
                if matches(&glob, basename(path))? || matches(&glob, path)? {
                    kept.push(path.clone());
                }
            }
            return Ok(kept);
        }
    }
    if let Some(kind) = kind {
        if !kind.is_empty() {
            let wanted = type_extensions(kind);
            return Ok(entries
                .iter()
                .filter(|path| wanted.contains(&suffix(path).as_str()))
                .cloned()
                .collect());
        }
    }
    Ok(entries.to_vec())
}

fn selector(params: &serde_json::Value) -> (Option<&str>, Option<&str>) {
    (
        params.get("glob").and_then(|value| value.as_str()),
        params.get("type").and_then(|value| value.as_str()),
    )
}

fn candidates(
    params: &serde_json::Value,
    workdir: &Path,
    glob: Option<&str>,
    kind: Option<&str>,
) -> Result<Vec<String>, OpError> {
    let entries = listing(workdir, str_param(params, "enum")?)?;
    let mut position: HashMap<&str, i64> = HashMap::new();
    for (index, path) in entries.iter().enumerate() {
        position.entry(path.as_str()).or_insert(index as i64);
    }
    let mut ranked: Vec<(i64, String, String)> = admitted(&entries, glob, kind)?
        .into_iter()
        .map(|path| {
            let rank = position.get(parent(&path)).copied().unwrap_or(-1);
            let name = basename(&path).to_string();
            (rank, name, path)
        })
        .collect();
    ranked.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
    Ok(ranked.into_iter().map(|entry| entry.2).collect())
}

fn match_line(line: &str) -> String {
    if !over(line, GREP_LINE_CHAR_CAP) {
        return line.to_string();
    }
    let extra = points(line) - GREP_LINE_CHAR_CAP;
    format!("{}... [+{extra} chars]", capped(line, GREP_LINE_CHAR_CAP))
}

fn count_matches(regex: &fancy_regex::Regex, text: &str) -> Result<usize, OpError> {
    let mut count = 0;
    for found in regex.find_iter(text) {
        found.map_err(|error| failed(format!("regex match failed: {error}")))?;
        count += 1;
    }
    Ok(count)
}

struct Query<'a> {
    mode: &'a str,
    limit: usize,
    before: usize,
    after: usize,
    pattern: &'a str,
    multiline: bool,
    ignore_case: bool,
}

fn query(params: &serde_json::Value) -> Result<Query<'_>, OpError> {
    let mode = params
        .get("output_mode")
        .and_then(|value| value.as_str())
        .unwrap_or("files_with_matches");
    if !OUTPUT_MODES.contains(&mode) {
        return Err(refused(format!("invalid output_mode: {mode}")));
    }
    let context = number_param(params, "context", 0.0).trunc() as usize;
    Ok(Query {
        mode,
        limit: number_param(params, "head_limit", GREP_DEFAULT_HEAD).trunc() as usize,
        before: match number_param(params, "before_context", 0.0).trunc() as usize {
            0 => context,
            explicit => explicit,
        },
        after: match number_param(params, "after_context", 0.0).trunc() as usize {
            0 => context,
            explicit => explicit,
        },
        pattern: str_param(params, "pattern")?,
        multiline: bool_param(params, "multiline"),
        ignore_case: bool_param(params, "ignore_case"),
    })
}

#[cfg(unix)]
fn guarded_text(path: &str, workspace: &Path) -> Option<String> {
    if binary_extension(&suffix(path)) {
        return None;
    }
    let target = guard::contained_file(path, workspace, false).ok()?;
    let entry = target.lstat().ok()??;
    let bytes = target.read_bytes(entry.size).ok()?;
    if bytes[..bytes.len().min(BINARY_SNIFF_BYTES)].contains(&0) {
        return None;
    }
    Some(decode_lossy(&bytes))
}

#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, workspace: &Path, workdir: &Path) -> OpResult {
    let query = query(params)?;
    let root = workspace.to_string_lossy().into_owned();
    let (raw_glob, kind) = selector(params);
    let glob = match raw_glob {
        Some(glob) => Some(fs_glob::contained_pattern(glob, &root)?),
        None => None,
    };
    let entries = if params.get("enum").is_some_and(|value| value.is_string()) {
        candidates(params, workdir, glob, kind)?
    } else {
        admitted(&start_walk(params, workspace, &root)?, glob, kind)?
    };
    let files = entries
        .into_iter()
        .filter(|path| guard::is_contained_regular(Path::new(path), workspace))
        .collect();
    scanned(&query, files, &|path| guarded_text(path, workspace))
}

#[cfg(unix)]
fn start_walk(
    params: &serde_json::Value,
    workspace: &Path,
    root: &str,
) -> Result<Vec<String>, OpError> {
    let named = params
        .get("path")
        .and_then(|value| value.as_str())
        .unwrap_or(root)
        .to_string();
    let walked = if guard::rooted(&named, workspace).is_file() {
        vec![guarded(guard::contained_regular(&named, workspace))?]
    } else {
        walk::walk_files(&guarded(guard::contained_dir(&named, workspace, false))?)
    };
    Ok(walked
        .iter()
        .map(|hit| hit.to_string_lossy().into_owned())
        .collect())
}

pub fn run(params: &serde_json::Value, workdir: &Path) -> OpResult {
    let query = query(params)?;
    let (glob, kind) = selector(params);
    let files = candidates(params, workdir, glob, kind)?;
    scanned(&query, files, &contained_text)
}

fn scanned(query: &Query, files: Vec<String>, read: &dyn Fn(&str) -> Option<String>) -> OpResult {
    let Query {
        mode,
        limit,
        before,
        after,
        pattern,
        multiline,
        ignore_case,
    } = *query;
    let regex = grep_regex(pattern, multiline, ignore_case)?;

    if mode == "files_with_matches" {
        let mut matched: Vec<String> = Vec::new();
        for path in files {
            let Some(text) = read(&path) else {
                continue;
            };
            let hit = if multiline {
                matches(&regex, &text)?
            } else {
                let mut any = false;
                for line in text.split('\n') {
                    if matches(&regex, line)? {
                        any = true;
                        break;
                    }
                }
                any
            };
            if !hit {
                continue;
            }
            matched.push(path);
            if matched.len() >= limit {
                return Ok(json!({"files": matched, "count": matched.len(), "truncated": true}));
            }
        }
        return Ok(json!({"files": matched, "count": matched.len(), "truncated": false}));
    }

    if mode == "count" {
        let counting = if multiline {
            grep_regex(pattern, multiline, ignore_case)?
        } else {
            regex
        };
        let mut counts = Vec::new();
        let mut total = 0;
        for path in files {
            let Some(text) = read(&path) else {
                continue;
            };
            let hit = if multiline {
                count_matches(&counting, &text)?
            } else {
                let mut lines_hit = 0;
                for line in text.split('\n') {
                    if matches(&counting, line)? {
                        lines_hit += 1;
                    }
                }
                lines_hit
            };
            if hit == 0 {
                continue;
            }
            counts.push(json!({"file": path, "count": hit}));
            total += hit;
        }
        return Ok(json!({"counts": counts, "total_matches": total}));
    }

    let mut found: Vec<serde_json::Value> = Vec::new();
    for path in files {
        let Some(text) = read(&path) else {
            continue;
        };
        let lines: Vec<&str> = text.split('\n').collect();
        for index in 0..lines.len() {
            if !matches(&regex, lines[index])? {
                continue;
            }
            let mut entry = json!({
                "file": path,
                "line": index + 1,
                "content": match_line(lines[index]),
            });
            let mut rows = Vec::new();
            let neighbors = (index.saturating_sub(before)..index)
                .chain(index + 1..(index + 1 + after).min(lines.len()));
            for row in neighbors {
                rows.push(json!({"line": row + 1, "content": match_line(lines[row])}));
            }
            if !rows.is_empty() {
                entry["context"] = json!(rows);
            }
            found.push(entry);
            if found.len() >= limit {
                return Ok(json!({"matches": found, "count": found.len(), "truncated": true}));
            }
        }
    }
    Ok(json!({"matches": found, "count": found.len(), "truncated": false}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-grep-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_enum(workdir: &Path, entries: &[&str]) {
        let mut body = entries.join("\0");
        body.push('\0');
        std::fs::write(workdir.join("grep-enum"), body).unwrap();
    }

    #[test]
    fn orders_by_directory_position_then_name() {
        let dir = scratch("order");
        let root = dir.to_str().unwrap().to_string();
        let sub = format!("{root}/sub");
        std::fs::create_dir_all(&sub).unwrap();
        for name in ["b.py", "a.py"] {
            std::fs::write(format!("{root}/{name}"), "match me\n").unwrap();
        }
        std::fs::write(format!("{sub}/c.py"), "match me\n").unwrap();
        write_enum(
            &dir,
            &[
                root.as_str(),
                &format!("{root}/b.py"),
                &format!("{root}/a.py"),
                sub.as_str(),
                &format!("{sub}/c.py"),
            ],
        );
        let params = json!({"pattern": "match", "enum": "grep-enum"});
        let result = run(&params, &dir).unwrap();
        assert_eq!(
            result["files"],
            json!([
                format!("{root}/a.py"),
                format!("{root}/b.py"),
                format!("{sub}/c.py")
            ])
        );
        assert_eq!(result["truncated"], false);
    }

    #[test]
    fn content_mode_carries_context_rows() {
        let dir = scratch("context");
        let root = dir.to_str().unwrap().to_string();
        let path = format!("{root}/f.txt");
        std::fs::write(&path, "one\ntwo\nhit\nfour\nfive\n").unwrap();
        write_enum(&dir, &[root.as_str(), path.as_str()]);
        let params = json!({
            "pattern": "hit",
            "enum": "grep-enum",
            "output_mode": "content",
            "context": 1,
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 1);
        let entry = &result["matches"][0];
        assert_eq!(entry["line"], 3);
        assert_eq!(
            entry["context"],
            json!([{"line": 2, "content": "two"}, {"line": 4, "content": "four"}])
        );
    }

    #[test]
    fn count_mode_totals_lines() {
        let dir = scratch("count");
        let root = dir.to_str().unwrap().to_string();
        let path = format!("{root}/f.md");
        std::fs::write(&path, "x\nyx\nz\n").unwrap();
        write_enum(&dir, &[root.as_str(), path.as_str()]);
        let params = json!({"pattern": "x", "enum": "grep-enum", "output_mode": "count"});
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["total_matches"], 2);
        assert_eq!(result["counts"][0]["count"], 2);
    }

    #[test]
    fn multiline_spans_lines() {
        let dir = scratch("multiline");
        let root = dir.to_str().unwrap().to_string();
        let path = format!("{root}/f.rs");
        std::fs::write(&path, "fn main() {\n    body\n}\n").unwrap();
        write_enum(&dir, &[root.as_str(), path.as_str()]);
        let params = json!({
            "pattern": r"main.*body",
            "enum": "grep-enum",
            "multiline": true,
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 1);
    }

    #[test]
    fn glob_filter_and_lookahead() {
        let dir = scratch("glob");
        let root = dir.to_str().unwrap().to_string();
        std::fs::write(format!("{root}/a.py"), "alpha beta\n").unwrap();
        std::fs::write(format!("{root}/b.txt"), "alpha beta\n").unwrap();
        write_enum(
            &dir,
            &[
                root.as_str(),
                &format!("{root}/a.py"),
                &format!("{root}/b.txt"),
            ],
        );
        let params = json!({
            "pattern": r"alpha(?= beta)",
            "enum": "grep-enum",
            "glob": "*.py",
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["files"], json!([format!("{root}/a.py")]));
    }

    #[test]
    fn invalid_regex_refuses() {
        let dir = scratch("badregex");
        write_enum(&dir, &[]);
        let params = json!({"pattern": "(", "enum": "grep-enum"});
        match run(&params, &dir) {
            Err(OpError::Refused(message)) => assert!(message.starts_with("invalid regex: ")),
            _ => panic!("expected refusal"),
        }
    }

    #[test]
    fn head_limit_truncates() {
        let dir = scratch("limit");
        let root = dir.to_str().unwrap().to_string();
        for name in ["a.txt", "b.txt", "c.txt"] {
            std::fs::write(format!("{root}/{name}"), "hit\n").unwrap();
        }
        write_enum(
            &dir,
            &[
                root.as_str(),
                &format!("{root}/a.txt"),
                &format!("{root}/b.txt"),
                &format!("{root}/c.txt"),
            ],
        );
        let params = json!({"pattern": "hit", "enum": "grep-enum", "head_limit": 2});
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 2);
        assert_eq!(result["truncated"], true);
    }
}
