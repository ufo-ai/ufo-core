//! Executing the ops a `run` directive asks of this terminal.

mod exec;
pub mod fileops;

use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

#[cfg(unix)]
use crate::guard;
use crate::wire::{OpRequest, Session};

pub const OP_EXEC: &str = "exec";
pub const OP_WRITE: &str = "write";
pub const OP_READ: &str = "read";
pub const OP_FILE: &str = "fileop";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReadBackParams {
    max_bytes: Option<u64>,
    workspace: Option<String>,
}

/// Session-stable op state: the scratch workdir walk enumerations land in and the directory the
/// member launched from.
pub struct OpRuntime {
    pub workdir: PathBuf,
    pub cwd: PathBuf,
}

/// Run one op and answer its reply body, or the failure string carried in `x-ufo-op-err`.
pub fn run_op(rt: &OpRuntime, session: &Session, op: &OpRequest) -> Result<Vec<u8>, String> {
    match op.kind.as_str() {
        OP_EXEC => exec::run(&op.params, &rt.workdir, &rt.cwd, op.timeout_s),
        OP_WRITE => landed(session, op)
            .map(|_| Vec::new())
            .map_err(|_| format!("EIO: could not write {}", op.arg)),
        OP_READ => {
            let params = if op.params.is_empty() {
                ReadBackParams {
                    max_bytes: None,
                    workspace: None,
                }
            } else {
                serde_json::from_str::<ReadBackParams>(&op.params)
                    .map_err(|error| format!("read failed: params are not JSON: {error}"))?
            };
            read_back(&op.arg, params.max_bytes, params.workspace.as_deref())
        }
        OP_FILE => {
            let params: serde_json::Value = serde_json::from_str(&op.params)
                .map_err(|error| format!("fileop failed: params are not JSON: {error}"))?;
            fileops::run(&op.name, &params, &rt.workdir).map_err(|error| {
                if error.starts_with("ENOENT: no bundled program") {
                    error
                } else {
                    format!("fileop failed: {error}")
                }
            })
        }
        other => Err(format!("unknown op kind: {other}")),
    }
}

fn landed(session: &Session, op: &OpRequest) -> Result<(), String> {
    let target = Path::new(&op.arg);
    let parent = match target.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("/"),
    };
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let staged = parent.join(format!(".ufo-staged-{}", op.op_id));
    let outcome = session
        .fetch_staged(&op.op_id, &staged)
        .and_then(|_| fs::rename(&staged, target).map_err(|error| error.to_string()));
    if outcome.is_err() {
        let _ = fs::remove_file(&staged);
    }
    outcome
}

fn read_back(
    path: &str,
    max_bytes: Option<u64>,
    workspace: Option<&str>,
) -> Result<Vec<u8>, String> {
    #[cfg(unix)]
    if let Some(workspace) = workspace {
        let root = guard::contained_root(Path::new(workspace))
            .map_err(|error| format!("EACCES: {}", error.message()))?;
        let target = guard::contained_file(path, &root, false)
            .map_err(|error| format!("EACCES: {}", error.message()))?;
        let entry = target
            .lstat()
            .map_err(|error| format!("EACCES: {}", error.message()))?
            .ok_or_else(|| format!("ENOENT: {path}"))?;
        if max_bytes.is_some_and(|limit| entry.size > limit) {
            return Err(format!("EFBIG: {path}"));
        }
        return target
            .read_bytes(entry.size)
            .map_err(|error| format!("EIO: {}", error.message()));
    }
    let source = Path::new(path);
    if !source.is_file() {
        return Err(format!("ENOENT: {path}"));
    }
    if max_bytes.is_some_and(|limit| source.metadata().is_ok_and(|meta| meta.len() > limit)) {
        return Err(format!("EFBIG: {path}"));
    }
    fs::read(source).map_err(|error| format!("EIO: could not read {path}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_back_answers_the_file() {
        let path = std::env::temp_dir().join(format!("ufo-read-test-{}", std::process::id()));
        fs::write(&path, b"payload").unwrap();
        assert_eq!(
            read_back(path.to_str().unwrap(), None, None).unwrap(),
            b"payload"
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn read_back_names_a_missing_file() {
        let missing = "/nonexistent/ufo-read-test";
        assert_eq!(
            read_back(missing, None, None).unwrap_err(),
            format!("ENOENT: {missing}")
        );
    }

    #[test]
    fn read_back_applies_the_callers_byte_cap_before_reading() {
        let path = std::env::temp_dir().join(format!("ufo-read-cap-{}", std::process::id()));
        fs::write(&path, b"payload").unwrap();
        assert_eq!(
            read_back(path.to_str().unwrap(), Some(3), None).unwrap_err(),
            format!("EFBIG: {}", path.display())
        );
        let _ = fs::remove_file(path);
    }

    #[cfg(unix)]
    #[test]
    fn bounded_read_refuses_a_symlink_before_opening_document_bytes() {
        let root = std::env::temp_dir().join(format!("ufo-read-contained-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let outside = root.with_extension("outside");
        fs::write(&outside, b"outside").unwrap();
        std::os::unix::fs::symlink(&outside, root.join("report.pdf")).unwrap();
        let error = read_back(
            root.join("report.pdf").to_str().unwrap(),
            Some(1024),
            Some(root.to_str().unwrap()),
        )
        .unwrap_err();
        assert!(error.starts_with("EACCES:"), "{error}");
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_file(outside);
    }
}
