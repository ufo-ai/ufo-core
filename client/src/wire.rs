//! The directive wire: tab-separated lines over held HTTP POST streams.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::time::Duration;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(120);
const AGENT_IDLE_REFRESH: Duration = Duration::from_secs(4);
const FALLBACK_TIMEOUT_SECONDS: u64 = 600;

/// One `run` directive: an op the server asks this terminal to execute.
#[derive(Debug, Clone, PartialEq)]
pub struct OpRequest {
    pub op_id: String,
    pub kind: String,
    pub name: String,
    pub timeout_s: u64,
    pub arg: String,
    pub params: String,
}

/// One parsed directive line. Unknown verbs parse to `Unknown` and are dropped by the renderer.
#[derive(Debug, Clone, PartialEq)]
pub enum Directive {
    Say(String),
    Note(String),
    Txt(String),
    Status(String),
    Ufo {
        width: usize,
        frame: String,
    },
    Ask(String),
    Choose {
        prompt: String,
        options: Vec<String>,
    },
    Secret {
        sealed: String,
        slot: String,
        prompt: String,
    },
    Poll(f64),
    Since(String),
    Run(OpRequest),
    Token(String),
    Workspace(String),
    Install,
    Logout,
    File {
        name: String,
        size: String,
        url: String,
    },
    Exit(i32),
    Unknown,
}

/// Reverse the surface's escaping: `\\`→`\`, `\t`→tab, `\n`→newline, single pass.
pub fn unescape(field: &str) -> String {
    let mut out = String::with_capacity(field.len());
    let mut chars = field.chars();
    while let Some(character) = chars.next() {
        if character != '\\' {
            out.push(character);
            continue;
        }
        match chars.next() {
            Some('\\') => out.push('\\'),
            Some('t') => out.push('\t'),
            Some('n') => out.push('\n'),
            Some(other) => {
                out.push('\\');
                out.push(other);
            }
            None => out.push('\\'),
        }
    }
    out
}

fn field(fields: &[String], index: usize) -> String {
    fields.get(index).cloned().unwrap_or_default()
}

/// Parse one raw line into a directive: split on tabs, unescape each field after the verb.
pub fn parse_line(line: &str) -> Directive {
    let mut parts = line.split('\t');
    let verb = parts.next().unwrap_or("");
    let fields: Vec<String> = parts.map(unescape).collect();
    match verb {
        "say" => Directive::Say(field(&fields, 0)),
        "note" => Directive::Note(field(&fields, 0)),
        "txt" => Directive::Txt(field(&fields, 0)),
        "status" => Directive::Status(field(&fields, 0)),
        "ufo" => {
            let packed = field(&fields, 0);
            let (width, frame) = packed.split_once(' ').unwrap_or((packed.as_str(), ""));
            match width.parse() {
                Ok(width) => Directive::Ufo {
                    width,
                    frame: frame.to_string(),
                },
                Err(_) => Directive::Unknown,
            }
        }
        "ask" => Directive::Ask(field(&fields, 0)),
        "choose" if !fields.is_empty() => Directive::Choose {
            prompt: fields[0].clone(),
            options: fields[1..]
                .iter()
                .filter(|o| !o.is_empty())
                .cloned()
                .collect(),
        },
        "secret" if fields.len() >= 3 => Directive::Secret {
            sealed: fields[0].clone(),
            slot: fields[1].clone(),
            prompt: fields[2].clone(),
        },
        "poll" => Directive::Poll(field(&fields, 0).parse().unwrap_or(1.0)),
        "since" if fields.len() >= 2 => Directive::Since(format!("{}:{}", fields[0], fields[1])),
        "run" => Directive::Run(OpRequest {
            op_id: field(&fields, 0),
            kind: field(&fields, 1),
            name: field(&fields, 2),
            timeout_s: field(&fields, 3)
                .parse()
                .unwrap_or(FALLBACK_TIMEOUT_SECONDS),
            arg: field(&fields, 4),
            params: field(&fields, 5),
        }),
        "token" => Directive::Token(field(&fields, 0)),
        "workspace" => Directive::Workspace(field(&fields, 0)),
        "install" => Directive::Install,
        "logout" => Directive::Logout,
        "file" if fields.len() >= 2 => Directive::File {
            name: fields[0].clone(),
            size: fields[1].clone(),
            url: field(&fields, 2),
        },
        "exit" => Directive::Exit(field(&fields, 0).parse().unwrap_or(0)),
        _ => Directive::Unknown,
    }
}

/// The body one request carries: a member message, an empty resume, or an op's reply.
#[derive(Clone)]
pub enum PostBody {
    Message(String),
    Empty,
    OpReply {
        op_id: String,
        reply: Result<Vec<u8>, String>,
    },
}

/// One member session on the wire: endpoint state, persistent connections, and the since cursor.
pub struct Session {
    pub gateway_url: String,
    pub workspace_url: Option<String>,
    pub channel: String,
    pub token: Option<String>,
    pub session_id: String,
    pub cwd: Option<String>,
    pub since: Option<String>,
    pub installed: bool,
    pub tty: bool,
    agent: ureq::Agent,
    last_post: std::time::Instant,
}

fn build_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(CONNECT_TIMEOUT)
        .timeout_read(READ_TIMEOUT)
        .build()
}

impl Session {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        gateway_url: String,
        workspace_url: Option<String>,
        channel: String,
        token: Option<String>,
        session_id: String,
        cwd: Option<String>,
        installed: bool,
        tty: bool,
    ) -> Session {
        Session {
            gateway_url,
            workspace_url,
            channel,
            token,
            session_id,
            cwd,
            since: None,
            installed,
            tty,
            agent: build_agent(),
            last_post: std::time::Instant::now(),
        }
    }

    /// The endpoint this session posts to: the workspace surface when signed in, onboarding
    /// otherwise.
    pub fn endpoint(&self) -> String {
        match &self.workspace_url {
            Some(workspace) => {
                format!(
                    "{}/surface/ufo/{}",
                    workspace.trim_end_matches('/'),
                    self.channel
                )
            }
            None => format!(
                "{}/v1/onboard/{}",
                self.gateway_url.trim_end_matches('/'),
                self.channel
            ),
        }
    }

    /// The base URL the member is talking to, for error reporting.
    pub fn base(&self) -> &str {
        self.workspace_url.as_deref().unwrap_or(&self.gateway_url)
    }

    /// POST one request and stream its directives as they arrive.
    pub fn post(&mut self, body: PostBody) -> Result<DirectiveStream, String> {
        if self.last_post.elapsed() > AGENT_IDLE_REFRESH {
            self.agent = build_agent();
        }
        self.last_post = std::time::Instant::now();
        let mut request = self.request("POST", &self.endpoint());
        let outcome = match body {
            PostBody::Message(text) => request.send_string(&text),
            PostBody::Empty => request.send_string(""),
            PostBody::OpReply { op_id, reply } => {
                request = request.set("x-ufo-op", &header_safe(&op_id));
                match reply {
                    Ok(bytes) => request.send_bytes(&bytes),
                    Err(failure) => request
                        .set("x-ufo-op-err", &header_safe(&failure))
                        .send_bytes(&[]),
                }
            }
        };
        let response = opened(outcome)?;
        Ok(DirectiveStream {
            reader: Box::new(BufReader::new(response.into_reader())),
            failed: false,
        })
    }

    /// POST one privately entered secret out of band; the response body is directive lines whose
    /// `say` fields are returned for the caller to render.
    pub fn post_secret(
        &self,
        sealed: &str,
        slot: &str,
        value: &str,
    ) -> Result<Vec<String>, String> {
        let request = self
            .request("POST", &self.endpoint())
            .set("x-ufo-secret", &header_safe(sealed))
            .set("x-ufo-slot", &header_safe(slot));
        let text = match request.send_string(value) {
            Ok(response) => response
                .into_string()
                .map_err(|error| format!("lost connection ({error})"))?,
            Err(ureq::Error::Status(_, response)) => response.into_string().unwrap_or_default(),
            Err(error) => return Err(format!("lost connection ({error})")),
        };
        Ok(text
            .lines()
            .filter_map(|line| match parse_line(line) {
                Directive::Say(said) => Some(said),
                _ => None,
            })
            .collect())
    }

    /// GET the staged bytes of an in-flight write op into `dest`.
    pub fn fetch_staged(&self, op_id: &str, dest: &Path) -> Result<(), String> {
        let workspace = self
            .workspace_url
            .as_deref()
            .ok_or("no workspace to fetch from")?;
        let url = format!(
            "{}/surface/ufo/{}/op/{}",
            workspace.trim_end_matches('/'),
            self.channel,
            op_id
        );
        let mut request = self.agent.get(&url);
        if let Some(token) = &self.token {
            request = request.set("authorization", &format!("Bearer {token}"));
        }
        let response = opened(request.call())?;
        let mut reader = response.into_reader();
        let mut file = File::create(dest)
            .map_err(|error| format!("could not stage {}: {error}", dest.display()))?;
        std::io::copy(&mut reader, &mut file)
            .map_err(|error| format!("could not stage {}: {error}", dest.display()))?;
        Ok(())
    }

    fn request(&self, method: &str, url: &str) -> ureq::Request {
        let mut request = self
            .agent
            .request(method, url)
            .set("content-type", "text/plain")
            .set("x-ufo-session", &self.session_id)
            .set("x-ufo-tty", if self.tty { "1" } else { "0" })
            .set("x-ufo-installed", if self.installed { "1" } else { "0" })
            .set("x-ufo-script", env!("CARGO_PKG_VERSION"));
        if let Some(token) = &self.token {
            request = request.set("authorization", &format!("Bearer {token}"));
        }
        if self.workspace_url.is_some() {
            if let Some(cwd) = &self.cwd {
                request = request.set("x-ufo-cwd", cwd);
            }
        }
        if let Some(since) = &self.since {
            request = request.set("x-ufo-since", since);
        }
        request
    }
}

fn opened(outcome: Result<ureq::Response, ureq::Error>) -> Result<ureq::Response, String> {
    match outcome {
        Ok(response) => Ok(response),
        Err(ureq::Error::Status(code, response)) => {
            let body = response.into_string().unwrap_or_default();
            Err(format!("chat failed ({code}): {}", body.trim()))
        }
        Err(error) => Err(format!("lost connection ({error})")),
    }
}

fn header_safe(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect()
}

/// Directives read line by line off one held response stream.
pub struct DirectiveStream {
    reader: Box<dyn BufRead>,
    failed: bool,
}

impl Iterator for DirectiveStream {
    type Item = Result<Directive, String>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.failed {
            return None;
        }
        loop {
            let mut line = String::new();
            match self.reader.read_line(&mut line) {
                Ok(0) => return None,
                Ok(_) => {
                    let trimmed = line.trim_end_matches(['\n', '\r']);
                    if trimmed.is_empty() {
                        continue;
                    }
                    return Some(Ok(parse_line(trimmed)));
                }
                Err(error) => {
                    self.failed = true;
                    return Some(Err(format!("lost connection ({error})")));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn escape(value: &str) -> String {
        value
            .replace('\\', "\\\\")
            .replace('\t', "\\t")
            .replace('\r', "")
            .replace('\n', "\\n")
    }

    #[test]
    fn unescape_reverses_surface_escaping() {
        for original in [
            "plain",
            "a\tb",
            "line1\nline2",
            "back\\slash",
            "mix\\t\t\n\\",
        ] {
            let expected: String = original.chars().filter(|&c| c != '\r').collect();
            assert_eq!(unescape(&escape(original)), expected);
        }
    }

    #[test]
    fn unescape_keeps_unknown_escapes() {
        assert_eq!(unescape("a\\qb"), "a\\qb");
        assert_eq!(unescape("tail\\"), "tail\\");
    }

    #[test]
    fn parses_text_verbs() {
        assert_eq!(
            parse_line("say\thello\\nworld"),
            Directive::Say("hello\nworld".into())
        );
        assert_eq!(parse_line("note\tdim"), Directive::Note("dim".into()));
        assert_eq!(parse_line("txt\tchunk"), Directive::Txt("chunk".into()));
        assert_eq!(
            parse_line("status\t12 tok"),
            Directive::Status("12 tok".into())
        );
        assert_eq!(parse_line("say"), Directive::Say(String::new()));
    }

    #[test]
    fn parses_run() {
        let line = "run\tabc123\texec\texec\t120\t\t{\"argv\":[\"ls\"]}";
        assert_eq!(
            parse_line(line),
            Directive::Run(OpRequest {
                op_id: "abc123".into(),
                kind: "exec".into(),
                name: "exec".into(),
                timeout_s: 120,
                arg: String::new(),
                params: "{\"argv\":[\"ls\"]}".into(),
            })
        );
    }

    #[test]
    fn parses_choose_and_ask() {
        assert_eq!(
            parse_line("choose\tPick:\ta\tb"),
            Directive::Choose {
                prompt: "Pick:".into(),
                options: vec!["a".into(), "b".into()]
            }
        );
        assert_eq!(
            parse_line("choose\tFree text?"),
            Directive::Choose {
                prompt: "Free text?".into(),
                options: vec![]
            }
        );
        assert_eq!(parse_line("ask\t>"), Directive::Ask(">".into()));
    }

    #[test]
    fn parses_session_verbs() {
        assert_eq!(
            parse_line("since\tturn-1\tcursor-9"),
            Directive::Since("turn-1:cursor-9".into())
        );
        assert_eq!(parse_line("poll\t1"), Directive::Poll(1.0));
        assert_eq!(parse_line("token\ttok"), Directive::Token("tok".into()));
        assert_eq!(
            parse_line("workspace\thttps://w"),
            Directive::Workspace("https://w".into())
        );
        assert_eq!(parse_line("install"), Directive::Install);
        assert_eq!(parse_line("logout"), Directive::Logout);
        assert_eq!(parse_line("exit\t2"), Directive::Exit(2));
        assert_eq!(parse_line("exit"), Directive::Exit(0));
    }

    #[test]
    fn parses_file_with_and_without_link() {
        assert_eq!(
            parse_line("file\treport.pdf\t123\thttps://x"),
            Directive::File {
                name: "report.pdf".into(),
                size: "123".into(),
                url: "https://x".into()
            }
        );
        assert_eq!(
            parse_line("file\treport.pdf\t123"),
            Directive::File {
                name: "report.pdf".into(),
                size: "123".into(),
                url: String::new()
            }
        );
    }

    #[test]
    fn parses_craft() {
        assert_eq!(
            parse_line("ufo\t15 line1\\nline2"),
            Directive::Ufo {
                width: 15,
                frame: "line1\nline2".into()
            }
        );
    }

    #[test]
    fn drops_unknown_verbs() {
        assert_eq!(parse_line("debugger\thttps://d"), Directive::Unknown);
        assert_eq!(parse_line("auth\t/auth/start"), Directive::Unknown);
        assert_eq!(parse_line("mystery"), Directive::Unknown);
    }

    #[test]
    fn endpoint_switches_on_workspace() {
        let mut session = Session::new(
            "https://gw".into(),
            None,
            "onboard".into(),
            None,
            "sid".into(),
            None,
            false,
            false,
        );
        assert_eq!(session.endpoint(), "https://gw/v1/onboard/onboard");
        session.workspace_url = Some("https://ws/".into());
        session.channel = "abc".into();
        assert_eq!(session.endpoint(), "https://ws/surface/ufo/abc");
    }
}
