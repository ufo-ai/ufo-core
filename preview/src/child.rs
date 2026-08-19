use std::process::Output;
use std::time::Duration;

/// Ceilings a rendering child runs under; exceeding any one kills only that child.
#[derive(Clone, Debug)]
pub struct Limits {
    pub deadline: Duration,
    pub memory_bytes: u64,
    pub file_size_bytes: u64,
    pub cpu_secs: u64,
}

#[derive(Debug)]
pub enum ChildError {
    Timeout,
    Failed(Output),
    Spawn(std::io::Error),
}

#[cfg(target_os = "linux")]
type RlimitResource = libc::__rlimit_resource_t;
#[cfg(not(target_os = "linux"))]
type RlimitResource = libc::c_int;

const CHILD_MAX_OPEN_FILES: u64 = 4096;

/// Run one child to completion under `limits`: an environment built from scratch (cleared,
/// then `envs` applied), its own session (so the deadline can kill the whole group), rlimits
/// set between fork and exec.
pub async fn run(
    mut cmd: tokio::process::Command,
    limits: &Limits,
    envs: &[(&str, &str)],
) -> Result<Output, ChildError> {
    cmd.env_clear();
    for (key, value) in envs {
        cmd.env(key, value);
    }
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd.kill_on_drop(true);
    let memory = limits.memory_bytes;
    let fsize = limits.file_size_bytes;
    let cpu = limits.cpu_secs;
    unsafe {
        cmd.pre_exec(move || {
            if libc::setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            // Any finite RLIMIT_AS makes macOS reject the exec of a dynamically linked
            // binary with EINVAL (the dyld shared-cache reservation exceeds the limit); the
            // deployed container is Linux, where the bound is real and enforced.
            #[cfg(target_os = "linux")]
            set_rlimit(libc::RLIMIT_AS, memory)?;
            #[cfg(not(target_os = "linux"))]
            let _ = memory;
            set_rlimit(libc::RLIMIT_FSIZE, fsize)?;
            set_rlimit(libc::RLIMIT_CPU, cpu)?;
            set_rlimit(libc::RLIMIT_NOFILE, CHILD_MAX_OPEN_FILES)?;
            Ok(())
        });
    }
    let child = cmd.spawn().map_err(ChildError::Spawn)?;
    let pid = child.id();
    let waited = tokio::time::timeout(limits.deadline, child.wait_with_output()).await;
    match waited {
        Ok(Ok(out)) if out.status.success() => Ok(out),
        Ok(Ok(out)) => Err(ChildError::Failed(out)),
        Ok(Err(e)) => Err(ChildError::Spawn(e)),
        Err(_) => {
            if let Some(pid) = pid {
                unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
            }
            Err(ChildError::Timeout)
        }
    }
}

fn set_rlimit(resource: RlimitResource, value: u64) -> std::io::Result<()> {
    let lim = libc::rlimit {
        rlim_cur: value,
        rlim_max: value,
    };
    if unsafe { libc::setrlimit(resource, &lim) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn limits(deadline_ms: u64) -> Limits {
        Limits {
            deadline: Duration::from_millis(deadline_ms),
            memory_bytes: 512 * 1024 * 1024,
            file_size_bytes: 64 * 1024 * 1024,
            cpu_secs: 30,
        }
    }

    #[tokio::test]
    async fn completes_within_deadline() {
        let mut cmd = tokio::process::Command::new("/bin/echo");
        cmd.arg("ok");
        let out = run(cmd, &limits(5_000), &[]).await.unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "ok");
    }

    #[tokio::test]
    async fn deadline_kills_the_process_group() {
        let dir = tempfile::tempdir().unwrap();
        let probe_file = dir.path().join("grandchild.pid");
        let mut cmd = tokio::process::Command::new("/bin/sh");
        cmd.args(["-c", "sleep 30 & echo $! > \"$PROBE_FILE\"; wait"]);
        let started = std::time::Instant::now();
        let err = run(
            cmd,
            &limits(1_000),
            &[("PROBE_FILE", probe_file.to_str().unwrap())],
        )
        .await
        .unwrap_err();
        assert!(matches!(err, ChildError::Timeout));
        assert!(started.elapsed() < Duration::from_secs(5));

        let grandchild_pid: libc::pid_t = std::fs::read_to_string(&probe_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let poll_deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            let dead = unsafe { libc::kill(grandchild_pid, 0) } == -1
                && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH);
            if dead {
                break;
            }
            assert!(
                std::time::Instant::now() < poll_deadline,
                "grandchild pid {grandchild_pid} still alive 2s after the group kill"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }

    #[tokio::test]
    async fn nonzero_exit_is_failed_with_output() {
        let mut cmd = tokio::process::Command::new("/bin/sh");
        cmd.args(["-c", "echo boom >&2; exit 3"]);
        match run(cmd, &limits(5_000), &[]).await.unwrap_err() {
            ChildError::Failed(out) => {
                assert!(String::from_utf8_lossy(&out.stderr).contains("boom"))
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn environment_is_cleared() {
        std::env::set_var("CHILD_LEAK_PROBE_CLEARED", "secret");
        let cmd = tokio::process::Command::new("/usr/bin/env");
        let out = run(cmd, &limits(5_000), &[]).await.unwrap();
        assert!(!String::from_utf8_lossy(&out.stdout).contains("CHILD_LEAK_PROBE_CLEARED"));
        std::env::remove_var("CHILD_LEAK_PROBE_CLEARED");
    }

    #[tokio::test]
    async fn passed_envs_reach_the_child_but_inherited_ones_do_not() {
        std::env::set_var("CHILD_LEAK_PROBE_PASSTHROUGH", "secret");
        let mut cmd = tokio::process::Command::new("/bin/sh");
        cmd.args(["-c", "echo \"$HOME\""]);
        let out = run(cmd, &limits(5_000), &[("HOME", "/probe-home")])
            .await
            .unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "/probe-home");

        let cmd = tokio::process::Command::new("/usr/bin/env");
        let out = run(cmd, &limits(5_000), &[("HOME", "/probe-home")])
            .await
            .unwrap();
        let listing = String::from_utf8_lossy(&out.stdout);
        assert!(listing.contains("HOME=/probe-home"));
        assert!(!listing.contains("CHILD_LEAK_PROBE_PASSTHROUGH"));
        std::env::remove_var("CHILD_LEAK_PROBE_PASSTHROUGH");
    }
}
