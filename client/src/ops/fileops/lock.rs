//! The lock file is a digest of the target path under the temp dir, where `sbxfs` put it: outside the
//! workspace, so no enumeration sees it, and under the same name, so both writers contend for one lock.

use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::DirBuilderExt;
use std::path::{Path, PathBuf};

use crate::ops::fileops::text::{failed, OpError};

const LOCK_DIR: &str = "ufo-sbxfs-locks";

/// `{tempdir}/ufo-sbxfs-locks/{sha256(path)}` — the path's own bytes, hex lowercase, which is what
/// `hashlib.sha256(str(path).encode()).hexdigest()` answers for the same path.
pub fn lock_path(target: &Path) -> PathBuf {
    let digest = ring::digest::digest(&ring::digest::SHA256, target.as_os_str().as_bytes());
    let name: String = digest
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    std::env::temp_dir().join(LOCK_DIR).join(name)
}

pub struct FileLock {
    descriptor: libc::c_int,
}

impl Drop for FileLock {
    fn drop(&mut self) {
        unsafe { libc::flock(self.descriptor, libc::LOCK_UN) };
        unsafe { libc::close(self.descriptor) };
    }
}

pub fn exclusive(target: &Path) -> Result<FileLock, OpError> {
    let path = lock_path(target);
    let directory = path.parent().unwrap_or(Path::new("/"));
    if let Err(error) = std::fs::DirBuilder::new().mode(0o700).create(directory) {
        if error.kind() != std::io::ErrorKind::AlreadyExists {
            return Err(failed(format!("{}: {error}", directory.display())));
        }
    }
    let name = std::ffi::CString::new(path.as_os_str().as_bytes())
        .map_err(|_| failed(format!("{} is not a lock path", path.display())))?;
    let descriptor = unsafe {
        libc::open(
            name.as_ptr(),
            libc::O_CREAT | libc::O_RDWR,
            libc::c_uint::from(0o600u16),
        )
    };
    if descriptor < 0 {
        return Err(failed(format!(
            "{}: {}",
            path.display(),
            std::io::Error::last_os_error()
        )));
    }
    let held = FileLock { descriptor };
    loop {
        if unsafe { libc::flock(descriptor, libc::LOCK_EX) } == 0 {
            return Ok(held);
        }
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::EINTR) {
            return Err(failed(format!("{}: {error}", path.display())));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_lock_name_is_the_digest_python_computed() {
        let named = lock_path(Path::new("/workspace/shared.txt"));
        assert_eq!(named.parent().unwrap(), std::env::temp_dir().join(LOCK_DIR));
        assert_eq!(
            named.file_name().unwrap().to_str().unwrap(),
            "ec8509fb4fc7a52d52e53a73c17f58e2409e62d9b4e6455a4358de3652ad91d7"
        );
    }

    #[test]
    fn a_second_lock_on_one_path_waits_for_the_first() {
        let target = std::env::temp_dir().join(format!("ufo-lock-wait-{}", std::process::id()));
        let held = exclusive(&target).unwrap();
        let waiting = std::thread::spawn({
            let target = target.clone();
            move || {
                let second = exclusive(&target).unwrap();
                drop(second);
            }
        });
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(!waiting.is_finished(), "the second lock did not wait");
        drop(held);
        waiting.join().unwrap();
    }
}
