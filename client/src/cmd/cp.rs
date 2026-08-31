//! The `ufo cp` verb: copy files between this machine and a conversation's workspace.
//!
//! scp's grammar — `ufo cp report.pdf build:in/report.pdf` uploads, `ufo cp build:out/patch.diff .`
//! downloads, and a directory on either side syncs the tree. The remote side rides the terminal
//! surface's file routes under the member's own bearer: the channel resolves through the same
//! member-scoped queue key every post uses, an upload get-or-creates the conversation so staging
//! precedes the first turn, and a sync skips a file whose destination already has its size and a
//! modified time at least as new — rsync's quick check, since a workspace write cannot preserve
//! source times. Nothing else is policy: every file in a tree walks, and the transfers are
//! silent but for the closing count.

use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Deserialize;

use crate::config::Home;

pub const USAGE: &str = "usage: ufo cp SRC DST  (one side is CHANNEL:PATH)";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(300);
const COPY_BUFFER_BYTES: usize = 64 * 1024;

#[derive(Debug, PartialEq, Clone)]
pub enum Endpoint {
    Local(String),
    Remote { channel: String, path: String },
}

#[derive(Debug, PartialEq)]
pub struct Call {
    pub src: Endpoint,
    pub dst: Endpoint,
}

#[derive(Debug, Deserialize)]
struct Listing {
    files: Vec<Entry>,
}

#[derive(Debug, Clone, Deserialize)]
struct Entry {
    path: String,
    size_bytes: u64,
    modified_at: f64,
}

pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(usage) => {
            eprintln!("{usage}");
            return 2;
        }
    };
    match run(&call) {
        Ok(report) => {
            println!("{report}");
            0
        }
        Err(error) => {
            eprintln!("ufo cp: {error}");
            1
        }
    }
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    match args {
        [src, dst] => {
            let src = endpoint(src);
            let dst = endpoint(dst);
            match (&src, &dst) {
                (Endpoint::Remote { .. }, Endpoint::Local(_))
                | (Endpoint::Local(_), Endpoint::Remote { .. }) => Ok(Call { src, dst }),
                _ => Err(USAGE.to_string()),
            }
        }
        _ => Err(USAGE.to_string()),
    }
}

/// scp's reading of one argument: `channel:path` is remote when the part before the first colon
/// is non-empty and holds no slash, so `./notes:draft.md` and plain paths stay local.
pub fn endpoint(word: &str) -> Endpoint {
    if let Some((channel, path)) = word.split_once(':') {
        if !channel.is_empty() && !channel.contains('/') {
            return Endpoint::Remote {
                channel: channel.to_string(),
                path: path.trim_start_matches('/').to_string(),
            };
        }
    }
    Endpoint::Local(word.to_string())
}

/// Whether the copy may skip a file: the destination already holds its size and a modified time
/// at least as new. A workspace write stamps its own time, so equality of times never happens
/// across a real transfer — newer-or-equal at the destination reads "already carried".
pub fn carried(src_size: u64, src_modified: f64, dst: Option<(u64, f64)>) -> bool {
    match dst {
        Some((size, modified)) => size == src_size && modified >= src_modified,
        None => false,
    }
}

struct Wire {
    agent: ureq::Agent,
    workspace: String,
    token: String,
}

impl Wire {
    fn resolve() -> Result<Wire, String> {
        let home = Home::resolve();
        let workspace = env::var("WORKSPACE_URL")
            .ok()
            .filter(|url| !url.trim().is_empty())
            .or_else(|| home.workspace())
            .ok_or("no workspace: run `ufo` once to sign in")?;
        let token = home
            .credentials()
            .ok_or("no credentials: run `ufo` once to sign in")?;
        Ok(Wire {
            agent: ureq::AgentBuilder::new()
                .timeout_connect(CONNECT_TIMEOUT)
                .timeout_read(READ_TIMEOUT)
                .build(),
            workspace: workspace.trim_end_matches('/').to_string(),
            token,
        })
    }

    fn url(&self, channel: &str, route: &str) -> String {
        format!("{}/surface/ufo/{}/{}", self.workspace, channel, route)
    }

    fn listing(&self, channel: &str) -> Result<Vec<Entry>, String> {
        let response = self
            .agent
            .get(&self.url(channel, "files"))
            .set("authorization", &format!("Bearer {}", self.token))
            .call()
            .map_err(described)?;
        let listing: Listing = serde_json::from_reader(response.into_reader())
            .map_err(|error| format!("listing: {error}"))?;
        Ok(listing.files)
    }

    fn download(&self, channel: &str, path: &str, target: &Path) -> Result<(), String> {
        let response = self
            .agent
            .get(&self.url(channel, &format!("file/{path}")))
            .set("authorization", &format!("Bearer {}", self.token))
            .call()
            .map_err(described)?;
        if let Some(parent) = target
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).map_err(|error| format!("{}: {error}", parent.display()))?;
        }
        let mut reader = response.into_reader();
        let mut file =
            File::create(target).map_err(|error| format!("{}: {error}", target.display()))?;
        copy(&mut reader, &mut file)
    }

    fn upload(&self, source: &Path, channel: &str, path: &str) -> Result<(), String> {
        let body = fs::read(source).map_err(|error| format!("{}: {error}", source.display()))?;
        self.agent
            .put(&self.url(channel, &format!("file/{path}")))
            .set("authorization", &format!("Bearer {}", self.token))
            .send_bytes(&body)
            .map_err(described)?;
        Ok(())
    }
}

fn described(error: ureq::Error) -> String {
    match error {
        ureq::Error::Status(401, _) => "unauthorized: run `ufo` once to sign in again".to_string(),
        ureq::Error::Status(404, _) => "no such file".to_string(),
        ureq::Error::Status(code, response) => {
            let detail = response.into_string().unwrap_or_default();
            format!("{code}: {}", detail.trim())
        }
        other => other.to_string(),
    }
}

fn run(call: &Call) -> Result<String, String> {
    let wire = Wire::resolve()?;
    match (&call.src, &call.dst) {
        (Endpoint::Remote { channel, path }, Endpoint::Local(local)) => {
            pull(&wire, channel, path, Path::new(local))
        }
        (Endpoint::Local(local), Endpoint::Remote { channel, path }) => {
            push(&wire, Path::new(local), channel, path)
        }
        _ => Err(USAGE.to_string()),
    }
}

fn pull(wire: &Wire, channel: &str, path: &str, local: &Path) -> Result<String, String> {
    let entries = wire.listing(channel)?;
    let prefix = if path.is_empty() {
        String::new()
    } else {
        format!("{path}/")
    };
    let tree: Vec<&Entry> = entries
        .iter()
        .filter(|entry| path.is_empty() || entry.path.starts_with(&prefix))
        .collect();
    if entries.iter().any(|entry| entry.path == path) {
        let target = if local.is_dir() {
            let basename = path.rsplit('/').next().unwrap_or(path);
            local.join(basename)
        } else {
            local.to_path_buf()
        };
        wire.download(channel, path, &target)?;
        return Ok(target.display().to_string());
    }
    if tree.is_empty() {
        return Err(format!("no such file: {path} in {channel}"));
    }
    let mut copied = 0usize;
    let mut skipped = 0usize;
    for entry in tree {
        let rel = &entry.path[prefix.len()..];
        let target = local.join(rel);
        if carried(entry.size_bytes, entry.modified_at, stat(&target)) {
            skipped += 1;
            continue;
        }
        wire.download(channel, &entry.path, &target)?;
        copied += 1;
    }
    Ok(format!("{copied} copied, {skipped} unchanged"))
}

/// scp's remote target rule for one file: a trailing slash (or a bare channel) means "into that
/// directory", keeping the source's name; anything else is the destination name itself.
pub fn remote_single_target(path: &str, basename: &str) -> String {
    if path.is_empty() || path.ends_with('/') {
        format!("{path}{basename}")
    } else {
        path.to_string()
    }
}

fn push(wire: &Wire, local: &Path, channel: &str, path: &str) -> Result<String, String> {
    let metadata = fs::metadata(local).map_err(|error| format!("{}: {error}", local.display()))?;
    if metadata.is_file() {
        let remote = remote_single_target(path, &file_name(local)?);
        wire.upload(local, channel, &remote)?;
        return Ok(format!("{channel}:{remote}"));
    }
    let remote_files: std::collections::HashMap<String, (u64, f64)> = wire
        .listing(channel)?
        .into_iter()
        .map(|entry| (entry.path, (entry.size_bytes, entry.modified_at)))
        .collect();
    let mut copied = 0usize;
    let mut skipped = 0usize;
    for file in walked(local)? {
        let rel = file
            .strip_prefix(local)
            .map_err(|error| error.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        let remote = if path.is_empty() {
            rel.clone()
        } else {
            format!("{}/{rel}", path.trim_end_matches('/'))
        };
        let stat = fs::metadata(&file).map_err(|error| format!("{}: {error}", file.display()))?;
        let modified = epoch(stat.modified().ok());
        if carried(stat.len(), modified, remote_files.get(&remote).copied()) {
            skipped += 1;
            continue;
        }
        wire.upload(&file, channel, &remote)?;
        copied += 1;
    }
    Ok(format!("{copied} copied, {skipped} unchanged"))
}

fn file_name(path: &Path) -> Result<String, String> {
    path.file_name()
        .map(|name| name.to_string_lossy().to_string())
        .ok_or_else(|| format!("{}: not a file", path.display()))
}

fn walked(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let listed = fs::read_dir(&dir).map_err(|error| format!("{}: {error}", dir.display()))?;
        for item in listed {
            let item = item.map_err(|error| error.to_string())?;
            let path = item.path();
            let kind = item.file_type().map_err(|error| error.to_string())?;
            if kind.is_dir() {
                stack.push(path);
            } else if kind.is_file() {
                files.push(path);
            }
        }
    }
    files.sort();
    Ok(files)
}

fn stat(path: &Path) -> Option<(u64, f64)> {
    let metadata = fs::metadata(path).ok()?;
    metadata
        .is_file()
        .then(|| (metadata.len(), epoch(metadata.modified().ok())))
}

fn epoch(modified: Option<SystemTime>) -> f64 {
    modified
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|since| since.as_secs_f64())
        .unwrap_or(0.0)
}

fn copy(reader: &mut dyn Read, writer: &mut dyn Write) -> Result<(), String> {
    let mut buffer = [0u8; COPY_BUFFER_BYTES];
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| error.to_string())?;
        if read == 0 {
            writer.flush().map_err(|error| error.to_string())?;
            return Ok(());
        }
        writer
            .write_all(&buffer[..read])
            .map_err(|error| error.to_string())?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    #[test]
    fn one_side_must_be_remote() {
        assert!(parse(&args(&["a.txt", "b.txt"])).is_err());
        assert!(parse(&args(&["build:a", "build:b"])).is_err());
        assert!(parse(&args(&["a.txt"])).is_err());
        assert!(parse(&args(&[])).is_err());
        assert!(parse(&args(&["build:out/patch.diff", "."])).is_ok());
        assert!(parse(&args(&["patch.diff", "build:in/"])).is_ok());
    }

    #[test]
    fn a_colon_after_a_slash_stays_local() {
        assert_eq!(
            endpoint("./notes:draft.md"),
            Endpoint::Local("./notes:draft.md".to_string())
        );
        assert_eq!(
            endpoint("build:in/report.pdf"),
            Endpoint::Remote {
                channel: "build".to_string(),
                path: "in/report.pdf".to_string(),
            }
        );
        assert_eq!(
            endpoint("build:"),
            Endpoint::Remote {
                channel: "build".to_string(),
                path: String::new(),
            }
        );
    }

    #[test]
    fn the_quick_check_skips_only_a_newer_same_size_destination() {
        assert!(carried(11, 100.0, Some((11, 100.0))));
        assert!(carried(11, 100.0, Some((11, 250.0))));
        assert!(!carried(11, 100.0, Some((11, 50.0))));
        assert!(!carried(11, 100.0, Some((12, 250.0))));
        assert!(!carried(11, 100.0, None));
    }

    #[test]
    fn a_remote_trailing_slash_means_into_that_directory() {
        assert_eq!(remote_single_target("in/", "report.pdf"), "in/report.pdf");
        assert_eq!(remote_single_target("", "report.pdf"), "report.pdf");
        assert_eq!(
            remote_single_target("in/renamed.pdf", "report.pdf"),
            "in/renamed.pdf"
        );
    }
}
