#[cfg(unix)]
use std::path::Path;

use serde_json::json;

#[cfg(unix)]
use crate::guard;
#[cfg(unix)]
use crate::ops::fileops::lock;
#[cfg(unix)]
use crate::ops::fileops::text::{failed, guarded, required};
use crate::ops::fileops::text::{refused, str_param, OpError, OpResult};

fn normalized(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." && parts.last().is_some_and(|last| *last != "..") {
            parts.pop();
        } else {
            parts.push(part);
        }
    }
    format!(
        "{}{}",
        if path.starts_with('/') { "/" } else { "" },
        parts.join("/")
    )
}

fn make_parents(path: &str) {
    if let Some(cut) = path.rfind('/') {
        if cut > 0 {
            let _ = std::fs::create_dir_all(&path[..cut]);
        }
    }
}

#[cfg(unix)]
fn stat_mode(path: &str) -> Option<u32> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::symlink_metadata(path)
        .ok()
        .map(|found| found.permissions().mode() & 0o777)
}

#[cfg(not(unix))]
fn stat_mode(path: &str) -> Option<u32> {
    std::fs::symlink_metadata(path).ok().map(|_| 0)
}

#[cfg(unix)]
fn set_mode(path: &str, mode: u32) -> Result<(), OpError> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode & 0o777))
        .map_err(|_| failed(format!("Could not set mode on {path}")))
}

#[cfg(not(unix))]
fn set_mode(_path: &str, _mode: u32) -> Result<(), OpError> {
    Ok(())
}

fn landed(target: &str, staged: &str, allow_existing: bool) -> OpResult {
    let existing = stat_mode(target);
    let outcome = (|| {
        if existing.is_some() && !allow_existing {
            return Err(refused(format!(
                "file {target} must be read before it is written"
            )));
        }
        if stat_mode(staged).is_none() {
            return Err(refused(format!("{staged} not found")));
        }
        if let Some(mode) = existing {
            set_mode(staged, mode)?;
        }
        std::fs::rename(staged, target)
            .map_err(|_| refused(format!("Could not rename {staged} onto {target}")))?;
        Ok(json!({"created": existing.is_none()}))
    })();
    let _ = std::fs::remove_file(staged);
    outcome
}

fn permitted(params: &serde_json::Value) -> bool {
    params.get("allow_existing") == Some(&serde_json::Value::Bool(true))
}

/// The read-before-write check and the rename run under the target's cross-process lock, so a second
/// writer in another process waits rather than landing between them.
#[cfg(unix)]
pub fn run_contained(params: &serde_json::Value, root: &Path) -> OpResult {
    let target_param = required(params, "path")?;
    let staged_param = required(params, "staged_path")?;
    let target = guarded(guard::contained_file(target_param, root, true))?;
    let staged = guarded(guard::contained_file(staged_param, root, false))?;
    if staged.path() == target.path() {
        return Err(refused("staged file must differ from its target"));
    }
    let outcome = (|| {
        let _held = lock::exclusive(target.path())?;
        let existing = guarded(target.lstat())?;
        if existing.is_some() && !permitted(params) {
            return Err(refused(format!(
                "file {} must be read before it is written",
                target.path().display()
            )));
        }
        if guarded(staged.lstat())?.is_none() {
            return Err(refused(format!("{} not found", staged.path().display())));
        }
        if let Some(entry) = existing {
            guarded(staged.chmod(entry.mode))?;
        }
        guarded(target.replace_with(&staged))?;
        Ok(json!({"created": existing.is_none()}))
    })();
    staged.unlink();
    outcome
}

pub fn run(params: &serde_json::Value) -> OpResult {
    let target = str_param(params, "path")?;
    let staged = str_param(params, "staged_path")?;
    make_parents(target);
    if normalized(staged) == normalized(target) {
        return Err(refused("staged file must differ from its target"));
    }
    landed(target, staged, permitted(params))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-write-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn lands_new_file_and_makes_parents() {
        let dir = scratch("new");
        let staged = dir.join("staged");
        std::fs::write(&staged, "body").unwrap();
        let target = dir.join("deep/nest/out.txt");
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": staged.to_str().unwrap(),
        });
        let result = run(&params).unwrap();
        assert_eq!(result["created"], true);
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "body");
        assert!(!staged.exists());
    }

    #[test]
    fn refuses_unread_overwrite_and_unlinks_staged() {
        let dir = scratch("unread");
        let staged = dir.join("staged");
        std::fs::write(&staged, "new").unwrap();
        let target = dir.join("out.txt");
        std::fs::write(&target, "old").unwrap();
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": staged.to_str().unwrap(),
        });
        match run(&params) {
            Err(OpError::Refused(message)) => assert_eq!(
                message,
                format!(
                    "file {} must be read before it is written",
                    target.to_str().unwrap()
                )
            ),
            _ => panic!("expected refusal"),
        }
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "old");
        assert!(!staged.exists());
    }

    #[cfg(unix)]
    #[test]
    fn overwrite_preserves_mode() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("mode");
        let staged = dir.join("staged");
        std::fs::write(&staged, "new").unwrap();
        let target = dir.join("run.sh");
        std::fs::write(&target, "old").unwrap();
        std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o755)).unwrap();
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": staged.to_str().unwrap(),
            "allow_existing": true,
        });
        let result = run(&params).unwrap();
        assert_eq!(result["created"], false);
        let mode = std::fs::metadata(&target).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o755);
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "new");
    }

    #[test]
    fn same_path_refuses_and_keeps_the_file() {
        let dir = scratch("same");
        let target = dir.join("keep.txt");
        std::fs::write(&target, "body").unwrap();
        let doubled = format!("{}/./keep.txt", dir.to_str().unwrap());
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": doubled,
        });
        match run(&params) {
            Err(OpError::Refused(message)) => {
                assert_eq!(message, "staged file must differ from its target")
            }
            _ => panic!("expected refusal"),
        }
        assert!(target.exists());
    }

    #[test]
    fn missing_staged_refuses() {
        let dir = scratch("missing");
        let target = dir.join("out.txt");
        let ghost = dir.join("ghost");
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": ghost.to_str().unwrap(),
        });
        match run(&params) {
            Err(OpError::Refused(message)) => {
                assert_eq!(message, format!("{} not found", ghost.to_str().unwrap()))
            }
            _ => panic!("expected refusal"),
        }
    }

    /// `flock` is held per open file description, so a second `exclusive` call in this process contends
    /// exactly as a second process does.
    #[cfg(unix)]
    #[test]
    fn a_write_waits_for_the_shared_filesystem_lock() {
        use crate::ops::fileops::lock;
        let root = std::fs::canonicalize(scratch("locked")).unwrap();
        let target = root.join("shared.txt");
        std::fs::write(&target, "old\n").unwrap();
        let staged = root.join("staged");
        std::fs::write(&staged, "new\n").unwrap();
        let params = json!({
            "path": target.to_str().unwrap(),
            "staged_path": staged.to_str().unwrap(),
            "allow_existing": true,
        });
        let held = lock::exclusive(&target).unwrap();
        let writing = std::thread::spawn({
            let root = root.clone();
            move || run_contained(&params, &root)
        });
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(
            !writing.is_finished(),
            "the write did not wait for the lock"
        );
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "old\n");
        drop(held);
        let result = writing.join().unwrap().unwrap();
        assert_eq!(result["created"], false);
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "new\n");
        assert!(!staged.exists());
    }

    #[test]
    fn normalized_collapses_dots() {
        assert_eq!(normalized("/a/./b/../c"), "/a/c");
        assert_eq!(normalized("a//b/"), "a/b");
        assert_eq!(normalized("../../x"), "../../x");
    }
}
