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
const TRUST_BUNDLE_FILE: &str = "trust-bundle.pem";
const PEM_LINE_BYTES: usize = 64;
// The CA-bundle env vars the toolchains read. libcurl tools (git, cargo) ignore CURL_CA_BUNDLE when
// they set their own CAINFO, so each needs its own override or a MITM'd host (a cache-fronted
// registry, or git rewritten to the cache) fails with "unable to get local issuer certificate".
const CA_CERT_CONSUMERS: [&str; 6] = [
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "GIT_SSL_CAINFO",
    "CARGO_HTTP_CAINFO",
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
/// the `{"exit_code", "timed_out", "stdout_b64", "stderr_b64"}` reply JSON. Output lands in workdir
/// files, not pipes: a backgrounded child inheriting the streams must not hold the reply open after
/// the command itself exits.
///
/// `timed_out` says the deadline here ended the command, which nothing else can tell: the group
/// signal makes it exit `128 + SIGKILL`, the same code a member's own `kill` produces. The server
/// reads it to report the budget that expired, and the work a command detached into its own group
/// keeps running behind that report.
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
        let bundle = workdir.join(TRUST_BUNDLE_FILE);
        fs::write(&bundle, trust_bundle(cert)?)
            .map_err(|error| format!("could not write {}: {error}", bundle.display()))?;
        for name in CA_CERT_CONSUMERS {
            command.env(name, &bundle);
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
        Err(error) => {
            let detail = error.to_string();
            return Ok(reply(SPAWN_FAILED_CODE, false, &[], detail.as_bytes()));
        }
    };
    let (code, timed_out) = await_exit(&mut child, timeout_s);
    let out = fs::read(&out_path).unwrap_or_default();
    let err = fs::read(&err_path).unwrap_or_default();
    Ok(reply(code, timed_out, &out, &err))
}

/// What a command verifies TLS with: this machine's own trust store, then the deploy's egress CA.
/// Both halves are load-bearing — the CA signs the leaves the proxy mints for the hosts it
/// terminates, and the roots cover every host it tunnels untouched, whose real certificate the
/// command sees. The container carriers merge the same two by installing the CA into the system
/// store; nothing here touches the member's, so the merged copy lives in the op's workdir and the
/// environment points at it.
fn trust_bundle(ca_cert: &str) -> Result<String, String> {
    let roots = rustls_native_certs::load_native_certs()
        .map_err(|error| format!("could not read this machine's trust store: {error}"))?;
    if roots.is_empty() {
        return Err("this machine's trust store holds no certificates".into());
    }
    let mut bundle: String = roots.iter().map(|root| pem(root.as_ref())).collect();
    bundle.push_str(ca_cert);
    if !bundle.ends_with('\n') {
        bundle.push('\n');
    }
    Ok(bundle)
}

fn pem(certificate: &[u8]) -> String {
    let encoded = STANDARD.encode(certificate);
    let mut out = String::from("-----BEGIN CERTIFICATE-----\n");
    for line in encoded.as_bytes().chunks(PEM_LINE_BYTES) {
        out.push_str(std::str::from_utf8(line).expect("base64 is ascii"));
        out.push('\n');
    }
    out.push_str("-----END CERTIFICATE-----\n");
    out
}

fn sink(path: &Path) -> Result<File, String> {
    File::create(path).map_err(|error| format!("could not create {}: {error}", path.display()))
}

/// The command's exit code, and whether the deadline is what ended it.
fn await_exit(child: &mut Child, timeout_s: u64) -> (i32, bool) {
    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return (exit_code(status), false),
            Ok(None) => {
                if Instant::now() >= deadline {
                    kill(child);
                    return (child.wait().map(exit_code).unwrap_or(1), true);
                }
                thread::sleep(EXIT_POLL);
            }
            Err(_) => return (1, false),
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

fn reply(exit_code: i32, timed_out: bool, stdout: &[u8], stderr: &[u8]) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "exit_code": exit_code,
        "timed_out": timed_out,
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
        let result = parsed(&reply(0, false, b"hi\n", &[0x00, 0xff]));
        assert_eq!(result["stdout_b64"], "aGkK");
        assert_eq!(result["stderr_b64"], "AP8=");
        assert_eq!(parsed(&reply(0, false, &[], &[]))["stdout_b64"], "");
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
        assert_eq!(result["timed_out"], false);
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
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 128 + libc::SIGKILL);
        assert_eq!(result["timed_out"], true);
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
        assert_eq!(result["timed_out"], false);
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

    #[test]
    fn trust_bundle_carries_this_machine_and_the_egress_ca() {
        let bundle =
            trust_bundle("-----BEGIN CERTIFICATE-----\nEGRESSCA\n-----END CERTIFICATE-----\n")
                .unwrap();
        assert!(bundle.matches("BEGIN CERTIFICATE").count() > 1);
        assert!(bundle.ends_with("EGRESSCA\n-----END CERTIFICATE-----\n"));
    }

    #[test]
    fn every_root_encodes_as_a_readable_certificate() {
        let roots = rustls_native_certs::load_native_certs().unwrap();
        for root in &roots {
            let encoded = pem(root.as_ref());
            let body: String = encoded
                .lines()
                .filter(|line| !line.starts_with("-----"))
                .collect();
            assert!(encoded
                .lines()
                .all(|line| line.len() <= PEM_LINE_BYTES || line.starts_with("-----")));
            assert_eq!(STANDARD.decode(body).unwrap(), root.as_ref());
        }
    }

    #[cfg(unix)]
    #[test]
    fn materializes_the_trust_bundle() {
        let dir = scratch("ca");
        // The `test` guards prove git and cargo see the same bundle as curl/openssl; if either var
        // is unset or points elsewhere the `cat` is skipped and the certificate assertions fail.
        let reply = run(
            r#"{"argv":["/bin/sh","-c","test \"$GIT_SSL_CAINFO\" = \"$SSL_CERT_FILE\" && test \"$CARGO_HTTP_CAINFO\" = \"$SSL_CERT_FILE\" && cat \"$SSL_CERT_FILE\"; printf %s \"${UFO_EGRESS_CA_CERT:-unset}\""],"env":{"UFO_EGRESS_CA_CERT":"-----BEGIN CERTIFICATE-----\nEGRESSCA\n-----END CERTIFICATE-----\n"}}"#,
            &dir,
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        let stdout = String::from_utf8(decoded(&parsed(&reply), "stdout_b64")).unwrap();
        assert!(stdout.matches("BEGIN CERTIFICATE").count() > 1);
        assert!(stdout.ends_with("EGRESSCA\n-----END CERTIFICATE-----\nunset"));
    }
}
