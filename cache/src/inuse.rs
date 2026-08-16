use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Tracks the on-disk paths live requests are reading, so the LRU sweep never evicts one mid-serve.
/// A plain `std` mutex: every critical section is a counter bump or a membership test — never I/O,
/// never held across an await — so the blocking sweep and the async handlers share it safely. The
/// sweep takes `contains` and the atomic rename-aside under one lock a request bumps to claim a
/// path, so a request racing the sweep either wins the lock (the sweep skips) or loses it (the path
/// is already gone and the request re-fetches).
#[derive(Clone, Default)]
pub struct InUse(Arc<Mutex<HashMap<PathBuf, usize>>>);

impl InUse {
    /// Claim `path` for as long as the returned guard lives. The refcount admits concurrent readers
    /// of one path; the last guard to drop clears the entry.
    pub fn guard(&self, path: &Path) -> InUseGuard {
        *self
            .0
            .lock()
            .unwrap()
            .entry(path.to_path_buf())
            .or_insert(0) += 1;
        InUseGuard {
            in_use: self.clone(),
            path: path.to_path_buf(),
        }
    }

    /// Reserve `path` for eviction: if no request holds it, run `reserve` under the lock and return
    /// its result; otherwise return false without running it. `reserve` must be one fast,
    /// non-blocking step — an atomic rename-aside, never recursive I/O and never another `InUse` call
    /// (the lock is not reentrant) — because a request claiming the same path waits on this lock.
    pub fn reserve_if_free(&self, path: &Path, reserve: impl FnOnce() -> bool) -> bool {
        let held = self.0.lock().unwrap();
        if held.contains_key(path) {
            return false;
        }
        reserve()
    }

    fn release(&self, path: &Path) {
        let mut held = self.0.lock().unwrap();
        if let Some(count) = held.get_mut(path) {
            *count -= 1;
            if *count == 0 {
                held.remove(path);
            }
        }
    }
}

/// Drops a request's claim on a path, letting the sweep evict it once no request holds it.
pub struct InUseGuard {
    in_use: InUse,
    path: PathBuf,
}

impl Drop for InUseGuard {
    fn drop(&mut self) {
        self.in_use.release(&self.path);
    }
}
