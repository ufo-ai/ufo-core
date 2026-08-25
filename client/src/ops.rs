//! Executing the ops a `run` directive asks of this terminal.

mod exec;
pub mod fileops;

use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::config::Home;
#[cfg(unix)]
use crate::guard;
use crate::wire::{OpRequest, Session};

pub const OP_EXEC: &str = "exec";
pub const OP_WRITE: &str = "write";
pub const OP_READ: &str = "read";
pub const OP_FILE: &str = "fileop";
pub const OP_SYSTEM_SKILLS: &str = "system-skills";

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
    pub home: Home,
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
        OP_SYSTEM_SKILLS => crate::system_skills::load(&rt.home, &rt.cwd, &op.params),
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

    fn runtime(name: &str) -> OpRuntime {
        let root = std::env::temp_dir().join(format!("ufo-op-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        OpRuntime {
            workdir: root.clone(),
            cwd: root.clone(),
            home: Home {
                root: root.join("home"),
            },
        }
    }

    fn offline_session() -> Session {
        Session::new(
            "http://127.0.0.1:1".into(),
            Some("http://127.0.0.1:1".into()),
            "abc".into(),
            None,
            "sid".into(),
            None,
            false,
            false,
        )
    }

    fn asking(kind: &str, arg: &str, params: &str) -> OpRequest {
        OpRequest {
            op_id: "op1".into(),
            kind: kind.into(),
            name: "read".into(),
            timeout_s: 30,
            arg: arg.into(),
            params: params.into(),
        }
    }

    #[test]
    fn an_unknown_kind_is_refused_by_name() {
        let rt = runtime("unknown");
        let outcome = run_op(&rt, &offline_session(), &asking("telepathy", "", "{}"));
        assert_eq!(outcome, Err("unknown op kind: telepathy".to_string()));
        let _ = fs::remove_dir_all(&rt.workdir);
    }

    #[test]
    fn a_fileop_whose_params_are_not_json_says_so() {
        let rt = runtime("badparams");
        let outcome = run_op(&rt, &offline_session(), &asking(OP_FILE, "", "not json"));
        let failure = outcome.expect_err("params that are not JSON are refused");
        assert!(
            failure.starts_with("fileop failed: params are not JSON"),
            "{failure}"
        );
        let _ = fs::remove_dir_all(&rt.workdir);
    }

    #[test]
    fn system_skills_load_from_ufo_home_without_an_exec() {
        let rt = runtime("system-skills");
        let object = "308c9d25ca09c876af5002d9e89c5b82abd64748c78cc0fc1eeeae882f7e5f88";
        let bundle = "f".repeat(64);
        let skills = rt.home.root.join("skills");
        let source = skills
            .join("bundles")
            .join(&bundle)
            .join("objects")
            .join(object);
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("SKILL.md"), b"workflow").unwrap();
        fs::write(skills.join("current"), format!("\"sha256:{bundle}\"\n")).unwrap();
        let asked = asking(
            OP_SYSTEM_SKILLS,
            "",
            &format!(r#"{{"probe":"sha256:{object}"}}"#),
        );

        let reply = run_op(&rt, &offline_session(), &asked).unwrap();

        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&reply).unwrap(),
            serde_json::json!({"mounted": ["probe"]})
        );
        assert_eq!(
            fs::read(rt.cwd.join(".skills/probe/SKILL.md")).unwrap(),
            b"workflow"
        );
        let _ = fs::remove_dir_all(&rt.workdir);
    }

    #[test]
    fn a_read_op_answers_the_bytes_at_its_path() {
        let rt = runtime("read");
        let path = rt.workdir.join("answer.txt");
        fs::write(&path, b"payload").unwrap();
        let asked = asking(OP_READ, path.to_str().unwrap(), "");
        assert_eq!(
            run_op(&rt, &offline_session(), &asked),
            Ok(b"payload".to_vec())
        );
        let _ = fs::remove_dir_all(&rt.workdir);
    }

    #[test]
    fn a_read_op_names_the_path_it_could_not_find() {
        let rt = runtime("readmissing");
        let missing = rt.workdir.join("gone.txt");
        let asked = asking(OP_READ, missing.to_str().unwrap(), "");
        assert_eq!(
            run_op(&rt, &offline_session(), &asked),
            Err(format!("ENOENT: {}", missing.display()))
        );
        let _ = fs::remove_dir_all(&rt.workdir);
    }

    #[test]
    fn a_write_op_that_cannot_land_says_which_path() {
        let rt = runtime("write");
        let blocker = rt.workdir.join("afile");
        fs::write(&blocker, b"not a directory").unwrap();
        let target = blocker.join("under-a-file.txt");
        let asked = asking(OP_WRITE, target.to_str().unwrap(), "");
        assert_eq!(
            run_op(&rt, &offline_session(), &asked),
            Err(format!("EIO: could not write {}", target.display()))
        );
        let _ = fs::remove_dir_all(&rt.workdir);
    }
}
