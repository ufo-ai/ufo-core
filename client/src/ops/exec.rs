use std::collections::HashMap;
use std::env;
use std::fs;
use std::fs::File;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use flate2::read::GzDecoder;

mod command_safety;

use command_safety::dangerous_command_match;

use crate::config::Home;

const CA_CERT_ENV: &str = "UFO_EGRESS_CA_CERT";
const TRUST_BUNDLE_FILE: &str = "trust-bundle.pem";
const PEM_LINE_BYTES: usize = 64;
const X509_OVERRIDE: &str = "x509sslcertoverrideplatform";
const GH_BASH_ENV: &str = "gh-bash-env";
const GH_PATH_ENV: &str = "UFO_GH";
const GH_ORIGINAL_BASH_ENV: &str = "UFO_GH_ORIGINAL_BASH_ENV";
const GH_BASH_SETUP: &str = r#"if [ -n "${UFO_GH_ORIGINAL_BASH_ENV:-}" ]; then . "$UFO_GH_ORIGINAL_BASH_ENV"; fi
gh() { "$UFO_GH" "$@"; }
"#;
#[cfg(windows)]
const GH_FILE: &str = "gh.exe";
#[cfg(not(windows))]
const GH_FILE: &str = "gh";
const GH_ARCHIVE: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/gh.gz"));
const GH_LICENSE: &str = include_str!("../../licenses/github-cli.txt");
const GH_LICENSE_FILE: &str = "gh-LICENSE";
// libcurl tools (git, cargo) ignore CURL_CA_BUNDLE when they set their own CAINFO, so each needs its
// own override or a MITM'd host fails with unable to get local issuer certificate.
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
    safety_argv: Option<Vec<String>>,
}

/// Output lands in workdir files, not pipes: a backgrounded child inheriting the streams must not hold
/// the reply open. `timed_out` is the only teller — the group signal exits `128 + SIGKILL`, like a member's kill.
#[cfg(test)]
pub fn run(params: &str, workdir: &Path, cwd: &Path, timeout_s: u64) -> Result<Vec<u8>, String> {
    run_at_home(params, workdir, cwd, &Home::resolve(), timeout_s)
}

pub fn run_at_home(
    params: &str,
    workdir: &Path,
    cwd: &Path,
    home: &Home,
    timeout_s: u64,
) -> Result<Vec<u8>, String> {
    let parsed: Params =
        serde_json::from_str(params).map_err(|error| format!("bad exec params: {error}"))?;
    let argv: Vec<String> = parsed
        .argv
        .iter()
        .map(|value| {
            value.strip_prefix("$UFO_HOME/").map_or_else(
                || value.clone(),
                |relative| home.root.join(relative).to_string_lossy().into_owned(),
            )
        })
        .collect();
    let Some(program) = argv.first() else {
        return Err("exec argv is empty".into());
    };
    if parsed
        .safety_argv
        .as_deref()
        .is_some_and(dangerous_command_match)
    {
        return Err("exec refused dangerous command".into());
    }
    let gh = invokes_gh(&argv)
        .then(|| materialize_gh(workdir))
        .transpose()?;
    let client = is_ufo_run(&argv)
        .then(env::current_exe)
        .transpose()
        .map_err(|error| format!("could not locate this ufo client: {error}"))?;
    let executable = if let Some(client) = &client {
        client.as_path()
    } else if is_gh(program) {
        gh.as_deref().unwrap_or_else(|| Path::new(program))
    } else {
        Path::new(program)
    };
    let out_path = workdir.join("run.out");
    let err_path = workdir.join("run.err");
    let mut command = Command::new(executable);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::from(sink(&out_path)?))
        .stderr(Stdio::from(sink(&err_path)?))
        .env("UFO_OP_WORKDIR", workdir)
        .env("UFO_HOME", &home.root);
    for (name, value) in &parsed.env {
        if name == CA_CERT_ENV {
            continue;
        }
        command.env(name, value);
    }
    if let Some(gh) = &gh {
        let current = parsed
            .env
            .get("PATH")
            .map(std::ffi::OsString::from)
            .or_else(|| env::var_os("PATH"))
            .unwrap_or_default();
        let path = env::join_paths(
            std::iter::once(workdir.to_path_buf())
                .chain(env::split_paths(&current).filter(|path| !path.as_os_str().is_empty())),
        )
        .map_err(|error| format!("could not prepare gh PATH: {error}"))?;
        command.env("PATH", path);
        let bash_env = workdir.join(GH_BASH_ENV);
        fs::write(&bash_env, GH_BASH_SETUP)
            .map_err(|error| format!("could not prepare gh shell: {error}"))?;
        command.env(GH_PATH_ENV, gh);
        command.env("BASH_ENV", bash_env);
        match parsed
            .env
            .get("BASH_ENV")
            .map(std::ffi::OsString::from)
            .or_else(|| env::var_os("BASH_ENV"))
        {
            Some(original) => {
                command.env(GH_ORIGINAL_BASH_ENV, original);
            }
            None => {
                command.env_remove(GH_ORIGINAL_BASH_ENV);
            }
        }
    }
    if let Some(cert) = parsed.env.get(CA_CERT_ENV) {
        let bundle = workdir.join(TRUST_BUNDLE_FILE);
        fs::write(&bundle, trust_bundle(cert)?)
            .map_err(|error| format!("could not write {}: {error}", bundle.display()))?;
        for name in CA_CERT_CONSUMERS {
            command.env(name, &bundle);
        }
        let current_godebug = parsed
            .env
            .get("GODEBUG")
            .cloned()
            .or_else(|| std::env::var("GODEBUG").ok());
        command.env("GODEBUG", godebug(current_godebug.as_deref()));
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

fn godebug(current: Option<&str>) -> String {
    current
        .into_iter()
        .flat_map(|value| value.split(','))
        .filter(|setting| {
            !setting.is_empty()
                && setting
                    .split_once('=')
                    .is_none_or(|(name, _)| name != X509_OVERRIDE)
        })
        .chain(std::iter::once("x509sslcertoverrideplatform=1"))
        .collect::<Vec<_>>()
        .join(",")
}

fn materialize_gh(workdir: &Path) -> Result<PathBuf, String> {
    let path = workdir.join(GH_FILE);
    fs::write(workdir.join(GH_LICENSE_FILE), GH_LICENSE)
        .map_err(|error| format!("could not write bundled gh license: {error}"))?;
    if path.is_file() {
        return Ok(path);
    }
    let mut file =
        File::create(&path).map_err(|error| format!("could not create bundled gh: {error}"))?;
    io::copy(&mut GzDecoder::new(GH_ARCHIVE), &mut file)
        .map_err(|error| format!("could not unpack bundled gh: {error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("could not mark bundled gh executable: {error}"))?;
    }
    Ok(path)
}

fn is_gh(word: &str) -> bool {
    Path::new(word)
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.eq_ignore_ascii_case("gh") || name.eq_ignore_ascii_case("gh.exe"))
}

fn is_ufo_run(argv: &[String]) -> bool {
    argv.first()
        .and_then(|word| Path::new(word).file_name())
        .and_then(|name| name.to_str())
        .is_some_and(|name| {
            name.eq_ignore_ascii_case("ufo") || name.eq_ignore_ascii_case("ufo.exe")
        })
        && argv.get(1).map(String::as_str) == Some("run")
}

fn invokes_gh(argv: &[String]) -> bool {
    let Some(program) = argv.first() else {
        return false;
    };
    if is_gh(program) {
        return true;
    }
    let shell = Path::new(program)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if !matches!(
        shell.to_ascii_lowercase().as_str(),
        "sh" | "bash"
            | "zsh"
            | "fish"
            | "cmd"
            | "cmd.exe"
            | "powershell"
            | "powershell.exe"
            | "pwsh"
            | "pwsh.exe"
    ) {
        return false;
    }
    argv.iter().skip(1).any(|arg| {
        arg.split(|character: char| {
            character.is_ascii_whitespace()
                || matches!(
                    character,
                    '\'' | '"' | ';' | '|' | '&' | '(' | ')' | '<' | '>'
                )
        })
        .any(|word| word.eq_ignore_ascii_case("gh") || word.eq_ignore_ascii_case("gh.exe"))
    })
}

fn sink(path: &Path) -> Result<File, String> {
    File::create(path).map_err(|error| format!("could not create {}: {error}", path.display()))
}

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
    fn refuses_dangerous_shell_commands_before_spawn() {
        let dir = scratch("dangerous");
        let target = dir.join("keep");
        fs::write(&target, b"keep").unwrap();
        let params = serde_json::json!({
            "argv": ["/bin/bash", "-lc", format!("rm -f {}", target.display())],
            "env": {},
            "safety_argv": ["/bin/bash", "-lc", format!("rm -f {}", target.display())]
        })
        .to_string();

        assert_eq!(
            run(&params, &dir, Path::new("/tmp"), 30).unwrap_err(),
            "exec refused dangerous command"
        );
        assert!(target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_a_forced_rm_behind_an_unnamed_wrapper_before_spawn() {
        let dir = scratch("wrapped-dangerous");
        let target = dir.join("keep");
        fs::write(&target, b"keep").unwrap();
        let command = format!("nohup rm -rf {}", target.display());
        let params = serde_json::json!({
            "argv": ["/bin/bash", "-lc", &command],
            "env": {},
            "safety_argv": ["/bin/bash", "-lc", &command]
        })
        .to_string();

        assert_eq!(
            run(&params, &dir, Path::new("/tmp"), 30).unwrap_err(),
            "exec refused dangerous command"
        );
        assert!(target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn allows_nonforced_temp_directory_cleanup() {
        let dir = scratch("temp-cleanup");
        let target = dir.join("profile");
        fs::create_dir(&target).unwrap();
        let command = format!(
            "PROFILE={}; trap 'rm -r \"$PROFILE\"' EXIT",
            target.display()
        );
        let params = serde_json::json!({
            "argv": ["/bin/bash", "-lc", &command],
            "env": {},
            "safety_argv": ["/bin/bash", "-lc", &command]
        })
        .to_string();

        let result = parsed(&run(&params, &dir, Path::new("/tmp"), 30).unwrap());
        assert_eq!(result["exit_code"], 0);
        assert!(!target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn allows_runtime_cleanup_without_a_safety_command() {
        let dir = scratch("runtime-cleanup");
        let target = dir.join("remove");
        fs::write(&target, b"remove").unwrap();
        let params = serde_json::json!({
            "argv": ["/bin/bash", "-lc", format!("rm -f {}", target.display())],
            "env": {}
        })
        .to_string();

        let result = parsed(&run(&params, &dir, Path::new("/tmp"), 30).unwrap());
        assert_eq!(result["exit_code"], 0);
        assert!(!target.exists());
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
    fn go_uses_the_operation_bundle_without_dropping_other_debug_settings() {
        assert_eq!(
            godebug(Some("http2debug=1,x509sslcertoverrideplatform=0")),
            "http2debug=1,x509sslcertoverrideplatform=1"
        );
        assert_eq!(godebug(None), "x509sslcertoverrideplatform=1");
    }

    #[test]
    fn finds_gh_in_the_terminal_shell_command() {
        let argv = vec![
            "/bin/bash".to_string(),
            "-lc".to_string(),
            r#"cd /workspace && gh api graphql -f query='{repository(owner:\"metalcraftai\",name:\"ufo\"){issues(first:25){nodes{number}}}}'"#.to_string(),
        ];
        assert!(invokes_gh(&argv));
        assert!(!invokes_gh(&[
            "/bin/bash".to_string(),
            "-lc".to_string(),
            "git status".to_string()
        ]));
        assert!(invokes_gh(&["gh.exe".to_string()]));
        assert!(invokes_gh(&["/usr/local/bin/gh".to_string()]));
        assert!(!invokes_gh(&[
            "/bin/bash".to_string(),
            "-lc".to_string(),
            "echo /usr/local/bin/gh".to_string()
        ]));
        assert!(!invokes_gh(&["rg".to_string(), "gh".to_string()]));
    }

    #[test]
    fn recognizes_the_shared_run_verb_on_this_client() {
        assert!(is_ufo_run(&[
            "ufo".to_string(),
            "run".to_string(),
            "--".to_string(),
            "bash".to_string(),
        ]));
        assert!(is_ufo_run(&[
            "/usr/local/bin/ufo.exe".to_string(),
            "run".to_string(),
        ]));
        assert!(!is_ufo_run(&["ufo".to_string(), "fs".to_string()]));
    }

    #[cfg(all(unix, debug_assertions))]
    #[test]
    fn login_shell_uses_bundled_gh_after_replacing_path() {
        let dir = scratch("gh");
        let original_bash_env = dir.join("member-bash-env");
        fs::write(&original_bash_env, "export UFO_MEMBER_ENV=present\n").unwrap();
        let params = serde_json::json!({
            "argv": [
                "/bin/sh",
                "-c",
                "exec bash -c \"$1\" bash \"$2\"",
                "sh",
                "bash -lc \"$1\"",
                "PATH=/usr/bin:/bin; printf '%s:%s:' \"$UFO_MEMBER_ENV\" \"$(type -t gh)\"; gh"
            ],
            "env": {
                "BASH_ENV": original_bash_env,
                "GODEBUG": "http2debug=1",
                "UFO_EGRESS_CA_CERT": "-----BEGIN CERTIFICATE-----\nEGRESSCA\n-----END CERTIFICATE-----\n"
            }
        })
        .to_string();
        let reply = run(&params, &dir, Path::new("/tmp"), 30).unwrap();
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 0);
        let stdout = String::from_utf8(decoded(&result, "stdout_b64")).unwrap();
        assert_eq!(
            stdout,
            "present:function:ufo-gh-test:http2debug=1,x509sslcertoverrideplatform=1"
        );
        assert!(fs::read_to_string(dir.join(GH_LICENSE_FILE))
            .unwrap()
            .contains("Copyright (c) 2019 GitHub Inc."));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn reuses_materialized_gh_while_it_is_running() {
        let current = std::env::current_exe().unwrap();
        let dir = current
            .parent()
            .unwrap()
            .join(format!("ufo-exec-test-{}-running-gh", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(GH_FILE);
        fs::hard_link(current, &path).unwrap();

        let result = materialize_gh(&dir);

        assert_eq!(result.unwrap(), path);
        fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(all(unix, not(debug_assertions)))]
    #[test]
    fn release_contains_runnable_gh() {
        let reply = run(
            r#"{"argv":["/bin/sh","-c","command -v gh; gh version"],"env":{}}"#,
            &scratch("release-gh"),
            Path::new("/tmp"),
            30,
        )
        .unwrap();
        let result = parsed(&reply);
        assert_eq!(result["exit_code"], 0);
        let stdout = String::from_utf8(decoded(&result, "stdout_b64")).unwrap();
        assert!(stdout.contains("/gh\n"));
        assert!(stdout.contains("gh version 2.99.0"));
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
        let reply = run(
            r#"{"argv":["/bin/sh","-c","test \"$GIT_SSL_CAINFO\" = \"$SSL_CERT_FILE\" && test \"$CARGO_HTTP_CAINFO\" = \"$SSL_CERT_FILE\" && test \"$GODEBUG\" = \"http2debug=1,x509sslcertoverrideplatform=1\" && cat \"$SSL_CERT_FILE\"; printf %s \"${UFO_EGRESS_CA_CERT:-unset}\""],"env":{"GODEBUG":"http2debug=1,x509sslcertoverrideplatform=0","UFO_EGRESS_CA_CERT":"-----BEGIN CERTIFICATE-----\nEGRESSCA\n-----END CERTIFICATE-----\n"}}"#,
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
