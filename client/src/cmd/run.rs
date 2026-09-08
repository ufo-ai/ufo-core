use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, SystemTime};

const PROXY_ENV_NAMES: [&str; 4] = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"];
const READY_WAIT: Duration = Duration::from_secs(5);
const WAIT_POLL: Duration = Duration::from_millis(20);
const STOP_GRACE: Duration = Duration::from_secs(5);
const TASK_SWEEP_AGE: Duration = Duration::from_secs(60 * 60);
pub const USAGE: &str = "usage: ufo run [--task PATH] [--detach] -- COMMAND [ARG...]";

#[derive(Debug, PartialEq)]
enum Mode {
    Direct,
    Task(PathBuf),
    Supervise(PathBuf),
}

#[derive(Debug, PartialEq)]
struct Call {
    mode: Mode,
    detach: bool,
    argv: Vec<String>,
}

pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(error) => {
            eprintln!("{error}");
            return 2;
        }
    };
    let result = match call.mode {
        Mode::Direct => direct(&call.argv),
        Mode::Task(base) => task(&base, &call.argv, call.detach),
        Mode::Supervise(base) => {
            let result = supervise(&base, &call.argv);
            let _ = fs::remove_file(suffixed(&base, "lock"));
            result
        }
    };
    match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("ufo run: {error}");
            1
        }
    }
}

fn parse(args: &[String]) -> Result<Call, String> {
    let mut task = None;
    let mut supervise = None;
    let mut detach = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--" => {
                index += 1;
                break;
            }
            "--task" => {
                task = Some(PathBuf::from(
                    args.get(index + 1).ok_or_else(|| USAGE.to_string())?,
                ));
                index += 2;
            }
            "--detach" => {
                detach = true;
                index += 1;
            }
            "--supervise" => {
                supervise = Some(PathBuf::from(
                    args.get(index + 1).ok_or_else(|| USAGE.to_string())?,
                ));
                index += 2;
            }
            word if !word.starts_with('-') => break,
            _ => return Err(USAGE.to_string()),
        }
    }
    let argv = args[index..].to_vec();
    if argv.is_empty() || task.is_some() && supervise.is_some() || detach && task.is_none() {
        return Err(USAGE.to_string());
    }
    let mode = match (task, supervise) {
        (Some(base), None) => Mode::Task(base),
        (None, Some(base)) if !detach => Mode::Supervise(base),
        (None, None) if !detach => Mode::Direct,
        _ => return Err(USAGE.to_string()),
    };
    Ok(Call { mode, detach, argv })
}

fn direct(argv: &[String]) -> Result<i32, String> {
    let status = prepared(argv)?.status().map_err(spawn_error)?;
    Ok(exit_code(status))
}

fn task(base: &Path, argv: &[String], detach: bool) -> Result<i32, String> {
    let parent = base
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .map_err(|error| format!("could not create {}: {error}", parent.display()))?;
    sweep(parent);
    let pid_path = suffixed(base, "pid");
    let exit_path = suffixed(base, "exit");
    let lock_path = suffixed(base, "lock");
    clear_abandoned_lock(&lock_path, &pid_path);
    let mut launched = None;
    if !pid_path.exists() {
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock_path)
        {
            Ok(lock) => match launch_supervisor(base, argv, lock) {
                Ok(child) => launched = Some(child),
                Err(error) => {
                    let _ = fs::remove_file(&lock_path);
                    return Err(error);
                }
            },
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => {
                return Err(format!("could not lock {}: {error}", base.display()));
            }
        }
    }
    let pid = wait_for_pid(&pid_path, &exit_path)?;
    if detach {
        if !exit_path.exists() && !process_alive(pid) {
            return Err("the command ended without an exit code".to_string());
        }
        println!("{pid}");
        return Ok(0);
    }
    if let Some(mut child) = launched {
        let _ = child.wait();
    } else {
        wait_for_exit(pid, &exit_path)?;
    }
    copy_log(base)?;
    read_exit(&exit_path)
}

fn launch_supervisor(base: &Path, argv: &[String], mut lock: File) -> Result<Child, String> {
    let executable = env::current_exe()
        .map_err(|error| format!("could not locate the ufo executable: {error}"))?;
    let mut command = Command::new(executable);
    command
        .arg("run")
        .arg("--supervise")
        .arg(base)
        .arg("--")
        .args(argv)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
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
        .map_err(|error| format!("could not start the task supervisor: {error}"))?;
    if let Err(error) = lock
        .write_all(child.id().to_string().as_bytes())
        .and_then(|_| lock.sync_all())
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("could not record the task supervisor: {error}"));
    }
    Ok(child)
}

fn supervise(base: &Path, argv: &[String]) -> Result<i32, String> {
    let pid_path = suffixed(base, "pid");
    let log_path = suffixed(base, "log");
    let exit_path = suffixed(base, "exit");
    let lock_path = suffixed(base, "lock");
    let mut log = File::create(&log_path)
        .map_err(|error| format!("could not create {}: {error}", log_path.display()))?;
    let stopped = stop_flag()?;
    write_atomic(&pid_path, &std::process::id().to_string())?;
    let _ = fs::remove_file(lock_path);
    let mut command = match prepared(argv) {
        Ok(command) => command,
        Err(error) => return journal_failure(&mut log, &exit_path, &error, 1),
    };
    if stopped.load(std::sync::atomic::Ordering::Relaxed) {
        write_atomic(&exit_path, "143")?;
        return Ok(143);
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().map_err(|error| {
            format!("could not open the task log: {error}")
        })?))
        .stderr(Stdio::from(log.try_clone().map_err(|error| {
            format!("could not open the task log: {error}")
        })?));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                if libc::setpgid(0, 0) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let code = if error.kind() == std::io::ErrorKind::NotFound {
                127
            } else {
                1
            };
            return journal_failure(&mut log, &exit_path, &spawn_error(error), code);
        }
    };
    let code = await_child(&mut child, &stopped);
    write_atomic(&exit_path, &code.to_string())?;
    Ok(code)
}

fn journal_failure(
    log: &mut File,
    exit_path: &Path,
    error: &str,
    code: i32,
) -> Result<i32, String> {
    writeln!(log, "ufo run: {error}")
        .map_err(|error| format!("could not write the task log: {error}"))?;
    write_atomic(exit_path, &code.to_string())?;
    Ok(code)
}

fn prepared(argv: &[String]) -> Result<Command, String> {
    let mut command = Command::new(&argv[0]);
    command.args(&argv[1..]);
    let ca_file = env::var("SSL_CERT_FILE").ok();
    for name in PROXY_ENV_NAMES {
        if let Ok(value) = env::var(name) {
            command.env(
                name,
                crate::egress::loopback_proxy_url(&value, ca_file.as_deref())?,
            );
        }
    }
    Ok(command)
}

#[cfg(unix)]
fn stop_flag() -> Result<std::sync::Arc<std::sync::atomic::AtomicBool>, String> {
    use std::sync::atomic::AtomicBool;
    let stopped = std::sync::Arc::new(AtomicBool::new(false));
    for signal in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(signal, stopped.clone())
            .map_err(|error| format!("could not install the task signal handler: {error}"))?;
    }
    Ok(stopped)
}

#[cfg(not(unix))]
fn stop_flag() -> Result<std::sync::Arc<std::sync::atomic::AtomicBool>, String> {
    Ok(std::sync::Arc::new(std::sync::atomic::AtomicBool::new(
        false,
    )))
}

fn await_child(child: &mut Child, stopped: &std::sync::atomic::AtomicBool) -> i32 {
    use std::sync::atomic::Ordering;
    let mut stopping = None;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return exit_code(status),
            Ok(None) if stopped.load(Ordering::Relaxed) => {
                if stopping.is_none() {
                    stop_group(child);
                    stopping = Some(std::time::Instant::now());
                } else if stopping.is_some_and(|started| started.elapsed() >= STOP_GRACE) {
                    kill_group(child);
                }
            }
            Ok(None) => {}
            Err(_) => return 1,
        }
        thread::sleep(WAIT_POLL);
    }
}

#[cfg(unix)]
fn stop_group(child: &Child) {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
}

#[cfg(not(unix))]
fn stop_group(_child: &Child) {}

#[cfg(unix)]
fn kill_group(child: &mut Child) {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
}

#[cfg(not(unix))]
fn kill_group(child: &mut Child) {
    let _ = child.kill();
}

fn wait_for_pid(pid_path: &Path, exit_path: &Path) -> Result<u32, String> {
    let started = std::time::Instant::now();
    loop {
        if let Ok(pid) = read_number::<u32>(pid_path) {
            return Ok(pid);
        }
        if exit_path.exists() {
            return Err("the command ended without a task pid".to_string());
        }
        if started.elapsed() >= READY_WAIT {
            return Err("the command did not start".to_string());
        }
        thread::sleep(WAIT_POLL);
    }
}

fn wait_for_exit(pid: u32, exit_path: &Path) -> Result<(), String> {
    loop {
        if exit_path.exists() {
            return Ok(());
        }
        if !process_alive(pid) {
            return Err("the command ended without an exit code".to_string());
        }
        thread::sleep(WAIT_POLL);
    }
}

#[cfg(unix)]
fn process_alive(pid: u32) -> bool {
    if pid > i32::MAX as u32 {
        return false;
    }
    let result = unsafe { libc::kill(pid as i32, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(not(unix))]
fn process_alive(_pid: u32) -> bool {
    true
}

fn read_exit(path: &Path) -> Result<i32, String> {
    read_number(path).map_err(|_| "the command ended without an exit code".to_string())
}

fn read_number<T: std::str::FromStr>(path: &Path) -> Result<T, String> {
    fs::read_to_string(path)
        .map_err(|error| error.to_string())?
        .trim()
        .parse()
        .map_err(|_| format!("{} does not contain a number", path.display()))
}

fn copy_log(base: &Path) -> Result<(), String> {
    let path = suffixed(base, "log");
    let mut file =
        File::open(&path).map_err(|error| format!("could not read {}: {error}", path.display()))?;
    let mut stdout = std::io::stdout().lock();
    std::io::copy(&mut file, &mut stdout)
        .and_then(|_| stdout.flush())
        .map_err(|error| format!("could not write task output: {error}"))
}

fn write_atomic(path: &Path, value: &str) -> Result<(), String> {
    let temporary = suffixed(path, &format!("tmp-{}", std::process::id()));
    fs::write(&temporary, value)
        .and_then(|_| fs::rename(&temporary, path))
        .map_err(|error| format!("could not write {}: {error}", path.display()))
}

fn suffixed(base: &Path, suffix: &str) -> PathBuf {
    let mut value: OsString = base.as_os_str().to_owned();
    value.push(".");
    value.push(suffix);
    PathBuf::from(value)
}

fn sweep(parent: &Path) {
    let now = SystemTime::now();
    let Ok(entries) = fs::read_dir(parent) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("exit") {
            continue;
        }
        let old = entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > TASK_SWEEP_AGE);
        if !old {
            continue;
        }
        let base = path.with_extension("");
        for suffix in ["pid", "log", "exit", "lock"] {
            let _ = fs::remove_file(suffixed(&base, suffix));
        }
    }
}

fn clear_abandoned_lock(lock: &Path, pid: &Path) {
    if pid.exists() {
        return;
    }
    if read_number::<u32>(lock).is_ok_and(process_alive) {
        return;
    }
    let empty_and_young = lock
        .metadata()
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| SystemTime::now().duration_since(modified).ok())
        .is_some_and(|age| age < READY_WAIT)
        && fs::metadata(lock).is_ok_and(|metadata| metadata.len() == 0);
    if empty_and_young {
        return;
    }
    let _ = fs::remove_file(lock);
}

fn spawn_error(error: std::io::Error) -> String {
    if error.kind() == std::io::ErrorKind::NotFound {
        format!("command not found: {error}")
    } else {
        format!("could not start the command: {error}")
    }
}

#[cfg(unix)]
fn exit_code(status: ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

#[cfg(not(unix))]
fn exit_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|part| part.to_string()).collect()
    }

    #[test]
    fn parses_direct_and_task_calls() {
        assert_eq!(
            parse(&words(&["--", "printf", "%s", "ok"])).unwrap(),
            Call {
                mode: Mode::Direct,
                detach: false,
                argv: words(&["printf", "%s", "ok"]),
            }
        );
        assert_eq!(
            parse(&words(&["printf", "%s", "ok"])).unwrap(),
            Call {
                mode: Mode::Direct,
                detach: false,
                argv: words(&["printf", "%s", "ok"]),
            }
        );
        assert_eq!(
            parse(&words(&[
                "--task", "/tmp/run", "--detach", "--", "bash", "-lc", "echo ok"
            ]))
            .unwrap(),
            Call {
                mode: Mode::Task(PathBuf::from("/tmp/run")),
                detach: true,
                argv: words(&["bash", "-lc", "echo ok"]),
            }
        );
    }

    #[test]
    fn rejects_empty_and_detached_direct_calls() {
        assert_eq!(parse(&[]).unwrap_err(), USAGE);
        assert_eq!(
            parse(&words(&["--detach", "--", "true"])).unwrap_err(),
            USAGE
        );
    }

    #[test]
    fn suffixes_a_base_without_replacing_its_extension() {
        assert_eq!(
            suffixed(Path::new("/tmp/task.a"), "pid"),
            Path::new("/tmp/task.a.pid")
        );
    }
}
