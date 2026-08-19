//! The directive wire: tab-separated lines over held HTTP POST streams.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::sync::OnceLock;
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
    You(String),
    Sent {
        turn_id: String,
        opened: bool,
        arrival_id: String,
    },
    Absorbed(Vec<String>),
    Note(String),
    Txt(String),
    Status(String),
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
        "you" => Directive::You(field(&fields, 0)),
        "sent" if fields.len() >= 2 => Directive::Sent {
            turn_id: fields[0].clone(),
            opened: fields[1] == "1",
            arrival_id: field(&fields, 2),
        },
        "absorbed" => Directive::Absorbed(fields.into_iter().filter(|id| !id.is_empty()).collect()),
        "note" => Directive::Note(field(&fields, 0)),
        "txt" => Directive::Txt(field(&fields, 0)),
        "status" => Directive::Status(field(&fields, 0)),
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

/// The system's IANA timezone, read once: every posted message carries it so the agent reads the
/// member's local time. A system whose zone cannot be named sends no header.
fn system_timezone() -> Option<&'static str> {
    static ZONE: OnceLock<Option<String>> = OnceLock::new();
    ZONE.get_or_init(|| iana_time_zone::get_timezone().ok())
        .as_deref()
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

    /// The stop this session would post, prepared while the member is still watching the stream it
    /// ends — None on an onboarding stream, which holds no turn to stop. Nothing here touches the
    /// held stream's agent: that one is mid-response, and the stop travels on its own connection.
    pub fn stop(&self) -> Option<Stop> {
        self.workspace_url.as_ref()?;
        Some(Stop(
            self.dressed(build_agent().request("POST", &self.endpoint()))
                .set("x-ufo-stop", "1"),
        ))
    }

    /// The lane for send-only POSTs — None on an onboarding stream, which holds no conversation
    /// to send into. The lane carries only what admission needs, never the terminal headers, so a
    /// send can never disturb the held stream's claim.
    pub fn send_lane(&self) -> Option<SendLane> {
        self.workspace_url.as_ref()?;
        Some(SendLane {
            endpoint: self.endpoint(),
            session_id: self.session_id.clone(),
            token: self.token.clone(),
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

    /// GET the served client binary for `target` from the gateway into `dest`.
    pub fn fetch_client_binary(&self, target: &str, dest: &Path) -> Result<(), String> {
        let url = format!(
            "{}/ufo/bin/{target}",
            self.gateway_url.trim_end_matches('/')
        );
        let response = opened(self.agent.get(&url).call())?;
        let mut reader = response.into_reader();
        let mut file = File::create(dest)
            .map_err(|error| format!("could not stage {}: {error}", dest.display()))?;
        std::io::copy(&mut reader, &mut file)
            .map_err(|error| format!("could not stage {}: {error}", dest.display()))?;
        Ok(())
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
        self.dressed(self.agent.request(method, url))
    }

    fn dressed(&self, request: ureq::Request) -> ureq::Request {
        let mut request = request
            .set("content-type", "text/plain")
            .set("x-ufo-session", &self.session_id)
            .set("x-ufo-tty", if self.tty { "1" } else { "0" })
            .set("x-ufo-installed", if self.installed { "1" } else { "0" })
            .set("x-ufo-script", env!("CARGO_PKG_VERSION"));
        if let Some(zone) = system_timezone() {
            request = request.set("x-ufo-timezone", zone);
        }
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

/// The instant-send ack: the turn that took the message, whether this send opened it, and the
/// arrival id the turn names when it folds the message in.
#[derive(Debug, Clone, PartialEq)]
pub struct SentAck {
    pub turn_id: String,
    pub opened: bool,
    pub arrival_id: String,
}

/// Builds send-only POSTs: one message admitted the moment it is typed, on its own connection,
/// while the held stream keeps the tail.
#[derive(Clone)]
pub struct SendLane {
    endpoint: String,
    session_id: String,
    token: Option<String>,
}

impl SendLane {
    /// POST one message under `send_id`. A retry with the same id is admitted once and answers
    /// the same ack, so a flaky link never doubles a message.
    pub fn send(&self, send_id: &str, text: &str) -> Result<SentAck, String> {
        let mut request = build_agent()
            .request("POST", &self.endpoint)
            .set("content-type", "text/plain")
            .set("x-ufo-session", &self.session_id)
            .set("x-ufo-send", "1")
            .set("x-ufo-send-id", &header_safe(send_id));
        if let Some(zone) = system_timezone() {
            request = request.set("x-ufo-timezone", zone);
        }
        if let Some(token) = &self.token {
            request = request.set("authorization", &format!("Bearer {token}"));
        }
        let response = match request.send_string(text) {
            Ok(response) => response,
            Err(ureq::Error::Status(code, response)) => {
                return Err(format!(
                    "send failed ({code}): {}",
                    response.into_string().unwrap_or_default().trim()
                ))
            }
            Err(error) => return Err(format!("lost connection ({error})")),
        };
        let body = response
            .into_string()
            .map_err(|error| format!("lost connection ({error})"))?;
        sent_ack(&body).ok_or_else(|| "the server acknowledged nothing".to_string())
    }

    /// Take back one acknowledged send the turn has not taken up. Ok(true) and the words are the
    /// member's again; Ok(false) and they already left their hands — folded into a turn, founded
    /// onto one, or retracted before.
    pub fn retract(&self, arrival_id: &str) -> Result<bool, String> {
        let mut request = build_agent()
            .request("POST", &self.endpoint)
            .set("content-type", "text/plain")
            .set("x-ufo-session", &self.session_id)
            .set("x-ufo-unsend", &header_safe(arrival_id));
        if let Some(token) = &self.token {
            request = request.set("authorization", &format!("Bearer {token}"));
        }
        match request.send_string("") {
            Ok(_) => Ok(true),
            Err(ureq::Error::Status(409, _)) => Ok(false),
            Err(ureq::Error::Status(code, response)) => Err(format!(
                "unsend failed ({code}): {}",
                response.into_string().unwrap_or_default().trim()
            )),
            Err(error) => Err(format!("lost connection ({error})")),
        }
    }
}

/// The `sent` ack inside a send-only response body.
fn sent_ack(body: &str) -> Option<SentAck> {
    body.lines().find_map(|line| match parse_line(line) {
        Directive::Sent {
            turn_id,
            opened,
            arrival_id,
        } => Some(SentAck {
            turn_id,
            opened,
            arrival_id,
        }),
        _ => None,
    })
}

/// One member stop, prepared and not yet sent: the whole request, so the thread watching for Esc
/// fires it without reaching back into the session the drain is reading.
pub struct Stop(ureq::Request);

impl Stop {
    /// End the conversation's running turn. The body is empty — a stop admits no message — and the
    /// answer is the cancelled terminal the held stream is about to render, so it is discarded here.
    pub fn send(self) -> Result<(), String> {
        match self.0.send_string("") {
            Ok(response) => {
                let _ = response.into_string();
                Ok(())
            }
            Err(ureq::Error::Status(code, response)) => Err(format!(
                "the server answered {code}: {}",
                response.into_string().unwrap_or_default().trim()
            )),
            Err(error) => Err(format!("lost connection ({error})")),
        }
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
        assert_eq!(
            parse_line("you\tmy words"),
            Directive::You("my words".into())
        );
        assert_eq!(
            parse_line("sent\tturn-9\t0\tarr-3"),
            Directive::Sent {
                turn_id: "turn-9".into(),
                opened: false,
                arrival_id: "arr-3".into(),
            }
        );
        assert_eq!(
            parse_line("absorbed\tarr-3\tarr-4"),
            Directive::Absorbed(vec!["arr-3".into(), "arr-4".into()])
        );
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
    fn drops_unknown_verbs() {
        assert_eq!(parse_line("debugger\thttps://d"), Directive::Unknown);
        assert_eq!(parse_line("slack\tConnect Slack"), Directive::Unknown);
        assert_eq!(parse_line("auth\t/auth/start"), Directive::Unknown);
        assert_eq!(parse_line("ufo\t15 line1\\nline2"), Directive::Unknown);
        assert_eq!(parse_line("logout"), Directive::Unknown);
        assert_eq!(parse_line("mystery"), Directive::Unknown);
    }

    #[derive(serde::Deserialize)]
    struct FixtureRow {
        verb: String,
        fields: Vec<String>,
        line: String,
    }

    /// The parsed directive mapped back to its wire spelling. Exhaustive on purpose: a new
    /// variant fails to compile here until the fixture row that proves it exists.
    fn canonical(directive: &Directive) -> Option<(&'static str, Vec<String>)> {
        match directive {
            Directive::Say(text) => Some(("say", vec![text.clone()])),
            Directive::You(text) => Some(("you", vec![text.clone()])),
            Directive::Note(text) => Some(("note", vec![text.clone()])),
            Directive::Txt(text) => Some(("txt", vec![text.clone()])),
            Directive::Status(text) => Some(("status", vec![text.clone()])),
            Directive::Ask(prompt) => Some(("ask", vec![prompt.clone()])),
            Directive::Choose { prompt, options } => {
                let mut fields = vec![prompt.clone()];
                fields.extend(options.iter().cloned());
                Some(("choose", fields))
            }
            Directive::Secret {
                sealed,
                slot,
                prompt,
            } => Some(("secret", vec![sealed.clone(), slot.clone(), prompt.clone()])),
            Directive::Poll(seconds) => Some(("poll", vec![format!("{seconds}")])),
            Directive::Since(cursor) => {
                Some(("since", cursor.splitn(2, ':').map(str::to_string).collect()))
            }
            Directive::Run(op) => Some((
                "run",
                vec![
                    op.op_id.clone(),
                    op.kind.clone(),
                    op.name.clone(),
                    op.timeout_s.to_string(),
                    op.arg.clone(),
                    op.params.clone(),
                ],
            )),
            Directive::Token(token) => Some(("token", vec![token.clone()])),
            Directive::Workspace(url) => Some(("workspace", vec![url.clone()])),
            Directive::Install => Some(("install", vec![])),
            Directive::File { name, size, url } => {
                Some(("file", vec![name.clone(), size.clone(), url.clone()]))
            }
            Directive::Exit(code) => Some(("exit", vec![code.to_string()])),
            Directive::Sent {
                turn_id,
                opened,
                arrival_id,
            } => Some((
                "sent",
                vec![
                    turn_id.clone(),
                    if *opened { "1" } else { "0" }.to_string(),
                    arrival_id.clone(),
                ],
            )),
            Directive::Absorbed(arrival_ids) => Some(("absorbed", arrival_ids.clone())),
            Directive::Unknown => None,
        }
    }

    #[test]
    fn replays_the_wire_fixture() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/directives.jsonl"
        );
        let raw = std::fs::read_to_string(path).expect("wire fixture missing");
        let mut replayed = 0;
        let mut dropped = 0;
        for line in raw.lines() {
            let row: FixtureRow = serde_json::from_str(line).expect("fixture row");
            let parsed = parse_line(&row.line);
            match canonical(&parsed) {
                Some((verb, fields)) => {
                    assert_eq!(verb, row.verb, "line {:?}", row.line);
                    assert_eq!(fields, row.fields, "verb {:?}", row.verb);
                    replayed += 1;
                }
                None => {
                    assert!(
                        matches!(row.verb.as_str(), "debugger" | "slack" | "first"),
                        "verb {:?} parsed to Unknown",
                        row.verb
                    );
                    dropped += 1;
                }
            }
        }
        assert_eq!(replayed + dropped, raw.lines().count());
        assert_eq!(dropped, 3);
        assert!(replayed > 0);
    }

    #[test]
    fn a_send_ack_is_read_out_of_the_response_body() {
        let ack = sent_ack("note\tqueued\nsent\tturn-7\t1\tarr-2\n").expect("ack parses");
        assert_eq!(
            ack,
            SentAck {
                turn_id: "turn-7".into(),
                opened: true,
                arrival_id: "arr-2".into(),
            }
        );
        assert_eq!(sent_ack("note\tno ack here"), None);
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

    #[test]
    fn an_onboarding_session_holds_no_turn_to_stop() {
        let session = Session::new(
            "https://gw".into(),
            None,
            "onboard".into(),
            None,
            "sid".into(),
            None,
            false,
            true,
        );
        assert!(session.stop().is_none());
    }

    fn served(status: &str, body: &str) -> (String, std::thread::JoinHandle<String>) {
        use std::io::{Read, Write};

        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("a local port");
        let port = listener.local_addr().expect("the bound address").port();
        let reply = format!(
            "HTTP/1.1 {status}\r\ncontent-type: text/plain\r\ncontent-length: {}\r\n\r\n{body}",
            body.len()
        );
        let handle = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().expect("the client connects");
            let mut seen: Vec<u8> = Vec::new();
            let mut byte = [0u8; 1];
            while !seen.ends_with(b"\r\n\r\n") {
                match socket.read(&mut byte) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => seen.push(byte[0]),
                }
            }
            socket.write_all(reply.as_bytes()).expect("the reply lands");
            String::from_utf8_lossy(&seen).into_owned()
        });
        (format!("http://127.0.0.1:{port}"), handle)
    }

    fn stopping(base: String) -> Session {
        Session::new(
            base.clone(),
            Some(base),
            "abc".into(),
            Some("tok".into()),
            "sid".into(),
            None,
            false,
            true,
        )
    }

    #[test]
    fn a_stop_posts_an_empty_body_to_the_conversation() {
        let (base, serving) = served("200 OK", "");
        let session = stopping(base);
        assert_eq!(session.stop().expect("a signed-in stop").send(), Ok(()));
        let request = serving.join().expect("the server thread");
        let lowered = request.to_lowercase();
        assert!(
            request.starts_with("POST /surface/ufo/abc HTTP/1.1\r\n"),
            "{request}"
        );
        assert!(lowered.contains("x-ufo-stop: 1"), "{request}");
        assert!(lowered.contains("content-length: 0"), "{request}");
        assert!(lowered.contains("authorization: bearer tok"), "{request}");
        assert!(lowered.contains("x-ufo-session: sid"), "{request}");
    }

    #[test]
    fn a_refused_stop_names_what_the_server_answered() {
        let (base, serving) = served("400 Bad Request", "a stop admits no message");
        let session = stopping(base);
        assert_eq!(
            session.stop().expect("a signed-in stop").send(),
            Err("the server answered 400: a stop admits no message".to_string())
        );
        let _ = serving.join();
    }
}
