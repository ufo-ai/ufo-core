//! A `ufo fs` call from inside a sandbox carries no host listing, so the walk happens here — and it has
//! to visit what `sbxfs` visits, in that order, because the order decides which results survive truncation.

use std::path::{Path, PathBuf};

pub const SKIP_DIRS: &[&str] = &[
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
    "__pycache__",
];

const GIT_DIR: &str = ".git";

struct Listing {
    directories: Vec<String>,
    descend: Vec<String>,
    files: Vec<String>,
}

pub fn walk_files(start: &Path) -> Vec<PathBuf> {
    walked(start, true, true)
}

pub fn glob_files(start: &Path) -> Vec<PathBuf> {
    walked(start, false, false)
}

pub fn checkouts(root: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let listed = entries(&directory, true);
        if listed.directories.iter().any(|name| name == GIT_DIR)
            || listed.files.iter().any(|name| name == GIT_DIR)
        {
            found.push(directory);
            continue;
        }
        for name in listed.descend.iter().rev() {
            if !SKIP_DIRS.contains(&name.as_str()) {
                pending.push(directory.join(name));
            }
        }
    }
    found
}

fn walked(start: &Path, prune: bool, sort_files: bool) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut pending = vec![start.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let listed = entries(&directory, sort_files);
        for name in &listed.files {
            found.push(directory.join(name));
        }
        for name in listed.descend.iter().rev() {
            if !prune || !SKIP_DIRS.contains(&name.as_str()) {
                pending.push(directory.join(name));
            }
        }
    }
    found
}

/// A symlink to a directory counts as a directory that is never entered, exactly as `os.walk` leaves
/// one in `dirnames` and does not follow it; every other symlink counts as a file.
fn entries(directory: &Path, sort_files: bool) -> Listing {
    let mut listed = Listing {
        directories: Vec::new(),
        descend: Vec::new(),
        files: Vec::new(),
    };
    let Ok(found) = std::fs::read_dir(directory) else {
        return listed;
    };
    for entry in found.flatten() {
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        let kind = entry.file_type();
        let linked = kind.as_ref().is_ok_and(|found| found.is_symlink());
        let directory_here = kind.as_ref().is_ok_and(|found| found.is_dir())
            || (linked && std::fs::metadata(entry.path()).is_ok_and(|found| found.is_dir()));
        if !directory_here {
            listed.files.push(name);
            continue;
        }
        listed.directories.push(name.clone());
        if !linked {
            listed.descend.push(name);
        }
    }
    if sort_files {
        listed.files.sort();
    }
    listed
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-walk-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn sorts_each_directory_and_prunes_the_skip_list() {
        let root = scratch("prune");
        std::fs::create_dir_all(root.join("node_modules")).unwrap();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("b.txt"), "b").unwrap();
        std::fs::write(root.join("a.txt"), "a").unwrap();
        std::fs::write(root.join("src/inner.txt"), "i").unwrap();
        std::fs::write(root.join("node_modules/hidden.txt"), "h").unwrap();
        let walked: Vec<PathBuf> = walk_files(&root);
        assert_eq!(
            walked,
            vec![
                root.join("a.txt"),
                root.join("b.txt"),
                root.join("src/inner.txt")
            ]
        );
        let unpruned = glob_files(&root);
        assert!(unpruned.contains(&root.join("node_modules/hidden.txt")));
    }

    #[test]
    fn never_descends_a_symlinked_directory() {
        let root = scratch("link");
        std::fs::create_dir_all(root.join("real")).unwrap();
        std::fs::write(root.join("real/hit.txt"), "x").unwrap();
        std::os::unix::fs::symlink(root.join("real"), root.join("dir")).unwrap();
        let walked = walk_files(&root);
        assert_eq!(walked, vec![root.join("real/hit.txt")]);
    }

    #[test]
    fn a_checkout_hides_the_one_nested_in_it() {
        let root = scratch("checkouts");
        std::fs::create_dir_all(root.join("outer/.git")).unwrap();
        std::fs::create_dir_all(root.join("outer/inner/.git")).unwrap();
        std::fs::create_dir_all(root.join("plain")).unwrap();
        assert_eq!(checkouts(&root), vec![root.join("outer")]);
    }
}
