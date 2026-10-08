use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde::de::DeserializeOwned;
use serde::Deserialize;

use crate::config::Home;
use crate::egress::{start_loopback_proxy, tls_config, Proxy};
use crate::trust;

pub const USAGE: &str = "usage: ufo proxy --session TOKEN [--env | --stop]";
const PROXY_URL_ENV: &str = "UFO_PROXY_URL";
const STATE_DIR: &str = "proxy";
const PID_FILE: &str = "daemon.pid";
const PORT_FILE: &str = "daemon.port";
const LOG_FILE: &str = "daemon.log";
const CA_FILE: &str = "ca.pem";
const PROXY_PASSWORD: &str = "ufo";
const READ_TIMEOUT: Duration = Duration::from_secs(10);
const READY_WAIT: Duration = Duration::from_secs(5);
const WAIT_POLL: Duration = Duration::from_millis(20);
const PROBE_TIMEOUT: Duration = Duration::from_millis(500);
const HTTPS_SCHEME: &str = "https://";
const HTTPS_PORT: u16 = 443;
const PROXY_SESSION_NAMES: [&str; 4] = ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"];
const NO_PROXY_NAMES: [&str; 2] = ["NO_PROXY", "no_proxy"];
const CA_CONSUMERS: [&str; 6] = trust::CA_CERT_CONSUMERS;

#[derive(Debug, PartialEq)]
pub enum Call {
    Start {
        session: String,
        env: bool,
    },
    Stop,
    Serve {
        state: PathBuf,
        host: String,
        port: u16,
    },
}

#[derive(Deserialize)]
struct SessionSelf {
    env: BTreeMap<String, String>,
}

#[derive(Deserialize)]
struct Ca {
    ca_pem: String,
}

#[derive(Deserialize)]
struct Refusal {
    error: RefusalDetail,
}

#[derive(Deserialize)]
struct RefusalDetail {
    message: String,
}

/// Runs `ufo proxy`: prints a session's environment behind a loopback daemon, or stops it.
pub fn main(args: &[String], url_default: Option<&str>) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(error) => {
            eprintln!("{error}");
            return 2;
        }
    };
    let result = match call {
        Call::Start { session, env } => start(&session, env, url_default),
        Call::Stop => stop(),
        Call::Serve { state, host, port } => serve(&state, host, port),
    };
    match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("ufo proxy: {error}");
            1
        }
    }
}

/// Reads `ufo proxy`'s arguments; `--serve` is the daemon's own re-exec.
pub fn parse(args: &[String]) -> Result<Call, String> {
    if let [flag, state, host, port] = args {
        if flag == "--serve" {
            return Ok(Call::Serve {
                state: PathBuf::from(state),
                host: host.clone(),
                port: port.parse().map_err(|_| USAGE.to_string())?,
            });
        }
    }
    let mut session = None;
    let mut export = false;
    let mut stop = false;
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--session" => {
                session = Some(
                    rest.next()
                        .filter(|token| !token.is_empty())
                        .ok_or_else(|| USAGE.to_string())?
                        .clone(),
                );
            }
            "--env" => export = true,
            "--stop" => stop = true,
            _ => return Err(USAGE.to_string()),
        }
    }
    match (session, export, stop) {
        (_, false, true) => Ok(Call::Stop),
        (Some(session), env, false) => Ok(Call::Start { session, env }),
        _ => Err(USAGE.to_string()),
    }
}

pub(crate) fn proxy_url(env: Option<String>, url_default: Option<&str>) -> Result<String, String> {
    env.or_else(|| url_default.map(str::to_string))
        .map(|url| url.trim_end_matches('/').to_string())
        .ok_or_else(|| format!("Set {PROXY_URL_ENV} to the proxy service's URL."))
}

fn start(session: &str, export: bool, url_default: Option<&str>) -> Result<(), String> {
    let url = proxy_url(
        env::var(PROXY_URL_ENV)
            .ok()
            .filter(|value| !value.is_empty()),
        url_default,
    )?;
    let (host, port) = upstream(&url)?;
    let agent = ureq::AgentBuilder::new().timeout_read(READ_TIMEOUT).build();
    let view: SessionSelf = fetched(
        agent
            .get(&format!("{url}/v1/sessions/self"))
            .set("Authorization", &format!("Bearer {session}")),
    )?;
    if let Some(name) = view.env.keys().find(|name| !shell_name(name)) {
        return Err(format!(
            "the proxy service answered a variable no shell can set: {name:?}."
        ));
    }
    let ca: Ca = fetched(agent.get(&format!("{url}/v1/proxy/ca")))?;
    let state = Home::resolve().root.join(STATE_DIR);
    fs::create_dir_all(&state)
        .map_err(|error| format!("could not create {}: {error}", state.display()))?;
    let ca_path = state.join(CA_FILE);
    fs::write(&ca_path, trust::trust_bundle(&ca.ca_pem)?)
        .map_err(|error| format!("could not write {}: {error}", ca_path.display()))?;
    let local = match running(&state) {
        Some(local) => local,
        None => launched(&state, &host, port)?,
    };
    print!("{}", rendered(&view.env, local, session, &ca_path, export));
    Ok(())
}

fn upstream(url: &str) -> Result<(String, u16), String> {
    let invalid = || format!("{PROXY_URL_ENV} must be an https URL with a host, not {url}");
    let authority = url
        .strip_prefix(HTTPS_SCHEME)
        .ok_or_else(invalid)?
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default();
    let (host, port) = match authority.rsplit_once(':') {
        Some((host, port)) => (host, port.parse().map_err(|_| invalid())?),
        None => (authority, HTTPS_PORT),
    };
    if host.is_empty() || host.contains('@') {
        return Err(invalid());
    }
    Ok((host.to_string(), port))
}

fn fetched<T: DeserializeOwned>(request: ureq::Request) -> Result<T, String> {
    let url = request.url().to_string();
    match request.call() {
        Ok(response) => {
            let body = response
                .into_string()
                .map_err(|error| format!("could not read {url}: {error}"))?;
            serde_json::from_str(&body)
                .map_err(|error| format!("{url} answered an unreadable body: {error}"))
        }
        Err(ureq::Error::Status(status, response)) => {
            let body = response.into_string().unwrap_or_default();
            let message = serde_json::from_str::<Refusal>(&body)
                .map(|refusal| refusal.error.message)
                .unwrap_or(body);
            Err(format!("the proxy service answered {status}: {message}"))
        }
        Err(error) => Err(format!("could not reach {url}: {error}")),
    }
}

fn shell_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|first| first == '_' || first.is_ascii_alphabetic())
        && chars.all(|rest| rest == '_' || rest.is_ascii_alphanumeric())
}

fn running(state: &Path) -> Option<u16> {
    let pid = read_number::<u32>(&state.join(PID_FILE))?;
    let port = read_number::<u16>(&state.join(PORT_FILE))?;
    (process_alive(pid) && accepts(port)).then_some(port)
}

fn launched(state: &Path, host: &str, port: u16) -> Result<u16, String> {
    let port_path = state.join(PORT_FILE);
    let log_path = state.join(LOG_FILE);
    for stale in [state.join(PID_FILE), port_path.clone()] {
        let _ = fs::remove_file(stale);
    }
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("could not open {}: {error}", log_path.display()))?;
    let executable = env::current_exe()
        .map_err(|error| format!("could not locate the ufo executable: {error}"))?;
    let mut command = Command::new(executable);
    command
        .arg("proxy")
        .arg("--serve")
        .arg(state)
        .arg(host)
        .arg(port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(log));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("could not start the local proxy: {error}"))?;
    let started = Instant::now();
    loop {
        if let Some(local) = read_number::<u16>(&port_path) {
            return Ok(local);
        }
        if matches!(child.try_wait(), Ok(Some(_))) || started.elapsed() >= READY_WAIT {
            let _ = child.kill();
            return Err(format!(
                "the local proxy did not start; its log is {}",
                log_path.display()
            ));
        }
        thread::sleep(WAIT_POLL);
    }
}

pub(crate) fn rendered(
    env: &BTreeMap<String, String>,
    port: u16,
    token: &str,
    ca_path: &Path,
    export: bool,
) -> String {
    let proxy = format!(
        "http://{token}:{PROXY_PASSWORD}@{}:{port}",
        Ipv4Addr::LOCALHOST
    );
    let bundle = ca_path.display().to_string();
    let godebug = trust::godebug(None);
    let session_names =
        |name: &str| PROXY_SESSION_NAMES.contains(&name) || NO_PROXY_NAMES.contains(&name);
    PROXY_SESSION_NAMES
        .iter()
        .map(|name| (*name, proxy.as_str()))
        .chain(
            NO_PROXY_NAMES
                .iter()
                .filter_map(|name| env.get(*name).map(|value| (*name, value.as_str()))),
        )
        .chain(
            env.iter()
                .filter(|(name, _)| !session_names(name))
                .map(|(name, value)| (name.as_str(), value.as_str())),
        )
        .chain(CA_CONSUMERS.iter().map(|name| (*name, bundle.as_str())))
        .chain(std::iter::once(("GODEBUG", godebug.as_str())))
        .map(|(name, value)| match export {
            true => format!("export {name}={}\n", quoted(value)),
            false => format!("{name}={value}\n"),
        })
        .collect()
}

pub(crate) fn quoted(value: &str) -> String {
    format!("'{}'", value.replace('\'', r"'\''"))
}

fn serve(state: &Path, host: String, port: u16) -> Result<(), String> {
    let proxy = Proxy {
        tls: true,
        host,
        port,
        authorization: None,
    };
    let local = start_loopback_proxy(proxy, tls_config(None)?)?;
    write_atomic(&state.join(PID_FILE), &std::process::id().to_string())?;
    write_atomic(&state.join(PORT_FILE), &local.to_string())?;
    loop {
        thread::park();
    }
}

#[cfg(unix)]
fn stop() -> Result<(), String> {
    let state = Home::resolve().root.join(STATE_DIR);
    if let (Some(port), Some(pid)) = (running(&state), read_number::<i32>(&state.join(PID_FILE))) {
        if unsafe { libc::kill(pid, libc::SIGTERM) } != 0 {
            return Err(format!(
                "could not stop the local proxy: {}",
                std::io::Error::last_os_error()
            ));
        }
        let started = Instant::now();
        while accepts(port) {
            if started.elapsed() >= READY_WAIT {
                return Err(format!("the local proxy (pid {pid}) did not stop."));
            }
            thread::sleep(WAIT_POLL);
        }
    }
    for name in [PID_FILE, PORT_FILE] {
        let _ = fs::remove_file(state.join(name));
    }
    Ok(())
}

#[cfg(not(unix))]
fn stop() -> Result<(), String> {
    Err("--stop is not available on this platform.".to_string())
}

fn accepts(port: u16) -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from((Ipv4Addr::LOCALHOST, port)),
        PROBE_TIMEOUT,
    )
    .is_ok()
}

#[cfg(unix)]
fn process_alive(pid: u32) -> bool {
    let Ok(pid) = i32::try_from(pid) else {
        return false;
    };
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(not(unix))]
fn process_alive(_pid: u32) -> bool {
    true
}

fn read_number<T: std::str::FromStr>(path: &Path) -> Option<T> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

fn write_atomic(path: &Path, value: &str) -> Result<(), String> {
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    fs::write(&temporary, value)
        .and_then(|_| fs::rename(&temporary, path))
        .map_err(|error| format!("could not write {}: {error}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn parses_start_env_and_stop() {
        assert_eq!(
            parse(&args(&["--session", "tok"])).unwrap(),
            Call::Start {
                session: "tok".to_string(),
                env: false
            }
        );
        assert_eq!(
            parse(&args(&["--env", "--session", "tok"])).unwrap(),
            Call::Start {
                session: "tok".to_string(),
                env: true
            }
        );
        assert_eq!(parse(&args(&["--stop"])).unwrap(), Call::Stop);
        assert_eq!(
            parse(&args(&["--session", "tok", "--stop"])).unwrap(),
            Call::Stop
        );
        assert_eq!(
            parse(&args(&["--serve", "/tmp/state", "proxy.test", "443"])).unwrap(),
            Call::Serve {
                state: PathBuf::from("/tmp/state"),
                host: "proxy.test".to_string(),
                port: 443
            }
        );
    }

    #[test]
    fn rejects_a_missing_session_and_mixed_flags() {
        for refused in [
            &[][..],
            &["--env"][..],
            &["--session"][..],
            &["--session", ""][..],
            &["--session", "tok", "--env", "--stop"][..],
            &["--session", "tok", "extra"][..],
            &["--serve", "/tmp/state", "proxy.test", "port"][..],
        ] {
            assert_eq!(parse(&args(refused)).unwrap_err(), USAGE, "{refused:?}");
        }
    }

    #[test]
    fn proxy_url_prefers_the_env_then_the_build_default_then_refuses() {
        assert_eq!(
            proxy_url(
                Some("https://proxy.test/".to_string()),
                Some("https://built.test")
            )
            .unwrap(),
            "https://proxy.test"
        );
        assert_eq!(
            proxy_url(None, Some("https://built.test")).unwrap(),
            "https://built.test"
        );
        assert_eq!(
            proxy_url(None, None).unwrap_err(),
            "Set UFO_PROXY_URL to the proxy service's URL."
        );
    }

    #[test]
    fn the_upstream_is_the_proxy_urls_host_and_port() {
        assert_eq!(
            upstream("https://proxy.test").unwrap(),
            ("proxy.test".to_string(), 443)
        );
        assert_eq!(
            upstream("https://127.0.0.1:8443/base").unwrap(),
            ("127.0.0.1".to_string(), 8443)
        );
        for refused in [
            "http://proxy.test",
            "https://",
            "https://proxy.test:port",
            "https://user@proxy.test",
        ] {
            assert!(upstream(refused).is_err(), "{refused}");
        }
    }

    #[test]
    fn only_shell_names_are_exported() {
        assert!(shell_name("GH_TOKEN"));
        assert!(shell_name("_x1"));
        assert!(!shell_name("1X"));
        assert!(!shell_name("A;B"));
        assert!(!shell_name(""));
    }

    #[test]
    fn rendered_covers_every_variable_in_order_and_quotes_for_eval() {
        let env: BTreeMap<String, String> = [
            ("HTTPS_PROXY", "https://tok:ufo@proxy.test"),
            ("HTTP_PROXY", "https://tok:ufo@proxy.test"),
            ("https_proxy", "https://tok:ufo@proxy.test"),
            ("http_proxy", "https://tok:ufo@proxy.test"),
            ("NO_PROXY", "localhost,127.0.0.1,::1"),
            ("no_proxy", "localhost,127.0.0.1,::1"),
            ("GH_TOKEN", "ufo-sentinel-x"),
            ("ANTHROPIC_API_KEY", "it's"),
        ]
        .into_iter()
        .map(|(name, value)| (name.to_string(), value.to_string()))
        .collect();
        let ca = Path::new("/home/me/.ufo/proxy/ca.pem");
        let proxy = "http://tok:ufo@127.0.0.1:4321";
        let expected = [
            ("HTTPS_PROXY", proxy),
            ("HTTP_PROXY", proxy),
            ("https_proxy", proxy),
            ("http_proxy", proxy),
            ("NO_PROXY", "localhost,127.0.0.1,::1"),
            ("no_proxy", "localhost,127.0.0.1,::1"),
            ("ANTHROPIC_API_KEY", "it's"),
            ("GH_TOKEN", "ufo-sentinel-x"),
            ("SSL_CERT_FILE", "/home/me/.ufo/proxy/ca.pem"),
            ("REQUESTS_CA_BUNDLE", "/home/me/.ufo/proxy/ca.pem"),
            ("CURL_CA_BUNDLE", "/home/me/.ufo/proxy/ca.pem"),
            ("NODE_EXTRA_CA_CERTS", "/home/me/.ufo/proxy/ca.pem"),
            ("GIT_SSL_CAINFO", "/home/me/.ufo/proxy/ca.pem"),
            ("CARGO_HTTP_CAINFO", "/home/me/.ufo/proxy/ca.pem"),
            ("GODEBUG", "x509sslcertoverrideplatform=1"),
        ];
        let plain: String = expected
            .iter()
            .map(|(name, value)| format!("{name}={value}\n"))
            .collect();
        assert_eq!(rendered(&env, 4321, "tok", ca, false), plain);
        let exported = rendered(&env, 4321, "tok", ca, true);
        assert_eq!(exported.lines().count(), expected.len());
        assert!(exported.starts_with("export HTTPS_PROXY='http://tok:ufo@127.0.0.1:4321'\n"));
        assert!(exported.contains("\nexport ANTHROPIC_API_KEY='it'\\''s'\n"));
        assert!(exported.ends_with("\nexport GODEBUG='x509sslcertoverrideplatform=1'\n"));
    }

    #[cfg(unix)]
    #[test]
    fn quoted_values_survive_a_shell() {
        let value = "a'b $HOME `x` \"c\"";
        let output = Command::new("/bin/sh")
            .arg("-c")
            .arg(format!("printf %s {}", quoted(value)))
            .output()
            .unwrap();
        assert_eq!(String::from_utf8(output.stdout).unwrap(), value);
    }
}
