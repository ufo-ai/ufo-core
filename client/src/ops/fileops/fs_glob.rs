use std::collections::HashSet;
use std::path::Path;
#[cfg(unix)]
use std::path::PathBuf;
#[cfg(unix)]
use std::time::UNIX_EPOCH;

use serde_json::json;

#[cfg(unix)]
use crate::guard;
use crate::ops::fileops::text::{
    failed, fnmatch, matches, refused, str_param, workdir_text, OpError, OpResult,
};
#[cfg(unix)]
use crate::ops::fileops::text::{guarded, required};
#[cfg(unix)]
use crate::ops::fileops::walk;

const GLOB_MAX_RESULTS: usize = 1000;
const RECURSIVE: &str = "**";

struct Measured {
    path: String,
    size: i64,
    modified: f64,
}

enum Test {
    Recursive,
    Name(fancy_regex::Regex),
}

fn parts(path: &str) -> Vec<&str> {
    path.split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect()
}

fn relative_to<'a>(path: &'a str, root: &str) -> &'a str {
    let rest = path.get(root.len()..).unwrap_or("");
    rest.strip_prefix('/').unwrap_or(rest)
}

fn inside(path: &str, root: &str) -> bool {
    path.starts_with(&format!("{root}/"))
}

pub fn contained_pattern<'a>(pattern: &'a str, root: &str) -> Result<&'a str, OpError> {
    if parts(pattern).contains(&"..") {
        return Err(refused(format!("pattern '{pattern}' leaves {root}")));
    }
    if !pattern.starts_with('/') {
        return Ok(pattern);
    }
    if pattern != root && !inside(pattern, root) {
        return Err(refused(format!("pattern '{pattern}' escapes {root}")));
    }
    let relative = relative_to(pattern, root);
    if relative.is_empty() {
        return Err(refused(format!(
            "pattern '{pattern}' matches a directory root"
        )));
    }
    Ok(relative)
}

fn match_parts(tests: &[Test], test: usize, names: &[&str], name: usize) -> Result<bool, OpError> {
    if test == tests.len() {
        return Ok(name == names.len());
    }
    match &tests[test] {
        Test::Recursive => {
            for skip in name..=names.len() {
                if match_parts(tests, test + 1, names, skip)? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
        Test::Name(regex) => {
            if name >= names.len() {
                return Ok(false);
            }
            Ok(matches(regex, names[name])? && match_parts(tests, test + 1, names, name + 1)?)
        }
    }
}

fn glob_matcher(pattern: &str) -> Result<(Vec<Test>, bool), OpError> {
    let components = parts(pattern);
    let directory_only = components.last() == Some(&RECURSIVE);
    let tests = components
        .iter()
        .map(|part| {
            if *part == RECURSIVE {
                Ok(Test::Recursive)
            } else {
                Ok(Test::Name(fnmatch(part)?))
            }
        })
        .collect::<Result<Vec<Test>, OpError>>()?;
    Ok((tests, directory_only))
}

fn excluded(params: &serde_json::Value) -> Result<HashSet<String>, OpError> {
    let names = match params.get("exclude_names") {
        None | Some(serde_json::Value::Null) => return Ok(HashSet::new()),
        Some(serde_json::Value::Array(names)) => names,
        Some(_) => {
            return Err(refused(
                "exclude_names must be a list of path component names",
            ))
        }
    };
    let mut set = HashSet::new();
    for name in names {
        let Some(name) = name.as_str() else {
            return Err(refused(
                "exclude_names must be a list of path component names",
            ));
        };
        if name.is_empty() || name.contains('/') || name.contains('\\') {
            return Err(refused(
                "exclude_names must be a list of path component names",
            ));
        }
        set.insert(name.to_string());
    }
    Ok(set)
}

fn measured(workdir: &Path, name: &str) -> Result<Vec<Measured>, OpError> {
    let text = workdir_text(workdir, name)?;
    let items: Vec<&str> = text.split('\0').collect();
    let Some(cut) = items.iter().position(|item| item.is_empty()) else {
        return Err(refused(
            "a file left the walk between the listing and the measurement",
        ));
    };
    let paths = &items[..cut];
    let joined = items[cut + 1..].join("\0");
    let lines: Vec<&str> = joined.split('\n').filter(|line| !line.is_empty()).collect();
    if lines.len() != paths.len() {
        return Err(refused(
            "a file left the walk between the listing and the measurement",
        ));
    }
    paths
        .iter()
        .zip(lines)
        .map(|(path, line)| {
            let gap = line
                .find(' ')
                .ok_or_else(|| failed(format!("unparseable measurement line: {line}")))?;
            Ok(Measured {
                path: path.to_string(),
                size: line[..gap]
                    .parse()
                    .map_err(|_| failed(format!("unparseable measurement line: {line}")))?,
                modified: line[gap + 1..]
                    .parse()
                    .map_err(|_| failed(format!("unparseable measurement line: {line}")))?,
            })
        })
        .collect()
}

#[cfg(unix)]
fn measure(path: &Path) -> Option<Measured> {
    let found = std::fs::symlink_metadata(path).ok()?;
    let modified = found
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    Some(Measured {
        path: path.to_string_lossy().into_owned(),
        size: found.len() as i64,
        modified,
    })
}

#[cfg(unix)]
fn contained_glob<'a>(
    params: &'a serde_json::Value,
    workspace: &Path,
) -> Result<(PathBuf, &'a str), OpError> {
    let raw_pattern = required(params, "pattern")?;
    let root = workspace.to_string_lossy().into_owned();
    let pattern = contained_pattern(raw_pattern, &root)?;
    let start = match params.get("path").and_then(|value| value.as_str()) {
        Some(path) if !raw_pattern.starts_with('/') => path.to_string(),
        _ => root,
    };
    let walked = guarded(guard::contained_dir(&start, workspace, false))?;
    Ok((walked, pattern))
}

#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, workspace: &Path, workdir: &Path) -> OpResult {
    let names = excluded(params)?;
    let (walked, pattern) = contained_glob(params, workspace)?;
    let (tests, directory_only) = glob_matcher(pattern)?;
    let root = walked.to_string_lossy().into_owned();
    let supplied = params.get("enum").and_then(|value| value.as_str());
    let entries: Vec<Measured> = match supplied {
        Some(name) => measured(workdir, name)?,
        None => walk::glob_files(&walked)
            .into_iter()
            .filter_map(|hit| measure(&hit))
            .collect(),
    }
    .into_iter()
    .filter(|hit| guard::is_contained_regular(Path::new(&hit.path), workspace))
    .collect();
    selected(
        entries,
        &workspace.to_string_lossy(),
        &root,
        &tests,
        directory_only,
        &names,
    )
}

pub fn run(params: &serde_json::Value, workdir: &Path) -> OpResult {
    let workspace = str_param(params, "workspace")?;
    let raw_pattern = str_param(params, "pattern")?;
    let names = excluded(params)?;
    let pattern = contained_pattern(raw_pattern, workspace)?;
    let path = params
        .get("path")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let root = if raw_pattern.starts_with('/') || path.is_empty() {
        workspace
    } else {
        path
    };
    let (tests, directory_only) = glob_matcher(pattern)?;
    let entries = measured(workdir, str_param(params, "enum")?)?;
    selected(entries, workspace, root, &tests, directory_only, &names)
}

fn selected(
    entries: Vec<Measured>,
    workspace: &str,
    root: &str,
    tests: &[Test],
    directory_only: bool,
    names: &HashSet<String>,
) -> OpResult {
    let mut hits = Vec::new();
    for hit in entries {
        if !inside(&hit.path, root) {
            continue;
        }
        if directory_only || !match_parts(tests, 0, &parts(relative_to(&hit.path, root)), 0)? {
            continue;
        }
        if parts(relative_to(&hit.path, workspace))
            .iter()
            .any(|part| names.contains(*part))
        {
            continue;
        }
        hits.push(hit);
    }
    hits.sort_by(|left, right| {
        right
            .modified
            .partial_cmp(&left.modified)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let truncated = hits.len() > GLOB_MAX_RESULTS;
    let total = hits.len();
    let files: Vec<serde_json::Value> = hits
        .into_iter()
        .take(GLOB_MAX_RESULTS)
        .map(|hit| json!({"path": hit.path, "size": hit.size, "modified": hit.modified}))
        .collect();
    let mut result = json!({"files": files, "count": files.len(), "truncated": truncated});
    if truncated {
        result["truncated_message"] = json!(format!(
            "showing {GLOB_MAX_RESULTS} of {total} matches; refine the pattern"
        ));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-glob-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_enum(workdir: &Path, entries: &[(&str, i64, f64)]) {
        let mut body = String::new();
        for (path, _, _) in entries {
            body.push_str(path);
            body.push('\0');
        }
        body.push('\0');
        for (_, size, modified) in entries {
            body.push_str(&format!("{size} {modified:.9}\n"));
        }
        std::fs::write(workdir.join("glob-enum"), body).unwrap();
    }

    #[test]
    fn recursive_glob_sorts_by_mtime() {
        let dir = scratch("recursive");
        write_enum(
            &dir,
            &[
                ("/ws/a/x.rs", 10, 100.0),
                ("/ws/b/deep/y.rs", 20, 300.0),
                ("/ws/z.rs", 30, 200.0),
                ("/ws/skip.txt", 5, 400.0),
            ],
        );
        let params = json!({
            "pattern": "**/*.rs",
            "workspace": "/ws",
            "enum": "glob-enum",
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 3);
        assert_eq!(result["files"][0]["path"], "/ws/b/deep/y.rs");
        assert_eq!(result["files"][1]["path"], "/ws/z.rs");
        assert_eq!(result["files"][2]["path"], "/ws/a/x.rs");
        assert_eq!(result["files"][1]["size"], 30);
        assert_eq!(result["truncated"], false);
    }

    #[test]
    fn escaping_pattern_refuses() {
        let dir = scratch("escape");
        write_enum(&dir, &[]);
        for (pattern, message) in [
            ("../up/*.rs", "pattern '../up/*.rs' leaves /ws"),
            ("/other/*.rs", "pattern '/other/*.rs' escapes /ws"),
            ("/ws", "pattern '/ws' matches a directory root"),
        ] {
            let params = json!({"pattern": pattern, "workspace": "/ws", "enum": "glob-enum"});
            match run(&params, &dir) {
                Err(OpError::Refused(found)) => assert_eq!(found, message),
                _ => panic!("expected refusal for {pattern}"),
            }
        }
    }

    #[test]
    fn exclude_names_prunes_components() {
        let dir = scratch("exclude");
        write_enum(
            &dir,
            &[("/ws/keep/x.rs", 1, 1.0), ("/ws/node_modules/y.rs", 1, 2.0)],
        );
        let params = json!({
            "pattern": "**/*.rs",
            "workspace": "/ws",
            "enum": "glob-enum",
            "exclude_names": ["node_modules"],
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 1);
        assert_eq!(result["files"][0]["path"], "/ws/keep/x.rs");
        let bad = json!({
            "pattern": "*",
            "workspace": "/ws",
            "enum": "glob-enum",
            "exclude_names": ["a/b"],
        });
        assert!(matches!(run(&bad, &dir), Err(OpError::Refused(_))));
    }

    #[test]
    fn relative_root_from_path_param() {
        let dir = scratch("root");
        write_enum(&dir, &[("/ws/sub/x.md", 1, 1.0), ("/ws/x.md", 1, 2.0)]);
        let params = json!({
            "pattern": "*.md",
            "workspace": "/ws",
            "path": "/ws/sub",
            "enum": "glob-enum",
        });
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 1);
        assert_eq!(result["files"][0]["path"], "/ws/sub/x.md");
    }

    #[test]
    fn trailing_recursive_matches_nothing() {
        let dir = scratch("dironly");
        write_enum(&dir, &[("/ws/a/x.rs", 1, 1.0)]);
        let params = json!({"pattern": "a/**", "workspace": "/ws", "enum": "glob-enum"});
        let result = run(&params, &dir).unwrap();
        assert_eq!(result["count"], 0);
    }

    /// The exclusion runs before the cap, so a pruned tree cannot crowd the answer out.
    #[test]
    fn exclude_names_prune_before_the_cap() {
        let entries: Vec<Measured> = (0..GLOB_MAX_RESULTS + 1)
            .map(|index| Measured {
                path: if index % 2 == 0 {
                    format!("/ws/node_modules/pkg-{index}/file.rs")
                } else {
                    format!("/ws/src/file-{index}.rs")
                },
                size: index as i64,
                modified: index as f64,
            })
            .collect();
        let kept = entries.len() / 2;
        let (tests, directory_only) = glob_matcher("**/*.rs").unwrap();
        let names = HashSet::from(["node_modules".to_string()]);
        let result = selected(entries, "/ws", "/ws", &tests, directory_only, &names).unwrap();
        assert_eq!(result["count"], kept);
        assert_eq!(result["truncated"], false);
        assert!(result["truncated_message"].is_null());
        assert!(!result["files"]
            .as_array()
            .unwrap()
            .iter()
            .any(|file| file["path"].as_str().unwrap().contains("node_modules")));
        assert_eq!(result["files"][0]["path"], "/ws/src/file-999.rs");
    }

    #[test]
    fn the_cap_names_the_total_it_cut() {
        let entries: Vec<Measured> = (0..GLOB_MAX_RESULTS + 2)
            .map(|index| Measured {
                path: format!("/ws/file-{index}.rs"),
                size: 1,
                modified: index as f64,
            })
            .collect();
        let total = entries.len();
        let (tests, directory_only) = glob_matcher("*.rs").unwrap();
        let result = selected(
            entries,
            "/ws",
            "/ws",
            &tests,
            directory_only,
            &HashSet::new(),
        )
        .unwrap();
        assert_eq!(result["count"], GLOB_MAX_RESULTS);
        assert_eq!(result["truncated"], true);
        assert_eq!(
            result["truncated_message"],
            format!("showing {GLOB_MAX_RESULTS} of {total} matches; refine the pattern")
        );
        assert_eq!(
            result["files"][0]["path"],
            format!("/ws/file-{}.rs", total - 1)
        );
    }

    #[test]
    fn mismatched_measurement_refuses() {
        let dir = scratch("mismatch");
        std::fs::write(dir.join("glob-enum"), "/ws/a\0\0").unwrap();
        let params = json!({"pattern": "*", "workspace": "/ws", "enum": "glob-enum"});
        match run(&params, &dir) {
            Err(OpError::Refused(message)) => assert_eq!(
                message,
                "a file left the walk between the listing and the measurement"
            ),
            _ => panic!("expected refusal"),
        }
    }
}
