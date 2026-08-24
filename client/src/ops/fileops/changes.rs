use std::collections::HashMap;
use std::path::Path;

use serde_json::json;

#[cfg(unix)]
use crate::guard;
#[cfg(unix)]
use crate::ops::fileops::text::guarded;
use crate::ops::fileops::text::{
    binary_extension, capped, decode_lossy, failed, listing, over, points, str_param, suffix,
    OpError, OpResult,
};
#[cfg(unix)]
use crate::ops::fileops::walk;

const CHANGES_INPUT_MAX_CHARS: usize = 20000;
const CHANGES_INPUT_MAX_LINES: usize = 2000;
const CHANGES_PATCH_MAX_CHARS: usize = 10000;
const CHANGES_MAX_REPOSITORIES: usize = 10;
const CHANGES_MAX_FILES: usize = 100;
const CHANGES_TOTAL_MAX_CHARS: i64 = 250000;
const UNTRACKED_STATUS: &str = "??";
const STATUS_ENTRY_MIN: usize = 3;
const REPOSITORY_FIELD: &str = "R";
const DIFF_FIELD: &str = "D";
const DIFF_SECTION: &str = "\ndiff --git ";
const OLD_HEADER: &str = "--- ";
const NEW_HEADER: &str = "+++ ";
const NO_DEVICE: &str = "/dev/null";
const NO_NEWLINE: &str = "\n\\ No newline at end of file\n";

struct Checkout {
    path: String,
    entries: Vec<String>,
    diff: String,
}

struct Change {
    patch: String,
    truncated: bool,
}

fn split_lines(text: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let mut start = 0;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut index = 0;
    while index < chars.len() {
        let (position, character) = chars[index];
        let boundary = matches!(
            character,
            '\n' | '\r'
                | '\u{b}'
                | '\u{c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if boundary {
            let mut end = position + character.len_utf8();
            if character == '\r' && chars.get(index + 1).map(|next| next.1) == Some('\n') {
                end += 1;
                index += 1;
            }
            lines.push(text[start..end].to_string());
            start = end;
        }
        index += 1;
    }
    if start < text.len() {
        lines.push(text[start..].to_string());
    }
    lines
}

fn checkouts(items: &[String]) -> Result<Vec<Checkout>, OpError> {
    let mut found: Vec<Checkout> = Vec::new();
    let mut index = 0;
    while index < items.len() {
        if items[index] == REPOSITORY_FIELD {
            let path = items
                .get(index + 1)
                .ok_or_else(|| failed("malformed changes listing"))?;
            found.push(Checkout {
                path: path.clone(),
                entries: Vec::new(),
                diff: String::new(),
            });
            index += 1;
        } else if items[index] == DIFF_FIELD {
            let diff = items
                .get(index + 1)
                .ok_or_else(|| failed("malformed changes listing"))?;
            found
                .last_mut()
                .ok_or_else(|| failed("malformed changes listing"))?
                .diff = diff.clone();
            index += 1;
        } else if points(&items[index]) > STATUS_ENTRY_MIN {
            found
                .last_mut()
                .ok_or_else(|| failed("malformed changes listing"))?
                .entries
                .push(items[index].clone());
        }
        index += 1;
    }
    let paths: Vec<String> = found.iter().map(|checkout| checkout.path.clone()).collect();
    let mut kept = Vec::new();
    for checkout in found {
        let nested = paths.iter().any(|outer| {
            *outer != checkout.path && checkout.path.starts_with(&format!("{outer}/"))
        });
        if nested {
            continue;
        }
        kept.push(checkout);
        if kept.len() >= CHANGES_MAX_REPOSITORIES {
            break;
        }
    }
    Ok(kept)
}

fn git_escape(character: char) -> Option<u8> {
    match character {
        'a' => Some(7),
        'b' => Some(8),
        'f' => Some(12),
        'n' => Some(10),
        'r' => Some(13),
        't' => Some(9),
        'v' => Some(11),
        '\\' => Some(92),
        '"' => Some(34),
        _ => None,
    }
}

fn unquote(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    let mut bytes = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] != '\\' {
            bytes.push((chars[index] as u32 & 0xff) as u8);
            index += 1;
        } else if chars
            .get(index + 1)
            .is_some_and(|next| ('0'..='7').contains(next))
        {
            let digits: String = chars[index + 1..(index + 4).min(chars.len())]
                .iter()
                .collect();
            bytes.push((u32::from_str_radix(&digits, 8).unwrap_or(0) & 0xff) as u8);
            index += 4;
        } else {
            if let Some(next) = chars.get(index + 1) {
                bytes.push(git_escape(*next).unwrap_or((*next as u32 & 0xff) as u8))
            }
            index += 2;
        }
    }
    decode_lossy(&bytes)
}

fn diff_path(line: &str) -> Option<String> {
    let mut value: &str = line.get(4..).unwrap_or("");
    if let Some(stripped) = value.strip_suffix('\t') {
        value = stripped;
    }
    if value == NO_DEVICE {
        return None;
    }
    let mut owned = value.to_string();
    if owned.starts_with('"') && owned.ends_with('"') && owned.len() >= 2 {
        owned = unquote(&owned[1..owned.len() - 1]);
    }
    if owned.starts_with("a/") || owned.starts_with("b/") {
        owned = owned[2..].to_string();
    }
    Some(owned)
}

fn tracked_patches(diff: &str) -> HashMap<String, String> {
    let mut patches = HashMap::new();
    for section in diff.split(DIFF_SECTION) {
        let lines: Vec<&str> = section.split('\n').collect();
        let Some(header) = lines.iter().position(|line| line.starts_with(OLD_HEADER)) else {
            continue;
        };
        if !lines
            .get(header + 1)
            .is_some_and(|line| line.starts_with(NEW_HEADER))
        {
            continue;
        }
        let body = lines[header..].join("\n");
        let patch = if body.ends_with('\n') {
            body
        } else {
            format!("{body}\n")
        };
        for line in &lines[header..(header + 2).min(lines.len())] {
            if let Some(name) = diff_path(line) {
                patches.insert(name, patch.clone());
            }
        }
    }
    patches
}

fn added_text(path: &str) -> Option<String> {
    if binary_extension(&suffix(path)) {
        return None;
    }
    let bytes = std::fs::read(path).ok()?;
    Some(decode_lossy(
        &bytes[..bytes.len().min(CHANGES_INPUT_MAX_CHARS + 1)],
    ))
}

fn added_patch(name: &str, text: &str) -> Change {
    let mut lines = split_lines(&capped(text, CHANGES_INPUT_MAX_CHARS));
    let mut truncated = over(text, CHANGES_INPUT_MAX_CHARS);
    if lines.len() > CHANGES_INPUT_MAX_LINES {
        lines.truncate(CHANGES_INPUT_MAX_LINES);
        truncated = true;
    }
    let mut patch = String::new();
    if !lines.is_empty() {
        let span = if lines.len() == 1 {
            "1".to_string()
        } else {
            format!("1,{}", lines.len())
        };
        patch = format!("{OLD_HEADER}{NO_DEVICE}\n{NEW_HEADER}b/{name}\n@@ -0,0 +{span} @@\n");
        for line in &lines {
            patch.push('+');
            patch.push_str(line);
            if !line.ends_with('\n') {
                patch.push_str(NO_NEWLINE);
            }
        }
    }
    Change {
        truncated: truncated || over(&patch, CHANGES_PATCH_MAX_CHARS),
        patch: capped(&patch, CHANGES_PATCH_MAX_CHARS),
    }
}

fn repository_change(
    path: &str,
    status: &str,
    name: &str,
    patches: &HashMap<String, String>,
    read: &dyn Fn(&str) -> Option<String>,
) -> Change {
    if let Some(patch) = patches.get(name) {
        return Change {
            truncated: over(patch, CHANGES_PATCH_MAX_CHARS),
            patch: capped(patch, CHANGES_PATCH_MAX_CHARS),
        };
    }
    let added = status == UNTRACKED_STATUS || status.starts_with('A');
    let text = if added { read(path) } else { None };
    match text {
        None => Change {
            patch: String::new(),
            truncated: false,
        },
        Some(text) => added_patch(name, &text),
    }
}

fn relative_to<'a>(path: &'a str, root: &str) -> &'a str {
    let rest = path.get(root.len()..).unwrap_or("");
    rest.strip_prefix('/').unwrap_or(rest)
}

fn joined(base: &str, name: &str) -> String {
    let combined = format!("{base}/{name}");
    let components: Vec<&str> = combined
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    format!(
        "{}{}",
        if base.starts_with('/') { "/" } else { "" },
        components.join("/")
    )
}

fn entry_fields(entry: &str) -> (String, String) {
    let status: String = entry.chars().take(2).collect();
    let name = entry
        .char_indices()
        .nth(3)
        .map(|(index, _)| entry[index..].to_string())
        .unwrap_or_default();
    (status, name)
}

/// One file git holds no HEAD version of, read off a pinned parent fd and bounded by the cap every
/// other diff input takes.
#[cfg(unix)]
fn guarded_added_text(path: &str, workspace: &Path) -> Option<String> {
    if binary_extension(&suffix(path)) {
        return None;
    }
    let target = guard::contained_file(path, workspace, false).ok()?;
    target.lstat().ok()??;
    let bytes = target.read_bytes(CHANGES_INPUT_MAX_CHARS as u64 + 1).ok()?;
    Some(decode_lossy(&bytes))
}

#[cfg(unix)]
fn git(repository: &Path, args: &[&str]) -> String {
    let outcome = std::process::Command::new("git")
        .arg("-C")
        .arg(repository)
        .args(["-c", "core.quotePath=false"])
        .args(args)
        .output();
    match outcome {
        Ok(done) if done.status.success() => String::from_utf8_lossy(&done.stdout).into_owned(),
        _ => String::new(),
    }
}

/// The listing this op builds when no caller supplied one: every checkout under the workspace and
/// git's own answer for each, in the record shape the host's shell program writes, so one reader
/// serves both. git is the only thing that knows what a change is here — it counts what any writer
/// did, reports deletions, and counts nothing outside a checkout.
#[cfg(unix)]
fn walked_items(root: &Path) -> Vec<String> {
    let mut items = Vec::new();
    for repository in walk::checkouts(root) {
        items.push(REPOSITORY_FIELD.to_string());
        items.push(repository.to_string_lossy().into_owned());
        let status = git(
            &repository,
            &["status", "--porcelain=v1", "-z", "--no-renames", "-uall"],
        );
        items.extend(
            status
                .split('\0')
                .filter(|entry| !entry.is_empty())
                .map(str::to_string),
        );
        let diff = git(&repository, &["diff", "HEAD", "--no-renames", "-U3"]);
        items.push(DIFF_FIELD.to_string());
        items.push(diff.trim_end_matches('\n').to_string());
    }
    items
}

/// The same scan with its own walk: the workspace root goes through the guard by descent, the
/// checkouts under it are found by `sbxfs`'s own rule, and a file with no HEAD version is read off a
/// pinned parent fd. A `paths` param is accepted and ignored, as `sbxfs` accepts and ignores it: the
/// answer is every uncommitted change under the workspace.
#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, workspace: &Path, workdir: &Path) -> OpResult {
    let root = guarded(guard::contained_dir(
        &workspace.to_string_lossy(),
        workspace,
        false,
    ))?;
    let items = match params.get("enum").and_then(|value| value.as_str()) {
        Some(name) => listing(workdir, name)?,
        None => walked_items(&root),
    };
    assemble(&items, &root.to_string_lossy(), &|path| {
        guarded_added_text(path, workspace)
    })
}

pub fn run(params: &serde_json::Value, workdir: &Path) -> OpResult {
    let workspace = str_param(params, "workspace")?;
    let items = listing(workdir, str_param(params, "enum")?)?;
    assemble(&items, workspace, &added_text)
}

fn assemble(items: &[String], workspace: &str, read: &dyn Fn(&str) -> Option<String>) -> OpResult {
    let mut listed = Vec::new();
    let mut budget = CHANGES_TOTAL_MAX_CHARS;
    for repository in checkouts(items)? {
        let prefix = relative_to(&repository.path, workspace).to_string();
        let patches = tracked_patches(&repository.diff);
        for entry in &repository.entries {
            if listed.len() >= CHANGES_MAX_FILES || budget <= 0 {
                return Ok(json!({"changes": listed, "truncated": true}));
            }
            let (status, name) = entry_fields(entry);
            let change = repository_change(
                &joined(&repository.path, &name),
                &status,
                &name,
                &patches,
                read,
            );
            budget -= points(&change.patch) as i64;
            listed.push(json!({
                "path": joined(&prefix, &name),
                "patch": change.patch,
                "truncated": change.truncated,
            }));
        }
    }
    Ok(json!({"changes": listed, "truncated": false}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_lines_keeps_terminators() {
        assert_eq!(split_lines("a\r\nb\nc"), vec!["a\r\n", "b\n", "c"]);
        assert_eq!(
            split_lines("x\u{2028}y\u{85}"),
            vec!["x\u{2028}", "y\u{85}"]
        );
        assert_eq!(split_lines(""), Vec::<String>::new());
    }

    #[test]
    fn unquote_reverses_git_escaping() {
        assert_eq!(unquote("a\\tb"), "a\tb");
        assert_eq!(unquote("caf\\303\\251"), "café");
        assert_eq!(unquote("q\\\"e"), "q\"e");
    }

    #[test]
    fn diff_path_strips_prefixes_and_null() {
        assert_eq!(diff_path("--- a/src/x.rs"), Some("src/x.rs".to_string()));
        assert_eq!(diff_path("+++ b/src/x.rs"), Some("src/x.rs".to_string()));
        assert_eq!(diff_path("--- /dev/null"), None);
        assert_eq!(
            diff_path("+++ \"b/sp ace\\303\\251\""),
            Some("sp aceé".to_string())
        );
    }

    #[test]
    fn added_patch_marks_missing_newline() {
        let change = added_patch("f.txt", "one\ntwo");
        assert_eq!(
            change.patch,
            "--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n\\ No newline at end of file\n"
        );
        assert!(!change.truncated);
    }

    #[test]
    fn nested_repositories_fold_into_outer() {
        let items = vec![
            "R".to_string(),
            "/ws/outer".to_string(),
            "?? a.txt".to_string(),
            "D".to_string(),
            String::new(),
            "R".to_string(),
            "/ws/outer/inner".to_string(),
            "?? b.txt".to_string(),
            "D".to_string(),
            String::new(),
        ];
        let kept = checkouts(&items).unwrap();
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0].path, "/ws/outer");
        assert_eq!(kept[0].entries, vec!["?? a.txt"]);
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-changes-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn git(dir: &Path, args: &[&str]) -> String {
        let output = std::process::Command::new("git")
            .args(args)
            .current_dir(dir)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_SYSTEM", "/dev/null")
            .output()
            .expect("git runs");
        assert!(output.status.success(), "git {args:?}: {:?}", output);
        String::from_utf8_lossy(&output.stdout).into_owned()
    }

    #[test]
    fn end_to_end_over_a_real_repository() {
        if std::process::Command::new("git")
            .arg("--version")
            .output()
            .is_err()
        {
            return;
        }
        let workspace = scratch("e2e");
        let repo = workspace.join("proj");
        std::fs::create_dir_all(&repo).unwrap();
        git(&repo, &["init", "-q"]);
        git(&repo, &["config", "user.email", "t@t"]);
        git(&repo, &["config", "user.name", "t"]);
        std::fs::write(repo.join("tracked.txt"), "old line\n").unwrap();
        git(&repo, &["add", "."]);
        git(&repo, &["commit", "-qm", "seed"]);
        std::fs::write(repo.join("tracked.txt"), "new line\n").unwrap();
        std::fs::write(repo.join("fresh.txt"), "born\n").unwrap();

        let status = git(
            &repo,
            &[
                "-c",
                "core.quotePath=false",
                "status",
                "--porcelain=v1",
                "-z",
                "--no-renames",
                "-uall",
            ],
        );
        let diff = git(
            &repo,
            &[
                "-c",
                "core.quotePath=false",
                "diff",
                "HEAD",
                "--no-renames",
                "-U3",
            ],
        );
        let root = repo.to_str().unwrap();
        let mut body = format!("R\0{root}\0");
        body.push_str(&status);
        body.push_str(&format!("D\0{}\0", diff.trim_end_matches('\n')));
        let workdir = scratch("e2e-work");
        std::fs::write(workdir.join("changes-enum"), body).unwrap();

        let params = json!({"workspace": workspace.to_str().unwrap(), "enum": "changes-enum"});
        let result = run(&params, &workdir).unwrap();
        assert_eq!(result["truncated"], false);
        let changes = result["changes"].as_array().unwrap();
        assert_eq!(changes.len(), 2);
        let by_path: HashMap<&str, &serde_json::Value> = changes
            .iter()
            .map(|change| (change["path"].as_str().unwrap(), change))
            .collect();
        let tracked = by_path["proj/tracked.txt"];
        assert!(tracked["patch"].as_str().unwrap().contains("-old line"));
        assert!(tracked["patch"].as_str().unwrap().contains("+new line"));
        let fresh = by_path["proj/fresh.txt"];
        assert!(fresh["patch"].as_str().unwrap().contains("+born\n"));
        assert!(fresh["patch"]
            .as_str()
            .unwrap()
            .starts_with("--- /dev/null"));
    }
}
