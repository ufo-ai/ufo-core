use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::fold::Frame;
use crate::ui::toolrender::OpView;
use crate::ui::{meter_line, RESUMED_NOTE};
use crate::wire::{
    AuthorizationAnswer, AuthorizationChoice, AuthorizationOption, Directive, OpRequest,
    RuntimeAttestation,
};

const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    SessionStart {
        v: u32,
        channel: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        workspace_url: Option<String>,
        client_version: String,
    },
    TurnStart,
    TextDelta {
        text: String,
    },
    Message {
        text: String,
    },
    MemberMessage {
        text: String,
    },
    FiredMessage {
        text: String,
    },
    Note {
        text: String,
    },
    Status {
        text: String,
    },
    OpStart {
        op_id: String,
        kind: String,
        name: String,
        arg: String,
        command: String,
    },
    OpEnd {
        op_id: String,
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    FileShared {
        name: String,
        size: String,
        url: String,
    },
    Runtime {
        #[serde(flatten)]
        attestation: Box<RuntimeAttestation>,
    },
    InputRequest {
        id: u64,
        prompt: String,
        options: Vec<String>,
        #[serde(skip_serializing_if = "is_false")]
        multi_select: bool,
    },
    AuthorizationRequest {
        id: u64,
        prompt: String,
        options: Vec<AuthorizationOption>,
    },
    SecretRequest {
        id: u64,
        slot: String,
        prompt: String,
    },
    TurnEnd,
    MessageSent {
        turn_id: String,
        opened_run: bool,
        arrival_id: String,
    },
    MessageAbsorbed {
        arrival_ids: Vec<String>,
    },
    SignedIn {
        workspace_url: String,
        channel: String,
    },
    Error {
        message: String,
        fatal: bool,
    },
    Exit {
        code: i32,
    },
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Command {
    Send {
        text: String,
    },
    Answer {
        id: u64,
        text: String,
    },
    Authorize {
        id: u64,
        choice: AuthorizationChoice,
    },
    Secret {
        id: u64,
        value: String,
    },
    Detach,
    Shutdown,
}

#[derive(Debug, PartialEq)]
pub enum AnswerRouting {
    Post(String),
    Authorization(AuthorizationAnswer),
    Secret {
        sealed: String,
        slot: String,
        value: String,
    },
    Detach,
    Shutdown,
}

struct PendingSecret {
    sealed: String,
    slot: String,
}

struct PendingAuthorization {
    authorization_id: String,
    options: Vec<AuthorizationOption>,
}

#[derive(Default)]
pub struct Driver {
    next_id: u64,
    asks: HashSet<u64>,
    authorizations: HashMap<u64, PendingAuthorization>,
    secrets: HashMap<u64, PendingSecret>,
}

/// The events a live frame publishes. A frame that ends the turn publishes nothing of its own: the
/// cap directives that follow it do.
fn frame_events(event: &str, data: &str) -> Vec<Event> {
    let Some(frame) = Frame::decode(event, data) else {
        return Vec::new();
    };
    match frame {
        Frame::Message { text } => vec![Event::TextDelta { text }],
        Frame::Activity { text } => vec![Event::Status { text }],
        Frame::SubagentActivity(run) if !run.activity.is_empty() => vec![Event::Status {
            text: format!("{}: {}", run.label(), run.activity),
        }],
        Frame::SubagentActivity(_) => Vec::new(),
        Frame::Cost {
            tokens,
            cost_micro_usd,
        } => vec![Event::Status {
            text: meter_line(tokens, cost_micro_usd),
        }],
        Frame::Absorbed { arrivals } => vec![Event::MessageAbsorbed {
            arrival_ids: arrivals,
        }],
        Frame::Reply { text, .. } | Frame::Comment { text, .. } => vec![Event::Message { text }],
        Frame::Resumed { .. } => vec![Event::Note {
            text: RESUMED_NOTE.to_string(),
        }],
        Frame::Sources { .. } | Frame::Terminal(_) | Frame::Parked { .. } => Vec::new(),
    }
}

impl Driver {
    pub fn new() -> Driver {
        Driver::default()
    }

    pub fn session_start(&self, channel: &str, workspace_url: Option<&str>) -> Event {
        Event::SessionStart {
            v: PROTOCOL_VERSION,
            channel: channel.to_string(),
            workspace_url: workspace_url.map(str::to_string),
            client_version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }

    pub fn on_turn_start(&self) -> Event {
        Event::TurnStart
    }

    pub fn on_directive(&mut self, directive: &Directive) -> Vec<Event> {
        match directive {
            Directive::Frame { event, data } => frame_events(event, data),
            Directive::Say(text) => vec![Event::Message { text: text.clone() }],
            Directive::You(text) => vec![Event::MemberMessage { text: text.clone() }],
            Directive::Fired(text) => vec![Event::FiredMessage { text: text.clone() }],
            Directive::Note(text) => vec![Event::Note { text: text.clone() }],
            Directive::File { name, size, url } => vec![Event::FileShared {
                name: name.clone(),
                size: size.clone(),
                url: url.clone(),
            }],
            Directive::Runtime(attestation) => vec![Event::Runtime {
                attestation: Box::new(attestation.clone()),
            }],
            Directive::Ask(prompt) => vec![self.raise_input(prompt.clone(), Vec::new(), false)],
            Directive::Choose {
                prompt,
                options,
                multiple,
            } => {
                vec![self.raise_input(prompt.clone(), options.clone(), *multiple)]
            }
            Directive::Authorize {
                authorization_id,
                prompt,
                options,
            } => vec![self.raise_authorization(
                authorization_id.clone(),
                prompt.clone(),
                options.clone(),
            )],
            Directive::Secret {
                sealed,
                slot,
                prompt,
            } => vec![self.raise_secret(sealed, slot, prompt)],
            Directive::Exit(code) => vec![Event::Exit { code: *code }],
            Directive::Sent {
                turn_id,
                opened,
                arrival_id,
            } => vec![Event::MessageSent {
                turn_id: turn_id.clone(),
                opened_run: *opened,
                arrival_id: arrival_id.clone(),
            }],
            Directive::Poll(_)
            | Directive::Listen(_)
            | Directive::Since(_)
            | Directive::Run(_)
            | Directive::Token(_)
            | Directive::Workspace(_)
            | Directive::Install
            | Directive::Unknown => Vec::new(),
        }
    }

    fn raise_input(&mut self, prompt: String, options: Vec<String>, multi_select: bool) -> Event {
        let id = self.mint();
        self.asks.insert(id);
        Event::InputRequest {
            id,
            prompt,
            options,
            multi_select,
        }
    }

    fn raise_secret(&mut self, sealed: &str, slot: &str, prompt: &str) -> Event {
        let id = self.mint();
        self.secrets.insert(
            id,
            PendingSecret {
                sealed: sealed.to_string(),
                slot: slot.to_string(),
            },
        );
        Event::SecretRequest {
            id,
            slot: slot.to_string(),
            prompt: prompt.to_string(),
        }
    }

    fn raise_authorization(
        &mut self,
        authorization_id: String,
        prompt: String,
        options: Vec<AuthorizationOption>,
    ) -> Event {
        let id = self.mint();
        self.authorizations.insert(
            id,
            PendingAuthorization {
                authorization_id,
                options: options.clone(),
            },
        );
        Event::AuthorizationRequest {
            id,
            prompt,
            options,
        }
    }

    fn mint(&mut self) -> u64 {
        self.next_id += 1;
        self.next_id
    }

    pub fn on_op_started(&self, op: &OpRequest) -> Event {
        Event::OpStart {
            op_id: op.op_id.clone(),
            kind: op.kind.clone(),
            name: op.name.clone(),
            arg: op.arg.clone(),
            command: OpView::from_request(op).title(None),
        }
    }

    pub fn on_op_finished(&self, op_id: &str, result: &Result<Vec<u8>, String>) -> Event {
        Event::OpEnd {
            op_id: op_id.to_string(),
            ok: result.is_ok(),
            error: result.as_ref().err().cloned(),
        }
    }

    pub fn on_turn_end(&self) -> Event {
        Event::TurnEnd
    }

    pub fn message_sent(&self, ack: &crate::wire::SentAck) -> Event {
        Event::MessageSent {
            turn_id: ack.turn_id.clone(),
            opened_run: ack.opened,
            arrival_id: ack.arrival_id.clone(),
        }
    }

    pub fn signed_in(&self, workspace_url: &str, channel: &str) -> Event {
        Event::SignedIn {
            workspace_url: workspace_url.to_string(),
            channel: channel.to_string(),
        }
    }

    pub fn on_stream_error(&self, message: &str, fatal: bool) -> Event {
        Event::Error {
            message: message.to_string(),
            fatal,
        }
    }

    pub fn answer_body(&mut self, command: Command) -> Result<AnswerRouting, Event> {
        match command {
            Command::Send { text } => Ok(AnswerRouting::Post(text)),
            Command::Answer { id, text } => {
                if self.asks.remove(&id) {
                    Ok(AnswerRouting::Post(text))
                } else {
                    Err(refused(format!("no input request {id} is outstanding")))
                }
            }
            Command::Authorize { id, choice } => match self.authorizations.remove(&id) {
                Some(pending) => match pending
                    .options
                    .into_iter()
                    .find(|option| option.choice == choice)
                {
                    Some(option) => Ok(AnswerRouting::Authorization(AuthorizationAnswer {
                        authorization_id: pending.authorization_id,
                        choice,
                        text: option.label,
                    })),
                    None => Err(refused(format!(
                        "authorization request {id} has no such choice"
                    ))),
                },
                None => Err(refused(format!(
                    "no authorization request {id} is outstanding"
                ))),
            },
            Command::Secret { id, value } => match self.secrets.remove(&id) {
                Some(pending) => Ok(AnswerRouting::Secret {
                    sealed: pending.sealed,
                    slot: pending.slot,
                    value,
                }),
                None => Err(refused(format!("no secret request {id} is outstanding"))),
            },
            Command::Detach => Ok(AnswerRouting::Detach),
            Command::Shutdown => Ok(AnswerRouting::Shutdown),
        }
    }
}

fn is_false(value: &bool) -> bool {
    !value
}

pub fn parse_command(line: &str) -> Result<Command, Event> {
    serde_json::from_str(line).map_err(|error| refused(format!("bad command: {error}")))
}

fn refused(message: String) -> Event {
    Event::Error {
        message,
        fatal: false,
    }
}

pub fn emit(event: &Event) -> String {
    let mut line = serde_json::to_string(event).expect("events serialize");
    line.push('\n');
    line
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::RuntimeIdentity;

    fn op() -> OpRequest {
        OpRequest {
            op_id: "op1".into(),
            kind: "exec".into(),
            name: "exec".into(),
            timeout_s: 120,
            arg: "notes.md".into(),
            params: "{\"argv\":[\"ls\"]}".into(),
        }
    }

    #[test]
    fn content_directives_map_to_events() {
        let mut driver = Driver::new();
        assert_eq!(
            driver.on_directive(&Directive::Frame {
                event: "message".into(),
                data: "{\"text\":\"chunk\"}".into(),
            }),
            vec![Event::TextDelta {
                text: "chunk".into()
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Say("said".into())),
            vec![Event::Message {
                text: "said".into()
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Note("dim".into())),
            vec![Event::Note { text: "dim".into() }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Frame {
                event: "activity".into(),
                data: "{\"text\":\"Listing files.\"}".into(),
            }),
            vec![Event::Status {
                text: "Listing files.".into()
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Frame {
                event: "cost".into(),
                data: "{\"cost_micro_usd\":110,\"tokens\":12}".into(),
            }),
            vec![Event::Status {
                text: "12 tok - $0.000110".into()
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Frame {
                event: "absorbed".into(),
                data: "{\"arrivals\":[\"arr-1\"]}".into(),
            }),
            vec![Event::MessageAbsorbed {
                arrival_ids: vec!["arr-1".into()]
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Frame {
                event: "terminal".into(),
                data: "{\"status\":\"done\"}".into(),
            }),
            Vec::<Event>::new()
        );
        assert_eq!(
            driver.on_directive(&Directive::File {
                name: "report.pdf".into(),
                size: "123".into(),
                url: "https://x".into(),
            }),
            vec![Event::FileShared {
                name: "report.pdf".into(),
                size: "123".into(),
                url: "https://x".into(),
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Runtime(RuntimeAttestation {
                runtime: RuntimeIdentity {
                    revision: Some("abc12345".into()),
                    image_digest: Some(format!("sha256:{}", "a".repeat(64))),
                    config_digest: format!("sha256:{}", "b".repeat(64)),
                    sandbox_backend: "e2b".into(),
                    sandbox_digest: format!("sha256:{}", "c".repeat(64)),
                },
                model: "glm-5.3-flash".into(),
                reasoning: Some("high".into()),
                environment: Some(format!("sha256:{}", "d".repeat(64))),
            })),
            vec![Event::Runtime {
                attestation: Box::new(RuntimeAttestation {
                    runtime: RuntimeIdentity {
                        revision: Some("abc12345".into()),
                        image_digest: Some(format!("sha256:{}", "a".repeat(64))),
                        config_digest: format!("sha256:{}", "b".repeat(64)),
                        sandbox_backend: "e2b".into(),
                        sandbox_digest: format!("sha256:{}", "c".repeat(64)),
                    },
                    model: "glm-5.3-flash".into(),
                    reasoning: Some("high".into()),
                    environment: Some(format!("sha256:{}", "d".repeat(64))),
                }),
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Exit(2)),
            vec![Event::Exit { code: 2 }]
        );
    }

    #[test]
    fn session_plumbing_publishes_nothing() {
        let mut driver = Driver::new();
        for directive in [
            Directive::Poll(1.0),
            Directive::Since("turn-1:cursor-9".into()),
            Directive::Run(op()),
            Directive::Token("tok".into()),
            Directive::Workspace("https://w".into()),
            Directive::Install,
            Directive::Unknown,
        ] {
            assert_eq!(driver.on_directive(&directive), Vec::new());
        }
    }

    #[test]
    fn requests_mint_increasing_ids() {
        let mut driver = Driver::new();
        assert_eq!(
            driver.on_directive(&Directive::Ask(">".into())),
            vec![Event::InputRequest {
                id: 1,
                prompt: ">".into(),
                options: Vec::new(),
                multi_select: false,
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Choose {
                prompt: "Pick:".into(),
                options: vec!["a".into(), "b".into()],
                multiple: true,
            }),
            vec![Event::InputRequest {
                id: 2,
                prompt: "Pick:".into(),
                options: vec!["a".into(), "b".into()],
                multi_select: true,
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Secret {
                sealed: "sealed-blob".into(),
                slot: "OPENAI_API_KEY".into(),
                prompt: "Paste the key".into(),
            }),
            vec![Event::SecretRequest {
                id: 3,
                slot: "OPENAI_API_KEY".into(),
                prompt: "Paste the key".into(),
            }]
        );
        assert_eq!(
            driver.on_directive(&Directive::Ask("again".into())),
            vec![Event::InputRequest {
                id: 4,
                prompt: "again".into(),
                options: Vec::new(),
                multi_select: false,
            }]
        );
    }

    #[test]
    fn ops_bracket_their_run() {
        let driver = Driver::new();
        assert_eq!(
            driver.on_op_started(&op()),
            Event::OpStart {
                op_id: "op1".into(),
                kind: "exec".into(),
                name: "exec".into(),
                arg: "notes.md".into(),
                command: "exec ls".into(),
            }
        );
        assert_eq!(
            driver.on_op_finished("op1", &Ok(b"payload".to_vec())),
            Event::OpEnd {
                op_id: "op1".into(),
                ok: true,
                error: None,
            }
        );
        assert_eq!(
            driver.on_op_finished("op1", &Err("ENOENT: notes.md".into())),
            Event::OpEnd {
                op_id: "op1".into(),
                ok: false,
                error: Some("ENOENT: notes.md".into()),
            }
        );
    }

    #[test]
    fn sends_and_answers_route_to_a_post() {
        let mut driver = Driver::new();
        assert_eq!(
            driver
                .answer_body(Command::Send {
                    text: "hello".into()
                })
                .unwrap(),
            AnswerRouting::Post("hello".into())
        );
        driver.on_directive(&Directive::Ask(">".into()));
        assert_eq!(
            driver
                .answer_body(Command::Answer {
                    id: 1,
                    text: "yes".into()
                })
                .unwrap(),
            AnswerRouting::Post("yes".into())
        );
        assert_eq!(
            driver.answer_body(Command::Detach).unwrap(),
            AnswerRouting::Detach
        );
        assert_eq!(
            driver.answer_body(Command::Shutdown).unwrap(),
            AnswerRouting::Shutdown
        );
    }

    #[test]
    fn authorization_answers_route_by_code_and_keep_labels_display_only() {
        let mut driver = Driver::new();
        let options = vec![
            AuthorizationOption {
                label: "Allow once".into(),
                description: "This action only.".into(),
                choice: AuthorizationChoice::Allow,
            },
            AuthorizationOption {
                label: "Deny".into(),
                description: "Do not allow this action.".into(),
                choice: AuthorizationChoice::Deny,
            },
            AuthorizationOption {
                label: "Always allow for GitHub".into(),
                description: "Future repository reads from this account.".into(),
                choice: AuthorizationChoice::Always,
            },
        ];
        assert_eq!(
            driver.on_directive(&Directive::Authorize {
                authorization_id: "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6".into(),
                prompt: "Proceed?".into(),
                options: options.clone(),
            }),
            vec![Event::AuthorizationRequest {
                id: 1,
                prompt: "Proceed?".into(),
                options,
            }]
        );
        assert_eq!(
            driver
                .answer_body(Command::Authorize {
                    id: 1,
                    choice: AuthorizationChoice::Always,
                })
                .unwrap(),
            AnswerRouting::Authorization(AuthorizationAnswer {
                authorization_id: "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6".into(),
                choice: AuthorizationChoice::Always,
                text: "Always allow for GitHub".into(),
            })
        );
    }

    #[test]
    fn ordinary_text_cannot_answer_an_authorization_request() {
        let mut driver = Driver::new();
        driver.on_directive(&Directive::Authorize {
            authorization_id: "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6".into(),
            prompt: "Proceed?".into(),
            options: vec![AuthorizationOption {
                label: "Proceed once".into(),
                description: String::new(),
                choice: AuthorizationChoice::Allow,
            }],
        });
        assert_eq!(
            driver.answer_body(Command::Answer {
                id: 1,
                text: "Proceed once".into(),
            }),
            Err(Event::Error {
                message: "no input request 1 is outstanding".into(),
                fatal: false,
            })
        );
        assert_eq!(
            driver.answer_body(Command::Send {
                text: "Proceed once".into(),
            }),
            Ok(AnswerRouting::Post("Proceed once".into()))
        );
        assert!(driver
            .answer_body(Command::Authorize {
                id: 1,
                choice: AuthorizationChoice::Allow,
            })
            .is_ok());
    }

    #[test]
    fn secret_answers_carry_the_sealed_slot() {
        let mut driver = Driver::new();
        driver.on_directive(&Directive::Secret {
            sealed: "sealed-blob".into(),
            slot: "OPENAI_API_KEY".into(),
            prompt: "Paste the key".into(),
        });
        assert_eq!(
            driver
                .answer_body(Command::Secret {
                    id: 1,
                    value: "sk-live".into()
                })
                .unwrap(),
            AnswerRouting::Secret {
                sealed: "sealed-blob".into(),
                slot: "OPENAI_API_KEY".into(),
                value: "sk-live".into(),
            }
        );
    }

    #[test]
    fn an_answered_request_is_not_outstanding_twice() {
        let mut driver = Driver::new();
        driver.on_directive(&Directive::Ask(">".into()));
        driver.on_directive(&Directive::Secret {
            sealed: "sealed-blob".into(),
            slot: "SLOT".into(),
            prompt: "Paste".into(),
        });
        let answer = Command::Answer {
            id: 1,
            text: "yes".into(),
        };
        assert!(driver.answer_body(answer).is_ok());
        assert_eq!(
            driver.answer_body(Command::Answer {
                id: 1,
                text: "again".into()
            }),
            Err(Event::Error {
                message: "no input request 1 is outstanding".into(),
                fatal: false,
            })
        );
        assert!(driver
            .answer_body(Command::Secret {
                id: 2,
                value: "sk-live".into()
            })
            .is_ok());
        assert_eq!(
            driver.answer_body(Command::Secret {
                id: 2,
                value: "sk-live".into()
            }),
            Err(Event::Error {
                message: "no secret request 2 is outstanding".into(),
                fatal: false,
            })
        );
    }

    #[test]
    fn an_unknown_id_is_refused() {
        let mut driver = Driver::new();
        assert_eq!(
            driver.answer_body(Command::Answer {
                id: 9,
                text: "yes".into()
            }),
            Err(Event::Error {
                message: "no input request 9 is outstanding".into(),
                fatal: false,
            })
        );
        assert_eq!(
            driver.answer_body(Command::Secret {
                id: 9,
                value: "sk-live".into()
            }),
            Err(Event::Error {
                message: "no secret request 9 is outstanding".into(),
                fatal: false,
            })
        );
    }

    #[test]
    fn a_secret_request_is_not_answerable_as_an_input() {
        let mut driver = Driver::new();
        driver.on_directive(&Directive::Secret {
            sealed: "sealed-blob".into(),
            slot: "SLOT".into(),
            prompt: "Paste".into(),
        });
        assert_eq!(
            driver.answer_body(Command::Answer {
                id: 1,
                text: "sk-live".into()
            }),
            Err(Event::Error {
                message: "no input request 1 is outstanding".into(),
                fatal: false,
            })
        );
    }

    #[test]
    fn commands_parse() {
        assert_eq!(
            parse_command("{\"type\":\"send\",\"text\":\"hello\"}").unwrap(),
            Command::Send {
                text: "hello".into()
            }
        );
        assert_eq!(
            parse_command("{\"type\":\"answer\",\"id\":1,\"text\":\"yes\"}").unwrap(),
            Command::Answer {
                id: 1,
                text: "yes".into()
            }
        );
        assert_eq!(
            parse_command("{\"type\":\"secret\",\"id\":2,\"value\":\"sk-live\"}").unwrap(),
            Command::Secret {
                id: 2,
                value: "sk-live".into()
            }
        );
        assert_eq!(
            parse_command("{\"type\":\"authorize\",\"id\":3,\"choice\":\"deny\"}").unwrap(),
            Command::Authorize {
                id: 3,
                choice: AuthorizationChoice::Deny,
            }
        );
        assert_eq!(
            parse_command("{\"type\":\"detach\"}").unwrap(),
            Command::Detach
        );
        assert_eq!(
            parse_command("{\"type\":\"detach\"}\r\n").unwrap(),
            Command::Detach
        );
        assert_eq!(
            parse_command("{\"type\":\"shutdown\"}").unwrap(),
            Command::Shutdown
        );
    }

    #[test]
    fn a_bad_line_is_refused_without_ending_the_stream() {
        for line in [
            "",
            "not json",
            "{\"type\":",
            "{\"text\":\"hello\"}",
            "{\"type\":\"send\"}",
            "{\"type\":\"bogus\"}",
            "[1,2,3]",
        ] {
            match parse_command(line) {
                Err(Event::Error { message, fatal }) => {
                    assert!(message.starts_with("bad command: "), "{line}");
                    assert!(!fatal, "{line}");
                }
                other => panic!("{line} parsed as {other:?}"),
            }
        }
    }

    #[test]
    fn an_unknown_command_names_the_ones_that_exist() {
        let Err(Event::Error { message, .. }) = parse_command("{\"type\":\"bogus\"}") else {
            panic!("an unknown command is refused");
        };
        for known in [
            "send",
            "answer",
            "authorize",
            "secret",
            "detach",
            "shutdown",
        ] {
            assert!(message.contains(known), "{message}");
        }
    }

    #[test]
    fn events_serialize_to_stable_lines() {
        let driver = Driver::new();
        assert_eq!(
            emit(&driver.session_start("abc123", Some("https://w"))),
            format!(
                "{{\"type\":\"session_start\",\"v\":1,\"channel\":\"abc123\",\"workspace_url\":\"https://w\",\"client_version\":\"{}\"}}\n",
                env!("CARGO_PKG_VERSION")
            )
        );
        assert_eq!(
            emit(&driver.session_start("onboard", None)),
            format!(
                "{{\"type\":\"session_start\",\"v\":1,\"channel\":\"onboard\",\"client_version\":\"{}\"}}\n",
                env!("CARGO_PKG_VERSION")
            )
        );
        assert_eq!(emit(&driver.on_turn_start()), "{\"type\":\"turn_start\"}\n");
        assert_eq!(
            emit(&Event::TextDelta {
                text: "hi\nthere".into()
            }),
            "{\"type\":\"text_delta\",\"text\":\"hi\\nthere\"}\n"
        );
        assert_eq!(
            emit(&driver.on_op_started(&op())),
            "{\"type\":\"op_start\",\"op_id\":\"op1\",\"kind\":\"exec\",\"name\":\"exec\",\"arg\":\"notes.md\",\"command\":\"exec ls\"}\n"
        );
        assert_eq!(
            emit(&driver.on_op_finished("op1", &Ok(Vec::new()))),
            "{\"type\":\"op_end\",\"op_id\":\"op1\",\"ok\":true}\n"
        );
        assert_eq!(
            emit(&driver.on_op_finished("op1", &Err("EIO".into()))),
            "{\"type\":\"op_end\",\"op_id\":\"op1\",\"ok\":false,\"error\":\"EIO\"}\n"
        );
        assert_eq!(
            emit(&Event::InputRequest {
                id: 1,
                prompt: "Pick:".into(),
                options: vec!["a".into()],
                multi_select: false,
            }),
            "{\"type\":\"input_request\",\"id\":1,\"prompt\":\"Pick:\",\"options\":[\"a\"]}\n"
        );
        assert_eq!(
            emit(&Event::InputRequest {
                id: 2,
                prompt: "Pick all:".into(),
                options: vec!["a".into(), "b".into()],
                multi_select: true,
            }),
            "{\"type\":\"input_request\",\"id\":2,\"prompt\":\"Pick all:\",\"options\":[\"a\",\"b\"],\"multi_select\":true}\n"
        );
        assert_eq!(
            emit(&Event::SecretRequest {
                id: 3,
                slot: "SLOT".into(),
                prompt: "Paste".into(),
            }),
            "{\"type\":\"secret_request\",\"id\":3,\"slot\":\"SLOT\",\"prompt\":\"Paste\"}\n"
        );
        assert_eq!(
            emit(&Event::AuthorizationRequest {
                id: 4,
                prompt: "Proceed?".into(),
                options: vec![AuthorizationOption {
                    label: "Proceed once".into(),
                    description: "This action only.".into(),
                    choice: AuthorizationChoice::Allow,
                }],
            }),
            "{\"type\":\"authorization_request\",\"id\":4,\"prompt\":\"Proceed?\",\"options\":[{\"label\":\"Proceed once\",\"description\":\"This action only.\",\"choice\":\"allow\"}]}\n"
        );
        assert_eq!(emit(&driver.on_turn_end()), "{\"type\":\"turn_end\"}\n");
        assert_eq!(
            emit(&driver.on_stream_error("lost connection", true)),
            "{\"type\":\"error\",\"message\":\"lost connection\",\"fatal\":true}\n"
        );
        assert_eq!(
            emit(&Event::Exit { code: 2 }),
            "{\"type\":\"exit\",\"code\":2}\n"
        );
    }

    #[test]
    fn one_event_is_one_line() {
        let events = [
            Event::TextDelta {
                text: "a\nb\r\nc\u{2028}d".into(),
            },
            Event::Message {
                text: "tab\there".into(),
            },
            Event::Note {
                text: "quote\"and\\slash".into(),
            },
        ];
        for event in &events {
            let line = emit(event);
            assert_eq!(line.matches('\n').count(), 1, "{line}");
            assert!(line.ends_with('\n'));
        }
    }
}

#[cfg(test)]
mod signin_tests {
    use super::*;

    #[test]
    fn sign_in_and_out_are_events() {
        let driver = Driver::new();
        assert_eq!(
            emit(&driver.signed_in("https://w.example", "abc123")),
            "{\"type\":\"signed_in\",\"workspace_url\":\"https://w.example\",\"channel\":\"abc123\"}\n"
        );
    }

    #[test]
    fn op_start_speaks_the_members_command() {
        let driver = Driver::new();
        let op = OpRequest {
            op_id: "op9".into(),
            kind: "exec".into(),
            name: "exec".into(),
            timeout_s: 60,
            arg: String::new(),
            params: r#"{"argv":["sh","-c","UFO_WALK_ROOT=/w export UFO_WALK_ROOT ..."],"env":{}}"#
                .into(),
        };
        let Event::OpStart { command, .. } = driver.on_op_started(&op) else {
            panic!("op_start expected");
        };
        assert_eq!(command, "list files");
    }
}
