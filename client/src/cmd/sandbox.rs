//! `ufo sandbox`: run a command with its writes confined by the kernel to the directories named.
//! Seatbelt on macOS (`/usr/bin/sandbox-exec`, never one from PATH, since a PATH entry is writable
//! by what it confines), Landlock on Linux. Reads, exec, and the network stay open, and a child
//! inherits the confinement, so a shell and everything it starts are held the same way.

use std::path::PathBuf;

pub const USAGE: &str = "usage: ufo sandbox [--write DIR]... -- COMMAND [ARG...]";
pub const WRITE_FLAG: &str = "--write";
pub const WRITE_PARAM: &str = "WRITE";
pub const SEATBELT_EXECUTABLE: &str = "/usr/bin/sandbox-exec";
/// The rules every command gets before its writable roots: closed by default, the whole filesystem
/// readable, `/dev/null` and pseudo-terminals writable, the Mach services TLS and directory lookups
/// go through, and the network open. Signals and process info are open too: every `sandbox-exec`
/// is its own sandbox, and a stop line from one command has to reach the task an earlier one
/// detached. Adapted from Chromium's common and network sandbox policies.
pub const SEATBELT_BASE_POLICY: &str = r#"(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal)
(allow process-info*)
(allow file-read*)
(allow file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))
(allow sysctl-read)
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.opendirectoryd.membership")
  (global-name "com.apple.bsd.dirhelper")
  (global-name "com.apple.PowerManagement.control")
  (global-name "com.apple.SecurityServer")
  (global-name "com.apple.networkd")
  (global-name "com.apple.ocspd")
  (global-name "com.apple.trustd.agent")
  (global-name "com.apple.SystemConfiguration.DNSConfiguration")
  (global-name "com.apple.SystemConfiguration.configd")
  (global-name "com.apple.cfprefsd.agent")
  (global-name "com.apple.cfprefsd.daemon")
  (global-name "com.apple.logd"))
(allow ipc-posix-sem)
(allow ipc-posix-shm*)
(allow pseudo-tty)
(allow file-read* file-write* file-ioctl (literal "/dev/ptmx"))
(allow file-read* file-write* file-ioctl (regex #"^/dev/ttys[0-9]+"))
(allow system-socket (require-all (socket-domain AF_SYSTEM) (socket-protocol 2)))
(allow network-outbound)
(allow network-inbound)
(allow network-bind)
"#;
/// Paths every Linux command may write besides its roots: the sink, and the terminal devices a
/// shell and its children hold open.
pub const LANDLOCK_DEVICE_WRITES: [&str; 4] = ["/dev/null", "/dev/tty", "/dev/pts", "/dev/shm"];

#[derive(Debug, PartialEq)]
pub struct Call {
    pub writes: Vec<PathBuf>,
    pub argv: Vec<String>,
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    let mut writes = Vec::new();
    let mut rest = args;
    loop {
        match rest.first().map(String::as_str) {
            Some(flag) if flag == WRITE_FLAG => {
                let Some(dir) = rest.get(1) else {
                    return Err(format!("{WRITE_FLAG} takes a directory\n{USAGE}"));
                };
                writes.push(PathBuf::from(dir));
                rest = &rest[2..];
            }
            Some("--") => {
                rest = &rest[1..];
                break;
            }
            _ => return Err(USAGE.to_string()),
        }
    }
    if rest.is_empty() {
        return Err(USAGE.to_string());
    }
    Ok(Call {
        writes,
        argv: rest.to_vec(),
    })
}

/// The Seatbelt profile for `count` writable roots, each named through a `-D` parameter so no path
/// is ever spliced into the policy text. A root directory itself cannot be unlinked or renamed: it
/// is the boundary every later command is confined to, and a confined command must not move it.
pub fn seatbelt_profile(count: usize) -> String {
    let mut profile = String::from(SEATBELT_BASE_POLICY);
    if count == 0 {
        return profile;
    }
    let subpaths: Vec<String> = (0..count)
        .map(|index| format!("(subpath (param \"{WRITE_PARAM}_{index}\"))"))
        .collect();
    profile.push_str(&format!("(allow file-write* {})\n", subpaths.join(" ")));
    for index in 0..count {
        profile.push_str(&format!(
            "(deny file-write-unlink (require-all (literal (param \"{WRITE_PARAM}_{index}\")) (vnode-type DIRECTORY)))\n"
        ));
    }
    profile
}

/// The `sandbox-exec` argv for `call`. Seatbelt matches the path a vnode really has, so every root
/// is canonicalized (`/tmp` is `/private/tmp`); a root that does not exist yet is named as given.
pub fn seatbelt_argv(call: &Call) -> Vec<String> {
    let mut argv = vec![
        SEATBELT_EXECUTABLE.to_string(),
        "-p".to_string(),
        seatbelt_profile(call.writes.len()),
    ];
    for (index, root) in call.writes.iter().enumerate() {
        let canonical = std::fs::canonicalize(root).unwrap_or_else(|_| root.clone());
        argv.push("-D".to_string());
        argv.push(format!("{WRITE_PARAM}_{index}={}", canonical.display()));
    }
    argv.push("--".to_string());
    argv.extend(call.argv.iter().cloned());
    argv
}

pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(message) => {
            eprintln!("{message}");
            return 2;
        }
    };
    eprintln!("ufo sandbox: {}", confine_and_exec(&call));
    1
}

#[cfg(target_os = "macos")]
fn confine_and_exec(call: &Call) -> String {
    use std::os::unix::process::CommandExt;
    let argv = seatbelt_argv(call);
    let error = std::process::Command::new(&argv[0]).args(&argv[1..]).exec();
    format!("{SEATBELT_EXECUTABLE}: {error}")
}

#[cfg(target_os = "linux")]
fn confine_and_exec(call: &Call) -> String {
    use landlock::{
        path_beneath_rules, Access, AccessFs, CompatLevel, Compatible, Ruleset, RulesetAttr,
        RulesetCreatedAttr, RulesetStatus, ABI,
    };
    use std::os::unix::process::CommandExt;
    let abi = ABI::V5;
    let read_write = AccessFs::from_all(abi);
    let read_only = AccessFs::from_read(abi);
    let restricted = Ruleset::default()
        .set_compatibility(CompatLevel::BestEffort)
        .handle_access(read_write)
        .and_then(|ruleset| ruleset.create())
        .and_then(|ruleset| ruleset.add_rules(path_beneath_rules(["/"], read_only)))
        .and_then(|ruleset| {
            ruleset.add_rules(path_beneath_rules(LANDLOCK_DEVICE_WRITES, read_write))
        })
        .and_then(|ruleset| ruleset.add_rules(path_beneath_rules(&call.writes, read_write)))
        .and_then(|ruleset| ruleset.no_new_privs(true).restrict_self());
    match restricted {
        Err(error) => return format!("landlock: {error}"),
        Ok(status) if status.ruleset == RulesetStatus::NotEnforced => {
            return "landlock is not enforced by this kernel".to_string();
        }
        Ok(_) => {}
    }
    let error = std::process::Command::new(&call.argv[0])
        .args(&call.argv[1..])
        .exec();
    format!("{}: {error}", call.argv[0])
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn confine_and_exec(_call: &Call) -> String {
    "the kernel sandbox needs macOS or Linux".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn parses_writes_then_the_command() {
        let call = parse(&strings(&[
            "--write", "/a", "--write", "/b", "--", "sh", "-c", "x",
        ]))
        .unwrap();
        assert_eq!(
            call,
            Call {
                writes: vec![PathBuf::from("/a"), PathBuf::from("/b")],
                argv: strings(&["sh", "-c", "x"]),
            }
        );
    }

    #[test]
    fn refuses_a_missing_command_or_a_bare_write_flag() {
        assert_eq!(
            parse(&strings(&["--write", "/a", "--"])),
            Err(USAGE.to_string())
        );
        assert!(parse(&strings(&["--write"]))
            .unwrap_err()
            .starts_with(WRITE_FLAG));
        assert_eq!(parse(&strings(&["sh"])), Err(USAGE.to_string()));
    }

    #[test]
    fn the_profile_names_each_root_as_a_parameter_and_pins_the_root_itself() {
        let profile = seatbelt_profile(2);
        assert!(profile.starts_with(SEATBELT_BASE_POLICY));
        assert!(profile.contains(
            "(allow file-write* (subpath (param \"WRITE_0\")) (subpath (param \"WRITE_1\")))"
        ));
        assert!(profile.contains(
            "(deny file-write-unlink (require-all (literal (param \"WRITE_1\")) (vnode-type DIRECTORY)))"
        ));
        assert_eq!(seatbelt_profile(0), SEATBELT_BASE_POLICY);
    }

    #[test]
    fn the_argv_binds_each_root_by_parameter_and_ends_with_the_command() {
        let call = parse(&strings(&["--write", "/", "--", "sh", "-c", "x"])).unwrap();
        let argv = seatbelt_argv(&call);
        assert_eq!(argv[0], SEATBELT_EXECUTABLE);
        assert_eq!(argv[1], "-p");
        assert_eq!(argv[2], seatbelt_profile(1));
        assert_eq!(
            &argv[3..],
            &strings(&["-D", "WRITE_0=/", "--", "sh", "-c", "x"])[..]
        );
    }
}
