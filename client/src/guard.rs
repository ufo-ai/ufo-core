//! The containment guard for a path built from model, agent, or script input.
//!
//! Checks 2 to 4 of `core/src/ufo/harness/containment.py`, in Rust, for the paths the `ufo fs`
//! verb takes: the canonical parent asserted inside the root, a per-component `O_NOFOLLOW` descent
//! from a root fd so every operation names its target relative to a pinned parent fd, and the
//! target's own `lstat` that never follows a final link. A write stages a sibling
//! `O_CREAT|O_EXCL|O_NOFOLLOW` and renames it, so a link planted at the name cannot steer the bytes.
//!
//! The wire ops (`OP_FILE`) keep calling `std::fs` on paths the host already rewrote; this guard is
//! what the verb runs instead, because a path reaching it came from inside the sandbox.
//!
//! Refusal messages are `ContainmentError`'s own, because the host maps them to the model's
//! `ValueError` and an operator dashboard groups on them.

use std::ffi::{CString, OsStr, OsString};
use std::io::{Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::io::FromRawFd;
use std::path::{Path, PathBuf};

const STAGED_PREFIX: &str = ".ufo-staged-";
const UNUSABLE_NAMES: [&str; 3] = ["", ".", ".."];

fn dir_flags() -> libc::c_int {
    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW
}

/// A path built from untrusted input does not name a file inside its root. One type for every
/// refusal `containment.py` raises as a `ContainmentError` subclass: the message is what a caller
/// reads, and the class only ever chose which `except` clause caught it.
#[derive(Debug)]
pub struct GuardError(String);

impl GuardError {
    fn new(message: impl Into<String>) -> Self {
        GuardError(message.into())
    }

    pub fn message(&self) -> &str {
        &self.0
    }
}

/// The target's own stat, taken without following a final symlink.
#[derive(Clone, Copy, Debug)]
pub struct Entry {
    pub size: u64,
    pub mode: libc::mode_t,
}

/// A validated target, addressed only through `parent`, an fd on the canonical parent directory the
/// symlink-free descent reached. A directory renamed or replaced by a link after the descent takes
/// the fd's inode with it, which is what keeps the check and the operation naming one file.
#[derive(Debug)]
pub struct Contained {
    path: PathBuf,
    name: OsString,
    parent: libc::c_int,
}

impl Drop for Contained {
    fn drop(&mut self) {
        unsafe { libc::close(self.parent) };
    }
}

impl Contained {
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The target's own stat, never following a final symlink. `None` when nothing holds the name;
    /// a refusal when something other than a regular file does.
    pub fn lstat(&self) -> Result<Option<Entry>, GuardError> {
        match self.stat_at()? {
            None => Ok(None),
            Some(entry) if is_dir(entry.mode) => Err(GuardError::new(format!(
                "{} is a directory",
                self.display()
            ))),
            Some(entry) if !is_regular(entry.mode) => Err(GuardError::new(format!(
                "{} is not a regular file",
                self.display()
            ))),
            Some(entry) => Ok(Some(entry)),
        }
    }

    /// Up to `limit` bytes, read through an `O_NOFOLLOW` fd whose `fstat` says regular file.
    pub fn read_bytes(&self, limit: u64) -> Result<Vec<u8>, GuardError> {
        let handle = self.open_regular()?;
        let mut bytes = Vec::new();
        handle
            .take(limit)
            .read_to_end(&mut bytes)
            .map_err(|error| GuardError::new(format!("{}: {error}", self.display())))?;
        Ok(bytes)
    }

    /// The mode bits of the staged file this call is about to rename, set on an `O_NOFOLLOW` fd.
    pub fn chmod(&self, mode: libc::mode_t) -> Result<(), GuardError> {
        let descriptor = self.open_at(libc::O_WRONLY | libc::O_NOFOLLOW, 0)?;
        let outcome = unsafe { libc::fchmod(descriptor, mode & 0o777) };
        unsafe { libc::close(descriptor) };
        if outcome != 0 {
            return Err(GuardError::new(format!(
                "could not set the mode on {}",
                self.display()
            )));
        }
        Ok(())
    }

    pub fn unlink(&self) {
        let Ok(name) = self.c_name() else { return };
        unsafe { libc::unlinkat(self.parent, name.as_ptr(), 0) };
    }

    /// Rename `source` onto this target, both named relative to their pinned parents.
    pub fn replace_with(&self, source: &Contained) -> Result<(), GuardError> {
        let from = source.c_name()?;
        let onto = self.c_name()?;
        let outcome =
            unsafe { libc::renameat(source.parent, from.as_ptr(), self.parent, onto.as_ptr()) };
        if outcome != 0 {
            return Err(GuardError::new(format!(
                "could not rename {} onto {}",
                source.display(),
                self.display()
            )));
        }
        Ok(())
    }

    pub fn replace_text(&self, text: &str, mode: libc::mode_t) -> Result<(), GuardError> {
        self.replace_bytes(text.as_bytes(), mode)
    }

    /// Write `bytes` to a staged sibling created `O_CREAT|O_EXCL|O_NOFOLLOW` and rename it onto the
    /// target. Exclusive create is what refuses a name a link already holds instead of truncating
    /// through it; the rename installs the bytes whole.
    pub fn replace_bytes(&self, bytes: &[u8], mode: libc::mode_t) -> Result<(), GuardError> {
        let staged = format!("{STAGED_PREFIX}{}", random_hex());
        let staged_name = c_string(OsStr::new(&staged), &staged)?;
        let descriptor = unsafe {
            libc::openat(
                self.parent,
                staged_name.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW,
                libc::c_uint::from(0o600u16),
            )
        };
        if descriptor < 0 {
            return Err(GuardError::new(format!(
                "could not stage a write beside {}",
                self.display()
            )));
        }
        let outcome = (|| {
            if unsafe { libc::fchmod(descriptor, mode & 0o777) } != 0 {
                return Err(GuardError::new(format!(
                    "could not set the mode on a staged write beside {}",
                    self.display()
                )));
            }
            let mut handle = unsafe { std::fs::File::from_raw_fd(descriptor) };
            handle
                .write_all(bytes)
                .map_err(|error| GuardError::new(format!("{}: {error}", self.display())))?;
            let onto = self.c_name()?;
            if unsafe {
                libc::renameat(
                    self.parent,
                    staged_name.as_ptr(),
                    self.parent,
                    onto.as_ptr(),
                )
            } != 0
            {
                return Err(GuardError::new(format!(
                    "could not rename a staged write onto {}",
                    self.display()
                )));
            }
            Ok(())
        })();
        if outcome.is_err() {
            unsafe { libc::unlinkat(self.parent, staged_name.as_ptr(), 0) };
        }
        outcome
    }

    fn display(&self) -> String {
        self.path.display().to_string()
    }

    fn c_name(&self) -> Result<CString, GuardError> {
        c_string(&self.name, &self.display())
    }

    fn stat_at(&self) -> Result<Option<Entry>, GuardError> {
        let name = self.c_name()?;
        let mut found: libc::stat = unsafe { std::mem::zeroed() };
        let outcome = unsafe {
            libc::fstatat(
                self.parent,
                name.as_ptr(),
                &mut found,
                libc::AT_SYMLINK_NOFOLLOW,
            )
        };
        if outcome != 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::NotFound {
                return Ok(None);
            }
            return Err(GuardError::new(format!("{}: {error}", self.display())));
        }
        Ok(Some(Entry {
            size: found.st_size as u64,
            mode: found.st_mode,
        }))
    }

    fn open_at(&self, flags: libc::c_int, mode: libc::c_uint) -> Result<libc::c_int, GuardError> {
        let name = self.c_name()?;
        let descriptor = unsafe { libc::openat(self.parent, name.as_ptr(), flags, mode) };
        if descriptor >= 0 {
            return Ok(descriptor);
        }
        let error = std::io::Error::last_os_error();
        if error.kind() == std::io::ErrorKind::NotFound {
            return Err(GuardError::new(format!("{} not found", self.display())));
        }
        if error.raw_os_error() == Some(libc::ELOOP) {
            return Err(GuardError::new(format!(
                "{} is not a regular file",
                self.display()
            )));
        }
        Err(GuardError::new(format!("{}: {error}", self.display())))
    }

    fn open_regular(&self) -> Result<std::fs::File, GuardError> {
        let descriptor = self.open_at(libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK, 0)?;
        let mut found: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(descriptor, &mut found) } != 0 || !is_regular(found.st_mode) {
            unsafe { libc::close(descriptor) };
            return Err(GuardError::new(format!(
                "{} is not a regular file",
                self.display()
            )));
        }
        Ok(unsafe { std::fs::File::from_raw_fd(descriptor) })
    }
}

/// The canonical form of a root, refusing a symlinked one — the check a containment assertion
/// cannot stand in for: under a symlinked root every path resolves inside the link's target and
/// passes containment, while the bytes land wherever the link points.
pub fn contained_root(root: &Path) -> Result<PathBuf, GuardError> {
    let Ok(entry) = std::fs::symlink_metadata(root) else {
        return Err(GuardError::new(format!("{} not found", root.display())));
    };
    if !entry.is_dir() {
        return Err(GuardError::new(format!(
            "{} is not a directory",
            root.display()
        )));
    }
    std::fs::canonicalize(root)
        .map_err(|error| GuardError::new(format!("{}: {error}", root.display())))
}

/// A relative path read against `root`, never against the process's cwd.
pub fn rooted(path: &str, root: &Path) -> PathBuf {
    let target = Path::new(path);
    if target.is_absolute() {
        target.to_path_buf()
    } else {
        root.join(target)
    }
}

/// All the checks, then a handle pinned to the target's parent. `create_parent` makes each missing
/// directory as the descent reaches it, so a write to a path whose directories do not exist yet is
/// checked component by component as it is built.
pub fn contained_file(
    path: &str,
    root: &Path,
    create_parent: bool,
) -> Result<Contained, GuardError> {
    let canonical_root = contained_root(root)?;
    let target = rooted(path, &canonical_root);
    let name = leaf(&target);
    if UNUSABLE_NAMES.contains(&name.as_str()) {
        return Err(GuardError::new(format!(
            "{} is not a file path",
            target.display()
        )));
    }
    let parent = resolved(target.parent().unwrap_or(Path::new("/")));
    if !inside(&parent, &canonical_root) {
        return Err(GuardError::new(format!(
            "{} escapes {}",
            target.display(),
            canonical_root.display()
        )));
    }
    let descriptor = descend(&canonical_root, &parent, &target, create_parent)?;
    Ok(Contained {
        path: parent.join(&name),
        name: OsString::from(name),
        parent: descriptor,
    })
}

/// The canonical path of a directory under `root`, reached without following a link at any
/// component — the enumeration entry point, where the result is a directory to walk, so the
/// descent's fds are released once each component is proved.
pub fn contained_dir(path: &str, root: &Path, create: bool) -> Result<PathBuf, GuardError> {
    let canonical_root = contained_root(root)?;
    let target = rooted(path, &canonical_root);
    let resolved_target = resolved(&target);
    if !inside(&resolved_target, &canonical_root) {
        return Err(GuardError::new(format!(
            "{} escapes {}",
            target.display(),
            canonical_root.display()
        )));
    }
    let descriptor = descend(&canonical_root, &resolved_target, &target, create)?;
    unsafe { libc::close(descriptor) };
    Ok(resolved_target)
}

/// The canonical path of an existing regular file under `root`, for a reader that has to hand a
/// path to something else. Every ancestor is proved link-free and the target itself is `lstat`ed,
/// so the name cannot be a planted link.
pub fn contained_regular(path: &str, root: &Path) -> Result<PathBuf, GuardError> {
    let target = contained_file(path, root, false)?;
    if target.lstat()?.is_none() {
        return Err(GuardError::new(format!("{} not found", target.display())));
    }
    Ok(target.path().to_path_buf())
}

/// Whether an enumerated path is a regular file inside `root` that no symlink was crossed to
/// reach — the filter an enumeration applies to its own hits, where a per-component descent per hit
/// would cost more than the walk that produced it. A read of a hit still goes through
/// `contained_file`: this answers what to list, not what to open.
pub fn is_contained_regular(path: &Path, root: &Path) -> bool {
    let Ok(entry) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if !entry.is_file() {
        return false;
    }
    let Ok(canonical) = std::fs::canonicalize(path) else {
        return false;
    };
    canonical == path && inside(&canonical, root)
}

fn descend(
    root: &Path,
    parent: &Path,
    target: &Path,
    create: bool,
) -> Result<libc::c_int, GuardError> {
    let mut descriptor = open_root(root)?;
    let parts = parent
        .strip_prefix(root)
        .map(|rest| {
            rest.components()
                .map(|part| part.as_os_str().to_os_string())
                .collect::<Vec<OsString>>()
        })
        .unwrap_or_default();
    for part in parts {
        let name = match c_string(&part, &target.display().to_string()) {
            Ok(name) => name,
            Err(error) => {
                unsafe { libc::close(descriptor) };
                return Err(error);
            }
        };
        if create {
            unsafe { libc::mkdirat(descriptor, name.as_ptr(), 0o777) };
        }
        let child = unsafe { libc::openat(descriptor, name.as_ptr(), dir_flags(), 0) };
        if child < 0 {
            let error = std::io::Error::last_os_error();
            unsafe { libc::close(descriptor) };
            if error.kind() == std::io::ErrorKind::NotFound {
                return Err(GuardError::new(format!("{} not found", target.display())));
            }
            if matches!(
                error.raw_os_error(),
                Some(libc::ELOOP) | Some(libc::ENOTDIR)
            ) {
                return Err(GuardError::new(format!(
                    "{} passes through {}, which is not a directory",
                    target.display(),
                    Path::new(&part).display()
                )));
            }
            return Err(GuardError::new(format!("{error}")));
        }
        unsafe { libc::close(descriptor) };
        descriptor = child;
    }
    Ok(descriptor)
}

fn open_root(root: &Path) -> Result<libc::c_int, GuardError> {
    let name = c_string(root.as_os_str(), &root.display().to_string())?;
    let descriptor = unsafe { libc::open(name.as_ptr(), dir_flags(), 0) };
    if descriptor < 0 {
        return Err(GuardError::new(format!(
            "{} is not an openable directory",
            root.display()
        )));
    }
    Ok(descriptor)
}

/// The canonical form of `path`, resolved as far as the filesystem holds it and applied lexically
/// beyond that — a not-yet-created target still gets its existing ancestors canonicalized, which is
/// what `Path.resolve()` answers for the parent check.
fn resolved(path: &Path) -> PathBuf {
    let parts: Vec<OsString> = path
        .components()
        .map(|part| part.as_os_str().to_os_string())
        .collect();
    for cut in (0..=parts.len()).rev() {
        let head: PathBuf = parts[..cut].iter().collect();
        if head.as_os_str().is_empty() {
            continue;
        }
        let Ok(mut base) = std::fs::canonicalize(&head) else {
            continue;
        };
        for part in &parts[cut..] {
            if part == ".." {
                base.pop();
            } else if part != "." {
                base.push(part);
            }
        }
        return base;
    }
    path.to_path_buf()
}

fn inside(path: &Path, root: &Path) -> bool {
    path == root || path.starts_with(root)
}

fn leaf(path: &Path) -> String {
    let text = path.to_string_lossy();
    match text.rfind('/') {
        Some(cut) => text[cut + 1..].to_string(),
        None => text.to_string(),
    }
}

fn c_string(name: &OsStr, path: &str) -> Result<CString, GuardError> {
    CString::new(name.as_bytes()).map_err(|_| GuardError::new(format!("{path} is not a file path")))
}

fn is_dir(mode: libc::mode_t) -> bool {
    mode & libc::S_IFMT == libc::S_IFDIR
}

fn is_regular(mode: libc::mode_t) -> bool {
    mode & libc::S_IFMT == libc::S_IFREG
}

fn random_hex() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("os randomness is available");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A workspace holding one file, and an outside directory holding the file an escape would
    /// reach — `test_sbxfs.py`'s own fixture.
    fn workspace(tag: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("ufo-guard-{tag}-{}", std::process::id()));
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

    fn link(target: &Path, name: &Path) {
        std::os::unix::fs::symlink(target, name).unwrap();
    }

    #[test]
    fn reads_a_contained_file() {
        let (root, _outside) = workspace("read");
        let file = contained_file("notes.txt", &root, false).unwrap();
        let entry = file.lstat().unwrap().unwrap();
        assert_eq!(file.read_bytes(entry.size).unwrap(), b"workspace\n");
        assert_eq!(file.path(), root.join("notes.txt"));
    }

    #[test]
    fn refuses_a_traversal_path() {
        let (root, outside) = workspace("traversal");
        let escape = format!("{}/../outside/secret.txt", root.display());
        let error = contained_file(&escape, &root, false).unwrap_err();
        assert!(error.message().contains("escapes"), "{}", error.message());
        assert_eq!(
            std::fs::read_to_string(outside.join("secret.txt")).unwrap(),
            "outside\n"
        );
    }

    #[test]
    fn refuses_a_planted_symlink() {
        let (root, outside) = workspace("planted");
        link(&outside.join("secret.txt"), &root.join("link.txt"));
        let file = contained_file("link.txt", &root, false).unwrap();
        assert_eq!(
            file.lstat().unwrap_err().message(),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
    }

    #[test]
    fn refuses_a_symlinked_directory_component_out_of_the_workspace() {
        let (root, outside) = workspace("component");
        link(&outside, &root.join("dir"));
        let error = contained_file("dir/secret.txt", &root, false).unwrap_err();
        assert!(error.message().contains("escapes"), "{}", error.message());
    }

    #[test]
    fn refuses_a_symlinked_workspace_root() {
        let (root, _outside) = workspace("linkedroot");
        let linked = root.parent().unwrap().join("linked-workspace");
        link(&root, &linked);
        let error = contained_file("notes.txt", &linked, false).unwrap_err();
        assert_eq!(
            error.message(),
            format!("{} is not a directory", linked.display())
        );
    }

    #[test]
    fn refuses_a_component_that_is_not_a_directory() {
        let (root, _outside) = workspace("notdir");
        let error = contained_file("notes.txt/inner.txt", &root, false).unwrap_err();
        assert!(
            error.message().contains("passes through notes.txt"),
            "{}",
            error.message()
        );
    }

    #[test]
    fn write_lands_on_the_canonical_inode_under_an_in_root_link() {
        let (root, _outside) = workspace("inroot");
        std::fs::create_dir(root.join("real")).unwrap();
        link(&root.join("real"), &root.join("dir"));
        let file = contained_file("dir/new.txt", &root, false).unwrap();
        file.replace_bytes(b"landed\n", 0o644).unwrap();
        assert_eq!(
            std::fs::read_to_string(root.join("real/new.txt")).unwrap(),
            "landed\n"
        );
        assert!(std::fs::symlink_metadata(root.join("dir"))
            .unwrap()
            .is_symlink());
    }

    #[test]
    fn write_pins_the_checked_parent_directory() {
        let (root, outside) = workspace("pinned");
        let parent = root.join("repo");
        std::fs::create_dir(&parent).unwrap();
        std::fs::write(parent.join("app.py"), "workspace-old\n").unwrap();
        std::fs::write(outside.join("app.py"), "outside-old\n").unwrap();
        let file = contained_file("repo/app.py", &root, false).unwrap();
        // The swap `test_sbxfs.py` performs inside the file lock: the checked directory is renamed
        // away and a link out of the workspace takes its name. The pinned fd keeps the write on the
        // inode the descent proved.
        std::fs::rename(&parent, root.join("moved")).unwrap();
        link(&outside, &parent);
        file.replace_bytes(b"workspace-new\n", 0o644).unwrap();
        assert_eq!(
            std::fs::read_to_string(root.join("moved/app.py")).unwrap(),
            "workspace-new\n"
        );
        assert_eq!(
            std::fs::read_to_string(outside.join("app.py")).unwrap(),
            "outside-old\n"
        );
    }

    #[test]
    fn create_parent_builds_directories_through_the_descent() {
        let (root, _outside) = workspace("mkparent");
        let file = contained_file("deep/nest/out.txt", &root, true).unwrap();
        file.replace_bytes(b"body", 0o600).unwrap();
        assert_eq!(
            std::fs::read_to_string(root.join("deep/nest/out.txt")).unwrap(),
            "body"
        );
    }

    #[test]
    fn contained_dir_refuses_an_escape_and_follows_an_in_root_link() {
        let (root, outside) = workspace("dirs");
        std::fs::create_dir(root.join("real")).unwrap();
        link(&root.join("real"), &root.join("dir"));
        assert_eq!(
            contained_dir("dir", &root, false).unwrap(),
            root.join("real")
        );
        let error = contained_dir(outside.to_str().unwrap(), &root, false).unwrap_err();
        assert!(error.message().contains("escapes"), "{}", error.message());
    }

    #[test]
    fn contained_regular_refuses_a_missing_file_and_a_link() {
        let (root, outside) = workspace("regular");
        link(&outside.join("secret.txt"), &root.join("link.txt"));
        assert_eq!(
            contained_regular("ghost.txt", &root).unwrap_err().message(),
            format!("{} not found", root.join("ghost.txt").display())
        );
        assert_eq!(
            contained_regular("link.txt", &root).unwrap_err().message(),
            format!("{} is not a regular file", root.join("link.txt").display())
        );
    }

    #[test]
    fn a_path_that_names_no_file_in_the_root_is_refused() {
        let (root, _outside) = workspace("rootpath");
        // The root's own name is a usable one, so it is check 2 that refuses it: its parent is the
        // directory above the root.
        let named = contained_file(root.to_str().unwrap(), &root, false).unwrap_err();
        assert!(named.message().contains("escapes"), "{}", named.message());
        let upward = contained_file("..", &root, false).unwrap_err();
        assert!(
            upward.message().ends_with("is not a file path"),
            "{}",
            upward.message()
        );
    }

    #[test]
    fn is_contained_regular_filters_links_and_directories() {
        let (root, outside) = workspace("filter");
        link(&outside.join("secret.txt"), &root.join("link.txt"));
        assert!(is_contained_regular(&root.join("notes.txt"), &root));
        assert!(!is_contained_regular(&root.join("link.txt"), &root));
        assert!(!is_contained_regular(&root, &root));
        assert!(!is_contained_regular(&outside.join("secret.txt"), &root));
    }

    #[test]
    fn mode_carries_across_an_overwrite() {
        use std::os::unix::fs::PermissionsExt;
        let (root, _outside) = workspace("mode");
        let file = contained_file("notes.txt", &root, false).unwrap();
        file.chmod(0o755).unwrap();
        let entry = file.lstat().unwrap().unwrap();
        assert_eq!(entry.mode & 0o777, 0o755);
        file.replace_bytes(b"second\n", entry.mode).unwrap();
        let mode = std::fs::metadata(root.join("notes.txt"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o755);
        assert_eq!(
            std::fs::read_to_string(root.join("notes.txt")).unwrap(),
            "second\n"
        );
    }
}
