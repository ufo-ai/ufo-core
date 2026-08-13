//! The exec primitive: one command as the member's own subprocess.

use std::collections::HashMap;
use std::fs;
use std::fs::File;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;

const CA_CERT_ENV: &str = "UFO_EGRESS_CA_CERT";
const CA_CERT_FILE: &str = "egress-ca.pem";
const CA_CERT_CONSUMERS: [&str; 4] = [
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
];
const SPAWN_FAILED_CODE: i32 = 127;
const EXIT_POLL: Duration = Duration::from_millis(20);

#[derive(serde::Deserialize)]
struct Params {
    argv: Vec<String>,
    #[serde(default)]
    env: HashMap<String, String>,
}

/// Run the params' argv with its env overlaid, in `cwd`, group-killed at `timeout_s`, and answer
/// the `{"exit_code", "stdout_b64", "stderr_b64"}` reply JSON. Output lands in workdir files, not
/// pipes: a backgrounded child inheriting the streams must not hold the reply open after the
/// command itself exits.
pub fn run(params: &str, workdir: &Path, cwd: &Path, timeout_s: u64) -> Result<Vec<u8>, String> {
    let parsed: Params =
        serde_json::from_str(params).map_err(|error| format!("bad exec params: {error}"))?;
    let Some(program) = parsed.argv.first() else {
        return Err("exec argv is empty".into());
    };
    let out_path = workdir.join("run.out");
    let err_path = workdir.join("run.err");
    let mut command = Command::new(program);
    command
        .args(&parsed.argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::from(sink(&out_path)?))
        .stderr(Stdio::from(sink(&err_path)?))
        .env("UFO_OP_WORKDIR", workdir);
    for (name, value) in &parsed.env {
        if name == CA_CERT_ENV {
            continue;
        }
        command.env(name, value);
    }
    if let Some(cert) = parsed.env.get(CA_CERT_ENV) {
        let pem = workdir.join(CA_CERT_FILE);
        fs::write(&pem, cert)
            .map_err(|error| format!("could not write {}: {error}", pem.display()))?;
        for name in CA_CERT_CONSUMERS {
            command.env(name, &pem);
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                if libc::setpgid(0, 0) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => return Ok(reply(SPAWN_FAILED_CODE, &[], error.to_string().as_bytes())),
    };
    let code = await_exit(&mut child, timeout_s);
    let out = fs::read(&out_path).unwrap_or_default();
    let err = fs::read(&err_path).unwrap_or_default();
    Ok(reply(code, &out, &err))
}

fn sink(path: &Path) -> Result<File, String> {
    File::create(path).map_err(|error| format!("could not create {}: {error}", path.display()))
}

fn await_exit(child: &mut Child, timeout_s: u64) -> i32 {
    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return exit_code(status),
            Ok(None) => {
                if Instant::now() >= deadline {
                    kill(child);
                    return child.wait().map(exit_code).unwrap_or(1);
                }
                thread::sleep(EXIT_POLL);
            }
            Err(_) => return 1,
        }
    }
}

#[cfg(unix)]
fn kill(child: &mut Child) {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
}

#[cfg(not(unix))]
fn kill(child: &mut Child) {
    let _ = child.kill();
}

fn exit_code(status: ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return 128 + signal;
        }
    }
    status.code().unwrap_or(1)
}

fn reply(exit_code: i32, stdout: &[u8], stderr: &[u8]) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "exit_code": exit_code,
        "stdout_b64": STANDARD.encode(stdout),
        "stderr_b64": STANDARD.encode(stderr),
    }))
    .expect("exec reply serializes")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ufo-exec-test-{}-{name}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn parsed(reply: &[u8]) -> serde_json::Value {
        serde_json::from_slice(reply).unwrap()
    }

    fn decoded(result: &serde_json::Value, key: &str) -> Vec<u8> {
        STANDARD.decode(result[key].as_str().unwrap()).unwrap()
    }

    #[test]
    fn reply_is_padded_standard_base64() {
        let result = parsed(&reply(0, b"hi\n", &[0x00, 0xff]));
        assert_eq!(result["stdout_b64"], "aGkK");
        assert_eq!(result["stderr_b64"], "AP8=");
        assert_eq!(parsed(&reply(0, &[], &[]))["stdout_b64"], "");
    }

    #[cfg(unix)]
    #[test]
    fn echoes() {
        let reply = run(
            r#"{"argv":["/bin/echo","hi"],"env":{}}"#,
            &scratch("echo"),
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 0);
        assert_eq!(decoded(&result, "stdout_b64"), b"hi\n");
        assert_eq!(result["stderr_b64"], "");
    }

    #[cfg(unix)]
    #[test]
    fn answers_while_a_backgrounded_child_still_runs() {
        let started = Instant::now();
        let reply = run(
            r#"{"argv":["/bin/sh","-c","sleep 5 & echo done"],"env":{}}"#,
            &scratch("background"),
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        assert!(started.elapsed() < Duration::from_secs(3));
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 0);
        assert_eq!(decoded(&result, "stdout_b64"), b"done\n");
    }

    #[cfg(unix)]
    #[test]
    fn kills_the_group_at_the_timeout() {
        let started = Instant::now();
        let reply = run(
            r#"{"argv":["/bin/sh","-c","sleep 30"],"env":{}}"#,
            &scratch("timeout"),
            Path::new("/tmp"),
            1,
        )
        .unwrap();
        assert!(started.elapsed() < Duration::from_secs(10));
        assert_eq!(parsed(&reply)["exit_code"], 128 + libc::SIGKILL);
    }

    #[cfg(unix)]
    #[test]
    fn reports_a_missing_program_as_127() {
        let reply = run(
            r#"{"argv":["/nonexistent-ufo-test"],"env":{}}"#,
            &scratch("missing"),
            Path::new("/tmp"),
            5,
        )
        .unwrap();
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 127);
        assert_ne!(result["stderr_b64"], "");
    }

    #[cfg(unix)]
    #[test]
    fn overlays_env_and_workdir() {
        let dir = scratch("env");
        let reply = run(
            r#"{"argv":["/bin/sh","-c","printf %s \"$FOO:$UFO_OP_WORKDIR\""],"env":{"FOO":"bar"}}"#,
            &dir,
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        let expected = format!("bar:{}", dir.display());
        assert_eq!(decoded(&parsed(&reply), "stdout_b64"), expected.as_bytes());
    }

    #[cfg(unix)]
    #[test]
    fn materializes_the_ca_cert() {
        let dir = scratch("ca");
        let reply = run(
            r#"{"argv":["/bin/sh","-c","cat \"$SSL_CERT_FILE\"; printf %s \"${UFO_EGRESS_CA_CERT:-unset}\""],"env":{"UFO_EGRESS_CA_CERT":"PEMDATA"}}"#,
            &dir,
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        assert_eq!(decoded(&parsed(&reply), "stdout_b64"), b"PEMDATAunset");
    }
}
