//! The `ufo fs` verb: `sbxfs`'s CLI contract on the client binary.
//!
//! One op name and one JSON object of params, a JSON object on stdout, and the exit code that says
//! which kind of answer it is — 0 for a result, 1 for a refusal the model can act on, 2 for a usage
//! error. The refusal strings are the API: the host maps them to the model's `ValueError` and an
//! operator dashboard groups on them.
//!
//! No stdin is read by any op. Bytes reach a sandbox by a carrier copy-in, and `write` takes a
//! `staged_path` that already holds them.

#[cfg(unix)]
use std::io::Write;
#[cfg(unix)]
use std::path::Path;

#[cfg(unix)]
use crate::ops::fileops::{self, OpError, OPS};

pub const USAGE: &str = "usage: ufo fs {read|write|edit|grep|glob|changes} <json>";

/// The verb needs `openat` and `O_NOFOLLOW` for its containment descent, so the ops are unix only.
/// The Windows build still answers the verb: a usage error on stderr and exit 2, because falling
/// through the verb loop would post `fs read {…}` as a chat message.
#[cfg(not(unix))]
pub fn main(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("skills") {
        return crate::system_skills::main(&args[1..]);
    }
    eprintln!("{USAGE}");
    2
}

/// What the verb writes and exits with, kept as data so a test can assert the exact bytes and code.
#[cfg(unix)]
pub struct Outcome {
    pub stdout: Vec<u8>,
    pub stderr: String,
    pub code: i32,
}

/// Run one `ufo fs` call from the launch directory and answer the exit code.
#[cfg(unix)]
pub fn main(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("skills") {
        return crate::system_skills::main(&args[1..]);
    }
    let workdir = std::env::current_dir().unwrap_or_else(|_| Path::new(".").to_path_buf());
    let outcome = run(args, &workdir);
    if !outcome.stdout.is_empty() {
        let mut out = std::io::stdout().lock();
        let _ = out.write_all(&outcome.stdout);
        let _ = out.flush();
    }
    if !outcome.stderr.is_empty() {
        eprint!("{}", outcome.stderr);
    }
    outcome.code
}

/// `workdir` is where a caller-supplied `enum` listing is read from — the op workdir when the host
/// drives the verb, the launch directory otherwise.
#[cfg(unix)]
pub fn run(args: &[String], workdir: &Path) -> Outcome {
    if args.len() != 2 || !OPS.contains(&args[0].as_str()) {
        return Outcome {
            stdout: Vec::new(),
            stderr: format!("{USAGE}\n"),
            code: 2,
        };
    }
    let mut params = match serde_json::from_str::<serde_json::Value>(&args[1]) {
        Ok(params) if params.is_object() => params,
        Ok(_) => return refusal("params must be a JSON object"),
        Err(error) => return refusal(&format!("ValueError: {error}")),
    };
    let home = crate::config::Home::resolve();
    for key in ["path", "workspace"] {
        let Some(value) = params.get_mut(key) else {
            continue;
        };
        let Some(path) = value.as_str() else {
            continue;
        };
        if let Some(relative) = path.strip_prefix("$UFO_HOME/") {
            *value =
                serde_json::Value::String(home.root.join(relative).to_string_lossy().into_owned());
        }
    }
    match fileops::run_contained(&args[0], &params, workdir) {
        Ok(result) => Outcome {
            stdout: serde_json::to_vec(&result).expect("op results serialize"),
            stderr: String::new(),
            code: 0,
        },
        Err(OpError::Refused(message)) => refusal(&message),
        Err(OpError::Failed(message)) => Outcome {
            stdout: Vec::new(),
            stderr: format!("ufo fs: {message}\n"),
            code: 1,
        },
    }
}

#[cfg(unix)]
fn refusal(message: &str) -> Outcome {
    Outcome {
        stdout: serde_json::to_vec(&serde_json::json!({"error": message}))
            .expect("a refusal serializes"),
        stderr: String::new(),
        code: 1,
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use base64::Engine as _;
    use std::path::PathBuf;

    /// A workspace holding one file, and an outside directory holding the file an escape would
    /// reach — `test_sbxfs.py`'s own fixture.
    fn workspace(tag: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("ufo-fs-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let workspace = base.join("workspace");
        let outside = base.join("outside");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(workspace.join("notes.txt"), "workspace\n").unwrap();
        std::fs::write(outside.join("secret.txt"), "outside\n").unwrap();
        (
            std::fs::canonicalize(workspace).unwrap(),
            std::fs::canonicalize(outside).unwrap(),
        )
    }

    fn called(op: &str, params: serde_json::Value, workdir: &Path) -> Outcome {
        run(&[op.to_string(), params.to_string()], workdir)
    }

    fn answered(outcome: &Outcome) -> serde_json::Value {
        serde_json::from_slice(&outcome.stdout).expect("stdout is one JSON object")
    }

    /// The paths one result names, owned so the JSON they came from can be dropped.
    fn named(outcome: &Outcome, key: &str, field: &str) -> Vec<String> {
        answered(outcome)[key]
            .as_array()
            .expect("the result names a list")
            .iter()
            .map(|entry| {
                entry[field]
                    .as_str()
                    .expect("an entry names its path")
                    .to_string()
            })
            .collect()
    }

    fn refused(outcome: &Outcome) -> String {
        assert_eq!(outcome.code, 1, "{outcome:?}", outcome = outcome.stderr);
        assert!(outcome.stderr.is_empty(), "{}", outcome.stderr);
        answered(outcome)["error"]
            .as_str()
            .expect("a refusal names its message")
            .to_string()
    }

    fn path(root: &Path, name: &str) -> String {
        root.join(name).to_string_lossy().into_owned()
    }

    fn encoded(text: &str) -> String {
        base64::engine::general_purpose::URL_SAFE.encode(text.as_bytes())
    }

    fn edits(old: &str, new: &str) -> serde_json::Value {
        serde_json::json!([{"old_string_b64": encoded(old), "new_string_b64": encoded(new)}])
    }

    fn git(directory: &Path, args: &[&str]) {
        let done = std::process::Command::new("git")
            .args(args)
            .current_dir(directory)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_SYSTEM", "/dev/null")
            .output()
            .expect("git runs");
        assert!(done.status.success(), "git {args:?}: {done:?}");
    }

    fn repository(path: &Path, files: &[(&str, &str)]) {
        std::fs::create_dir_all(path).unwrap();
        git(path, &["init", "-q", "."]);
        for (name, text) in files {
            let target = path.join(name);
            std::fs::create_dir_all(target.parent().unwrap()).unwrap();
            std::fs::write(target, text).unwrap();
        }
        git(path, &["add", "-A"]);
        git(
            path,
            &[
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "base",
            ],
        );
    }

    #[test]
    fn the_usage_error_names_every_op_and_exits_two() {
        let (root, _outside) = workspace("usage");
        for args in [
            vec![],
            vec!["read".to_string()],
            vec!["read".to_string(), "{}".to_string(), "extra".to_string()],
            vec!["mystery".to_string(), "{}".to_string()],
        ] {
            let outcome = run(&args, &root);
            assert_eq!(outcome.code, 2);
            assert!(outcome.stdout.is_empty());
            assert_eq!(outcome.stderr, format!("{USAGE}\n"));
        }
    }

    #[test]
    fn params_must_be_a_json_object() {
        let (root, _outside) = workspace("params");
        let listed = called("read", serde_json::json!([1, 2]), &root);
        assert_eq!(refused(&listed), "params must be a JSON object");
        let broken = run(&["read".to_string(), "{oops".to_string()], &root);
        assert!(refused(&broken).starts_with("ValueError: "));
    }

    #[test]
    fn a_missing_path_reads_back_as_a_key_error() {
        let (root, _outside) = workspace("keyerror");
        let outcome = called(
            "read",
            serde_json::json!({"workspace": root.to_str().unwrap()}),
            &root,
        );
        assert_eq!(refused(&outcome), "KeyError: 'path'");
        let rootless = called("read", serde_json::json!({"path": "notes.txt"}), &root);
        assert_eq!(refused(&rootless), "KeyError: 'workspace'");
    }

    #[test]
    fn read_answers_a_contained_file() {
        let (root, _outside) = workspace("read");
        let outcome = called(
            "read",
            serde_json::json!({"path": "notes.txt", "workspace": root.to_str().unwrap()}),
            &root,
        );
        assert_eq!(outcome.code, 0);
        let result = answered(&outcome);
        assert_eq!(result["content"], "1\tworkspace");
        assert_eq!(result["total_lines"], 1);
        assert_eq!(result["path"], root.join("notes.txt").to_str().unwrap());
    }

    #[test]
    fn read_refuses_a_traversal_path_and_a_planted_symlink() {
        let (root, outside) = workspace("readguard");
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        let escape = format!("{}/../outside/secret.txt", root.display());
        let traversal = called(
            "read",
            serde_json::json!({"path": escape, "workspace": root.to_str().unwrap()}),
            &root,
        );
        assert!(refused(&traversal).contains("escapes"));
        let planted = called(
            "read",
            serde_json::json!({"path": "link.txt", "workspace": root.to_str().unwrap()}),
            &root,
        );
        assert_eq!(
            refused(&planted),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
    }

    #[test]
    fn write_lands_bytes_and_refuses_a_planted_symlink() {
        let (root, outside) = workspace("write");
        std::fs::create_dir(root.join("staging")).unwrap();
        let staged = root.join("staging/write.stage");
        std::fs::write(&staged, "landed\n").unwrap();
        let landed = called(
            "write",
            serde_json::json!({
                "path": "deep/nest/out.txt",
                "staged_path": staged.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert_eq!(answered(&landed)["created"], true);
        assert_eq!(
            std::fs::read_to_string(root.join("deep/nest/out.txt")).unwrap(),
            "landed\n"
        );
        assert!(!staged.exists());

        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        std::fs::write(&staged, "planted\n").unwrap();
        let planted = called(
            "write",
            serde_json::json!({
                "path": "link.txt",
                "staged_path": staged.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
                "allow_existing": true,
            }),
            &root,
        );
        assert_eq!(
            refused(&planted),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
        assert!(!staged.exists());
    }

    #[test]
    fn write_refuses_an_unread_overwrite() {
        let (root, _outside) = workspace("unread");
        std::fs::create_dir(root.join("staging")).unwrap();
        let staged = root.join("staging/write.stage");
        std::fs::write(&staged, "new\n").unwrap();
        let outcome = called(
            "write",
            serde_json::json!({
                "path": "notes.txt",
                "staged_path": staged.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert_eq!(
            refused(&outcome),
            format!(
                "file {} must be read before it is written",
                root.join("notes.txt").display()
            )
        );
        assert_eq!(
            std::fs::read_to_string(root.join("notes.txt")).unwrap(),
            "workspace\n"
        );
    }

    #[test]
    fn write_refuses_a_traversal_path() {
        let (root, outside) = workspace("writeup");
        std::fs::create_dir(root.join("staging")).unwrap();
        let staged = root.join("staging/write.stage");
        std::fs::write(&staged, "planted\n").unwrap();
        let outcome = called(
            "write",
            serde_json::json!({
                "path": format!("{}/../outside/secret.txt", root.display()),
                "staged_path": staged.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
                "allow_existing": true,
            }),
            &root,
        );
        assert!(refused(&outcome).contains("escapes"));
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
    }

    /// The case that tells check 2 from check 3: an ancestor link pointing inside the root is
    /// canonicalized, so the bytes land on the real directory's inode and the link is left alone.
    #[test]
    fn write_through_an_in_root_symlinked_directory_lands_on_the_canonical_inode() {
        let (root, _outside) = workspace("inroot");
        std::fs::create_dir(root.join("real")).unwrap();
        std::os::unix::fs::symlink(root.join("real"), root.join("dir")).unwrap();
        std::fs::create_dir(root.join("staging")).unwrap();
        let staged = root.join("staging/write.stage");
        std::fs::write(&staged, "landed\n").unwrap();
        let outcome = called(
            "write",
            serde_json::json!({
                "path": "dir/new.txt",
                "staged_path": staged.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert_eq!(answered(&outcome)["created"], true);
        assert_eq!(
            std::fs::read_to_string(root.join("real/new.txt")).unwrap(),
            "landed\n"
        );
        assert!(std::fs::symlink_metadata(root.join("dir"))
            .unwrap()
            .is_symlink());
    }

    #[test]
    fn edit_replaces_text_and_refuses_a_traversal_path() {
        let (root, outside) = workspace("edit");
        let applied = called(
            "edit",
            serde_json::json!({
                "path": "notes.txt",
                "workspace": root.to_str().unwrap(),
                "edits": edits("workspace", "edited"),
            }),
            &root,
        );
        assert_eq!(answered(&applied)["replacements"], 1);
        assert_eq!(
            std::fs::read_to_string(root.join("notes.txt")).unwrap(),
            "edited\n"
        );
        let escape = format!("{}/../outside/secret.txt", root.display());
        let traversal = called(
            "edit",
            serde_json::json!({
                "path": escape,
                "workspace": root.to_str().unwrap(),
                "edits": edits("outside", "edited"),
            }),
            &root,
        );
        assert!(refused(&traversal).contains("escapes"));
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
    }

    #[test]
    fn edit_refuses_a_planted_symlink() {
        let (root, outside) = workspace("editlink");
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        let outcome = called(
            "edit",
            serde_json::json!({
                "path": "link.txt",
                "workspace": root.to_str().unwrap(),
                "edits": edits("outside", "edited"),
            }),
            &root,
        );
        assert_eq!(
            refused(&outcome),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
    }

    /// A caller may supply the listing a walking op reads, which is the terminal carrier's own path.
    /// The listing decides what the walk visited and in which order; it does not decide what may be
    /// read, so an entry the guard does not vouch for is dropped from the answer.
    #[test]
    fn a_supplied_listing_still_passes_the_guard() {
        let (root, outside) = workspace("listing");
        std::fs::write(outside.join("secret.txt"), "SECRET\n").unwrap();
        std::fs::write(root.join("real.txt"), "SECRET but ours\n").unwrap();
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        let listing = format!(
            "{}\0{}\0{}\0{}\0",
            root.display(),
            root.join("link.txt").display(),
            root.join("real.txt").display(),
            outside.join("secret.txt").display()
        );
        std::fs::write(root.join("grep-enum"), listing).unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "workspace": root.to_str().unwrap(),
                "enum": "grep-enum",
            }),
            &root,
        );
        let files = answered(&outcome)["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry.as_str().unwrap().to_string())
            .collect::<Vec<String>>();
        assert_eq!(files, vec![path(&root, "real.txt")]);
    }

    #[test]
    fn grep_walks_for_itself_and_omits_a_planted_symlink() {
        let (root, outside) = workspace("grep");
        std::fs::write(outside.join("secret.txt"), "root:x:0:0:SECRET\n").unwrap();
        std::fs::write(root.join("notes.txt"), "nothing here\n").unwrap();
        std::fs::write(root.join("real.txt"), "SECRET but ours\n").unwrap();
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "output_mode": "content",
                "workspace": root.to_str().unwrap(),
                "glob": "*.txt",
            }),
            &root,
        );
        let files = named(&outcome, "matches", "file");
        assert_eq!(files, vec![path(&root, "real.txt")]);
    }

    #[test]
    fn grep_walks_extensionless_text_files() {
        let (root, _outside) = workspace("grep-extensionless");
        std::fs::write(root.join("Makefile"), "build: target\n").unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "target",
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert_eq!(
            answered(&outcome)["files"],
            serde_json::json!([path(&root, "Makefile")])
        );
    }

    #[test]
    fn grep_refuses_a_path_outside_the_workspace_and_a_traversal_glob() {
        let (root, outside) = workspace("grepguard");
        std::fs::write(outside.join("secret.txt"), "SECRET\n").unwrap();
        let escaped = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "workspace": root.to_str().unwrap(),
                "path": outside.join("secret.txt").to_str().unwrap(),
            }),
            &root,
        );
        assert!(refused(&escaped).contains("escapes"));
        let traversal = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "workspace": root.to_str().unwrap(),
                "glob": "../outside/*.txt",
            }),
            &root,
        );
        assert!(refused(&traversal).contains("leaves"));
    }

    /// A scan of one file is a read of that file, so the link is refused rather than skipped: the
    /// caller named it, and answering "no matches" would hide the refusal.
    #[test]
    fn grep_refuses_a_planted_symlink_named_as_its_own_path() {
        let (root, outside) = workspace("grepnamed");
        std::fs::write(outside.join("secret.txt"), "SECRET\n").unwrap();
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "workspace": root.to_str().unwrap(),
                "path": "link.txt",
            }),
            &root,
        );
        assert_eq!(
            refused(&outcome),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
    }

    /// The guard roots a relative name at the workspace, never at the process cwd: here the cwd
    /// holds a directory of that name and the workspace holds a file, so only one answer is right.
    #[test]
    fn grep_reads_a_relative_path_against_the_workspace_not_the_cwd() {
        let (root, outside) = workspace("greprelative");
        std::fs::write(root.join("target.txt"), "SECRET in the workspace file\n").unwrap();
        std::fs::create_dir(outside.join("target.txt")).unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "output_mode": "content",
                "workspace": root.to_str().unwrap(),
                "path": "target.txt",
            }),
            &outside,
        );
        let files = named(&outcome, "matches", "file");
        assert_eq!(files, vec![path(&root, "target.txt")]);
    }

    /// A scan still answers for the workspace's own files reached through a link that stays inside
    /// it: an ancestor link is followed once, under a resolution already proved contained.
    #[test]
    fn grep_reads_a_file_under_an_in_root_symlinked_directory() {
        let (root, _outside) = workspace("grepinroot");
        std::fs::create_dir(root.join("real")).unwrap();
        std::fs::write(root.join("real/hit.txt"), "SECRET but ours\n").unwrap();
        std::os::unix::fs::symlink(root.join("real"), root.join("dir")).unwrap();
        let outcome = called(
            "grep",
            serde_json::json!({
                "pattern": "SECRET",
                "output_mode": "content",
                "workspace": root.to_str().unwrap(),
                "path": "dir",
            }),
            &root,
        );
        let files = named(&outcome, "matches", "file");
        assert_eq!(files, vec![path(&root, "real/hit.txt")]);
    }

    #[test]
    fn glob_re_roots_an_absolute_workspace_pattern_and_refuses_an_escape() {
        let (root, outside) = workspace("glob");
        let rooted = called(
            "glob",
            serde_json::json!({
                "pattern": format!("{}/*.txt", root.display()),
                "path": root.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        let files = named(&rooted, "files", "path");
        assert_eq!(files, vec![path(&root, "notes.txt")]);
        let escaped = called(
            "glob",
            serde_json::json!({
                "pattern": format!("{}/*.txt", outside.display()),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert!(refused(&escaped).contains("escapes"));
        let traversal = called(
            "glob",
            serde_json::json!({"pattern": "../outside/*.txt", "workspace": root.to_str().unwrap()}),
            &root,
        );
        assert!(refused(&traversal).contains("leaves"));
    }

    #[test]
    fn glob_omits_planted_symlinks_and_excluded_trees() {
        let (root, outside) = workspace("globlinks");
        std::os::unix::fs::symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        std::os::unix::fs::symlink(&outside, root.join("dir")).unwrap();
        let flat = called(
            "glob",
            serde_json::json!({
                "pattern": "*.txt",
                "path": root.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        let files = named(&flat, "files", "path");
        assert_eq!(files, vec![path(&root, "notes.txt")]);
        let through_link = called(
            "glob",
            serde_json::json!({
                "pattern": "dir/*.txt",
                "path": root.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
            }),
            &root,
        );
        assert_eq!(answered(&through_link)["count"], 0);

        std::fs::create_dir_all(root.join("repo/.git")).unwrap();
        std::fs::write(root.join("repo/.git/index"), "metadata").unwrap();
        std::fs::write(root.join("repo/report.txt"), "result").unwrap();
        let excluded = called(
            "glob",
            serde_json::json!({
                "pattern": "repo/**/*",
                "path": root.to_str().unwrap(),
                "workspace": root.to_str().unwrap(),
                "exclude_names": [".git"],
            }),
            &root,
        );
        let files = named(&excluded, "files", "path");
        assert_eq!(files, vec![path(&root, "repo/report.txt")]);
    }

    /// What a change is, and whose: git counts a modification, a deletion and an untracked add in
    /// any checkout under the workspace, and counts nothing beside a checkout.
    #[test]
    fn changes_reports_every_checkout_and_nothing_outside_one() {
        let (root, _outside) = workspace("changes");
        repository(
            &root.join("checkout"),
            &[
                ("pkg/mod.py", "x = 1\ny = 2\nz = 3\n"),
                ("gone.txt", "old\n"),
            ],
        );
        repository(&root.join("deep/nested"), &[("kept.py", "a\n")]);
        std::fs::write(root.join("checkout/pkg/mod.py"), "x = 1\ny = 9\nz = 3\n").unwrap();
        std::fs::remove_file(root.join("checkout/gone.txt")).unwrap();
        std::fs::write(root.join("checkout/pkg/new.py"), "brand new\n").unwrap();
        std::fs::write(root.join("findings.md"), "what I found\n").unwrap();
        let outcome = called(
            "changes",
            serde_json::json!({"workspace": root.to_str().unwrap()}),
            &root,
        );
        let result = answered(&outcome);
        assert_eq!(result["truncated"], false);
        let mut listed: Vec<(String, String)> = result["changes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|change| {
                (
                    change["path"].as_str().unwrap().to_string(),
                    change["patch"].as_str().unwrap().to_string(),
                )
            })
            .collect();
        listed.sort();
        let names: Vec<&str> = listed.iter().map(|(path, _)| path.as_str()).collect();
        assert_eq!(
            names,
            vec![
                "checkout/gone.txt",
                "checkout/pkg/mod.py",
                "checkout/pkg/new.py"
            ]
        );
        let patches: Vec<&str> = listed.iter().map(|(_, patch)| patch.as_str()).collect();
        assert_eq!(
            patches[0],
            "--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        );
        assert_eq!(
            patches[1],
            "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 9\n z = 3\n"
        );
        assert_eq!(
            patches[2],
            "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+brand new\n"
        );
    }

    /// An unborn HEAD has nothing to diff against, so every file is read off its own contents — the
    /// same path an untracked file takes.
    #[test]
    fn changes_reads_a_checkout_that_has_no_commit_yet() {
        let (root, _outside) = workspace("unborn");
        let fresh = root.join("fresh");
        std::fs::create_dir_all(&fresh).unwrap();
        git(&fresh, &["init", "-q", "."]);
        std::fs::write(fresh.join("staged.py"), "staged\n").unwrap();
        std::fs::write(fresh.join("a file.py"), "spaced\n").unwrap();
        git(&fresh, &["add", "staged.py"]);
        let outcome = called(
            "changes",
            serde_json::json!({"workspace": root.to_str().unwrap(), "paths": ["fresh"]}),
            &root,
        );
        let result = answered(&outcome);
        let mut listed: Vec<(String, String)> = result["changes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|change| {
                (
                    change["path"].as_str().unwrap().to_string(),
                    change["patch"].as_str().unwrap().to_string(),
                )
            })
            .collect();
        listed.sort();
        assert_eq!(
            listed,
            vec![
                (
                    "fresh/a file.py".to_string(),
                    "--- /dev/null\n+++ b/a file.py\n@@ -0,0 +1 @@\n+spaced\n".to_string()
                ),
                (
                    "fresh/staged.py".to_string(),
                    "--- /dev/null\n+++ b/staged.py\n@@ -0,0 +1 @@\n+staged\n".to_string()
                ),
            ]
        );
    }

    #[test]
    fn changes_bounds_a_patch_and_says_so() {
        let (root, _outside) = workspace("bounded");
        repository(&root.join("checkout"), &[("big.py", "old\n")]);
        std::fs::write(root.join("checkout/big.py"), "line\n".repeat(4_000)).unwrap();
        let outcome = called(
            "changes",
            serde_json::json!({"workspace": root.to_str().unwrap()}),
            &root,
        );
        let result = answered(&outcome);
        let changes = result["changes"].as_array().unwrap();
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0]["truncated"], true);
        assert_eq!(
            changes[0]["patch"].as_str().unwrap().chars().count(),
            10_000
        );
    }

    #[test]
    fn a_symlinked_workspace_root_is_refused() {
        let (root, _outside) = workspace("linkedroot");
        let linked = root.parent().unwrap().join("linked-workspace");
        std::os::unix::fs::symlink(&root, &linked).unwrap();
        let outcome = called(
            "read",
            serde_json::json!({"path": "notes.txt", "workspace": linked.to_str().unwrap()}),
            &root,
        );
        assert_eq!(
            refused(&outcome),
            format!("{} is not a directory", linked.display())
        );
    }
}
