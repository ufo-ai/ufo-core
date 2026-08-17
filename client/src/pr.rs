//! The working directory's GitHub PR, watched best-effort through `gh`: a missing gh, a
//! directory outside a repository, or a branch without a PR is an empty answer, never an error.

use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

use serde::Deserialize;

const POLL: Duration = Duration::from_secs(30);

/// The PR the footer states: the number it shows and the page a click opens.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Pr {
    pub number: u64,
    pub url: String,
}

/// The PR for the current branch in `cwd`, from `gh pr view`.
pub fn current(cwd: &Path) -> Option<Pr> {
    ask(cwd, Path::new("gh"))
}

/// Poll the current PR, stating each change through `state`, until the listener hangs up.
pub fn watch(cwd: &Path, state: impl Fn(Option<Pr>) -> bool) {
    let mut last = None;
    loop {
        let now = current(cwd);
        if now != last {
            last = now.clone();
            if !state(now) {
                return;
            }
        }
        std::thread::sleep(POLL);
    }
}

fn ask(cwd: &Path, gh: &Path) -> Option<Pr> {
    let output = Command::new(gh)
        .args(["pr", "view", "--json", "number,url"])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    serde_json::from_slice(&output.stdout).ok()
}

#[cfg(test)]
#[cfg(unix)]
mod tests {
    use super::*;

    #[test]
    fn ask_answers_the_pr_its_absence_and_a_missing_gh() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("ufo-pr-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("test dir");
        let stub = dir.join("gh");
        std::fs::write(&stub, "#!/bin/sh\n[ -f pr.json ] || exit 1\ncat pr.json\n")
            .expect("stub gh");
        std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).expect("mode");

        assert_eq!(ask(&dir, &stub), None);

        let url = "https://github.com/acme/repo/pull/1892";
        std::fs::write(
            dir.join("pr.json"),
            format!(r#"{{"number": 1892, "url": "{url}"}}"#),
        )
        .expect("pr json");
        assert_eq!(
            ask(&dir, &stub),
            Some(Pr {
                number: 1892,
                url: url.to_string(),
            })
        );

        assert_eq!(ask(&dir, Path::new("/nonexistent/gh")), None);
        std::fs::remove_dir_all(&dir).ok();
    }
}
