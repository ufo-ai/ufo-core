//! Executing the ops a `run` directive asks of this terminal.

mod exec;
mod fileops;

use std::fs;
use std::path::{Path, PathBuf};

use crate::wire::{OpRequest, Session};

pub const OP_EXEC: &str = "exec";
pub const OP_WRITE: &str = "write";
pub const OP_READ: &str = "read";
pub const OP_FILE: &str = "fileop";

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
        OP_READ => read_back(&op.arg),
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

fn read_back(path: &str) -> Result<Vec<u8>, String> {
    let source = Path::new(path);
    if !source.is_file() {
        return Err(format!("ENOENT: {path}"));
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
        assert_eq!(read_back(path.to_str().unwrap()).unwrap(), b"payload");
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn read_back_names_a_missing_file() {
        let missing = "/nonexistent/ufo-read-test";
        assert_eq!(
            read_back(missing).unwrap_err(),
            format!("ENOENT: {missing}")
        );
    }
}
