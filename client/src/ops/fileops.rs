
mod changes;
mod fs_edit;
mod fs_glob;
mod fs_grep;
pub mod fs_read;
mod fs_write;
#[cfg(unix)]
mod lock;
mod text;
#[cfg(unix)]
mod walk;

use std::path::Path;

#[cfg(unix)]
use crate::guard;
pub use text::OpError;
#[cfg(unix)]
pub use text::OpResult;
#[cfg(unix)]
use text::{guarded, required};

#[cfg(unix)]
pub const OPS: [&str; 6] = ["read", "write", "edit", "grep", "glob", "changes"];

pub fn run(name: &str, params: &serde_json::Value, workdir: &Path) -> Result<Vec<u8>, String> {
    let outcome = match name {
        "read" => fs_read::run(params),
        "edit" => fs_edit::run(params),
        "write" => fs_write::run(params),
        "grep" => fs_grep::run(params, workdir),
        "glob" => fs_glob::run(params, workdir),
        "changes" => changes::run(params, workdir),
        _ => return Err(format!("ENOENT: no bundled program for {name}")),
    };
    match outcome {
        Ok(result) => Ok(reply(&result)),
        Err(OpError::Refused(message)) => Ok(reply(&serde_json::json!({"error": message}))),
        Err(OpError::Failed(message)) => Err(message),
    }
}

#[cfg(unix)]
pub fn run_contained(name: &str, params: &serde_json::Value, workdir: &Path) -> OpResult {
    if !OPS.contains(&name) {
        return Err(OpError::Failed(format!(
            "ENOENT: no bundled program for {name}"
        )));
    }
    let workspace = guarded(guard::contained_root(Path::new(required(
        params,
        "workspace",
    )?)))?;
    match name {
        "read" => fs_read::run_contained(params, &workspace),
        "edit" => fs_edit::run_contained(params, &workspace),
        "write" => fs_write::run_contained(params, &workspace),
        "grep" => fs_grep::run_contained(params, &workspace, workdir),
        "glob" => fs_glob::run_contained(params, &workspace, workdir),
        _ => changes::run_contained(params, &workspace, workdir),
    }
}

fn reply(result: &serde_json::Value) -> Vec<u8> {
    serde_json::to_vec(result).expect("op results serialize")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-fileops-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn refusals_ride_the_reply_as_error_json() {
        let workdir = scratch("refusal");
        let reply = run("read", &json!({"path": "/absent/file"}), &workdir).unwrap();
        let parsed: serde_json::Value = serde_json::from_slice(&reply).unwrap();
        assert_eq!(parsed["error"], "/absent/file not found");
    }

    #[test]
    fn unknown_program_matches_the_shell_message() {
        let workdir = scratch("unknown");
        assert_eq!(
            run("mystery", &json!({}), &workdir).unwrap_err(),
            "ENOENT: no bundled program for mystery"
        );
    }

    #[test]
    fn hard_failures_surface_as_err() {
        let workdir = scratch("hard");
        let failure = run(
            "grep",
            &json!({"pattern": "x", "enum": "missing-enum"}),
            &workdir,
        )
        .unwrap_err();
        assert!(failure.contains("missing-enum"), "{failure}");
    }

    #[test]
    fn edit_then_read_round_trip() {
        use base64::Engine as _;
        let encode = |text: &str| base64::engine::general_purpose::STANDARD.encode(text.as_bytes());
        let workdir = scratch("roundtrip");
        let path = workdir.join("file.txt");
        std::fs::write(&path, "hello world\n").unwrap();
        let edited = run(
            "edit",
            &json!({
                "path": path.to_str().unwrap(),
                "edits": [{"old_string_b64": encode("world"), "new_string_b64": encode("ufo")}],
            }),
            &workdir,
        )
        .unwrap();
        let edited: serde_json::Value = serde_json::from_slice(&edited).unwrap();
        assert_eq!(edited["replacements"], 1);
        let read = run("read", &json!({"path": path.to_str().unwrap()}), &workdir).unwrap();
        let read: serde_json::Value = serde_json::from_slice(&read).unwrap();
        assert_eq!(read["content"], "1\thello ufo");
    }
}
