//! Local rendering of the ops a `run` directive asks of this terminal: a header naming the act,
//! a colored diff for edits, and a bounded tail of exec output — all from data the client
//! already holds, nothing added to the wire. The rows come out full width; the transcript that
//! holds them clips them to its own.

use base64::engine::general_purpose::{STANDARD, URL_SAFE};
use base64::Engine as _;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use serde_json::Value;
use similar::{ChangeTag, TextDiff};

use crate::ops::{OP_EXEC, OP_FILE, OP_READ, OP_WRITE};
use crate::ui::theme::Theme;
use crate::wire::OpRequest;

/// How many trailing output lines an op result shows before folding.
const RESULT_TAIL_LINES: usize = 5;

const MARKER: &str = "⏺ ";
const INDENT: &str = "  ";
const BODY_MAX_LINES: usize = 40;
const DIFF_CONTEXT_LINES: usize = 2;

/// One op as the transcript states it: the header when the op starts, the body when it answers.
pub struct OpView {
    pub kind: String,
    pub name: String,
    pub arg: String,
    pub params: String,
}

impl OpView {
    pub fn from_request(op: &OpRequest) -> OpView {
        OpView {
            kind: op.kind.clone(),
            name: op.name.clone(),
            arg: op.arg.clone(),
            params: op.params.clone(),
        }
    }

    /// The header line stating what is running: the agent's own narration where it wrote one,
    /// else the command a member reads — `⏺ edit src/main.rs`.
    pub fn header(&self, description: Option<&str>, theme: &Theme) -> Line<'static> {
        Line::styled(
            format!("{MARKER}{}", self.title(description)),
            theme.tool_title,
        )
    }

    /// What the header states, for the activity row to say while the op runs.
    pub fn title(&self, description: Option<&str>) -> String {
        match description {
            Some(said) if !said.trim().is_empty() => said.trim().to_string(),
            _ => self.act(),
        }
    }

    /// The body lines once the op answered: a diff for edits (old/new from the op's own params),
    /// the bounded output tail for exec, one status line otherwise. `reply` is the op's reply
    /// body, `failed` the failure string when the op refused.
    pub fn body(&self, reply: Result<&[u8], &str>, theme: &Theme) -> Vec<Line<'static>> {
        let reply = match reply {
            Ok(reply) => reply,
            Err(failure) => return vec![Line::styled(format!("{INDENT}{failure}"), theme.error)],
        };
        match self.kind.as_str() {
            OP_EXEC => exec_tail(reply, theme),
            OP_FILE => self.file_result(reply, theme),
            _ => Vec::new(),
        }
    }

    fn act(&self) -> String {
        let params = self.parsed();
        match self.kind.as_str() {
            OP_EXEC => command_display(&argv(&params)),
            OP_WRITE | OP_READ => named(&self.kind, &self.arg),
            OP_FILE => match self.name.as_str() {
                "grep" => named(
                    "grep",
                    format!("{} {}", field(&params, "pattern"), field(&params, "path")).trim(),
                ),
                "glob" => named("glob", field(&params, "pattern")),
                "changes" => "changes".to_string(),
                name => named(name, field(&params, "path")),
            },
            other => other.to_string(),
        }
    }

    fn parsed(&self) -> Value {
        serde_json::from_str(&self.params).unwrap_or(Value::Null)
    }

    fn file_result(&self, reply: &[u8], theme: &Theme) -> Vec<Line<'static>> {
        let result: Value = serde_json::from_slice(reply).unwrap_or(Value::Null);
        if let Some(refusal) = result.get("error").and_then(Value::as_str) {
            return vec![Line::styled(format!("{INDENT}{refusal}"), theme.error)];
        }
        if self.name != "edit" {
            return Vec::new();
        }
        let params = self.parsed();
        let Some(edits) = params.get("edits").and_then(Value::as_array) else {
            return Vec::new();
        };
        let mut rows = Vec::new();
        for edit in edits {
            rows.extend(changed_rows(
                &decoded(edit.get("old_string_b64")),
                &decoded(edit.get("new_string_b64")),
            ));
        }
        folded(diff_lines(&rows, theme), theme)
    }
}

fn named(act: &str, subject: &str) -> String {
    if subject.is_empty() {
        act.to_string()
    } else {
        format!("{act} {subject}")
    }
}

fn argv(params: &Value) -> String {
    let Some(argv) = params.get("argv").and_then(Value::as_array) else {
        return String::new();
    };
    argv.iter()
        .filter_map(Value::as_str)
        .collect::<Vec<&str>>()
        .join(" ")
}

/// The member's view of a command: the server's walk program is named for what it does, a
/// `sh -c` wrapper is unwrapped, leading environment assignments are dropped, and whitespace
/// flattens to one line — a header row can hold no newline.
fn command_display(command: &str) -> String {
    if command.contains("UFO_WALK_ROOT") {
        return "list files".to_string();
    }
    let flat: Vec<&str> = command.split_whitespace().collect();
    let mut tokens = flat.as_slice();
    if let ["sh" | "bash" | "zsh", "-c", rest @ ..] = tokens {
        tokens = rest;
    }
    while let [first, rest @ ..] = tokens {
        if !is_env_assignment(first) || rest.is_empty() {
            break;
        }
        tokens = rest;
    }
    named("exec", &tokens.join(" "))
}

fn is_env_assignment(token: &str) -> bool {
    let Some((name, _)) = token.split_once('=') else {
        return false;
    };
    !name.is_empty()
        && name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
        && !name.chars().next().is_some_and(|ch| ch.is_ascii_digit())
}

fn field<'a>(params: &'a Value, key: &str) -> &'a str {
    params.get(key).and_then(Value::as_str).unwrap_or("")
}

fn exec_tail(reply: &[u8], theme: &Theme) -> Vec<Line<'static>> {
    let result: Value = serde_json::from_slice(reply).unwrap_or(Value::Null);
    let mut bytes = unbase64(field(&result, "stdout_b64"));
    bytes.extend(unbase64(field(&result, "stderr_b64")));
    let output = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = output.lines().collect();
    let hidden = lines.len().saturating_sub(RESULT_TAIL_LINES);
    let mut body = Vec::new();
    if hidden > 0 {
        body.push(fold(hidden, theme));
    }
    for line in &lines[hidden..] {
        body.push(Line::styled(format!("{INDENT}{line}"), theme.tool_output));
    }
    match result.get("exit_code").and_then(Value::as_i64) {
        Some(code) if code != 0 => {
            body.push(Line::styled(format!("{INDENT}exit {code}"), theme.error));
            body
        }
        _ => body,
    }
}

fn unbase64(text: &str) -> Vec<u8> {
    STANDARD.decode(text).unwrap_or_default()
}

fn decoded(value: Option<&Value>) -> String {
    let text = value.and_then(Value::as_str).unwrap_or_default();
    let bytes = URL_SAFE
        .decode(text)
        .or_else(|_| STANDARD.decode(text))
        .unwrap_or_default();
    String::from_utf8_lossy(&bytes).into_owned()
}

fn changed_rows(old: &str, new: &str) -> Vec<(ChangeTag, String)> {
    let diff = TextDiff::from_lines(old, new);
    let mut rows = Vec::new();
    for group in diff.grouped_ops(DIFF_CONTEXT_LINES) {
        for op in group {
            for change in diff.iter_changes(&op) {
                let text = change.value().trim_end_matches('\n').trim_end_matches('\r');
                rows.push((change.tag(), text.to_string()));
            }
        }
    }
    rows
}

fn diff_lines(rows: &[(ChangeTag, String)], theme: &Theme) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let mut at = 0;
    while at < rows.len() {
        if rows[at].0 == ChangeTag::Equal {
            lines.push(Line::styled(
                format!("{INDENT} {}", rows[at].1),
                theme.diff_context,
            ));
            at += 1;
            continue;
        }
        let removed = run_of(rows, at, ChangeTag::Delete);
        let added = run_of(rows, at + removed.len(), ChangeTag::Insert);
        at += removed.len() + added.len();
        if removed.len() == 1 && added.len() == 1 {
            let (old, new) = emphasized(&removed[0].1, &added[0].1, theme);
            lines.push(Line::from(old));
            lines.push(Line::from(new));
            continue;
        }
        for row in removed {
            lines.push(Line::styled(
                format!("{INDENT}-{}", row.1),
                theme.diff_removed,
            ));
        }
        for row in added {
            lines.push(Line::styled(
                format!("{INDENT}+{}", row.1),
                theme.diff_added,
            ));
        }
    }
    lines
}

fn run_of(rows: &[(ChangeTag, String)], from: usize, tag: ChangeTag) -> &[(ChangeTag, String)] {
    let end = rows[from..].iter().take_while(|row| row.0 == tag).count();
    &rows[from..from + end]
}

fn emphasized(old: &str, new: &str, theme: &Theme) -> (Vec<Span<'static>>, Vec<Span<'static>>) {
    let mut removed = vec![Span::styled(format!("{INDENT}-"), theme.diff_removed)];
    let mut added = vec![Span::styled(format!("{INDENT}+"), theme.diff_added)];
    for change in TextDiff::from_words(old, new).iter_all_changes() {
        let word = change.value().to_string();
        match change.tag() {
            ChangeTag::Delete => removed.push(Span::styled(
                word,
                theme.diff_removed.add_modifier(Modifier::REVERSED),
            )),
            ChangeTag::Insert => added.push(Span::styled(
                word,
                theme.diff_added.add_modifier(Modifier::REVERSED),
            )),
            ChangeTag::Equal => {
                removed.push(Span::styled(word.clone(), theme.diff_removed));
                added.push(Span::styled(word, theme.diff_added));
            }
        }
    }
    (removed, added)
}

fn folded(mut lines: Vec<Line<'static>>, theme: &Theme) -> Vec<Line<'static>> {
    if lines.len() <= BODY_MAX_LINES {
        return lines;
    }
    let hidden = lines.len() - (BODY_MAX_LINES - 1);
    lines.truncate(BODY_MAX_LINES - 1);
    lines.push(fold(hidden, theme));
    lines
}

fn fold(hidden: usize, theme: &Theme) -> Line<'static> {
    Line::styled(format!("{INDENT}… +{hidden} lines"), theme.muted)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme, Theme};

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::Plain, Scheme::Dark)
    }

    fn op(kind: &str, name: &str, arg: &str, params: &str) -> OpRequest {
        OpRequest {
            op_id: "op1".into(),
            kind: kind.into(),
            name: name.into(),
            timeout_s: 60,
            arg: arg.into(),
            params: params.into(),
        }
    }

    fn rendered(lines: &[Line<'static>]) -> Vec<String> {
        lines.iter().map(Line::to_string).collect()
    }

    fn encoded(text: &str) -> String {
        URL_SAFE.encode(text.as_bytes())
    }

    fn exec_reply(exit_code: i32, stdout: &str) -> String {
        format!(
            r#"{{"exit_code":{exit_code},"stdout_b64":"{}","stderr_b64":""}}"#,
            STANDARD.encode(stdout.as_bytes())
        )
    }

    fn reversed(line: &Line<'static>) -> Vec<String> {
        line.spans
            .iter()
            .filter(|span| span.style.add_modifier.contains(Modifier::REVERSED))
            .map(|span| span.content.to_string())
            .collect()
    }

    fn edit_params(path: &str, old: &str, new: &str) -> String {
        format!(
            r#"{{"path":"{path}","edits":[{{"old_string_b64":"{}","new_string_b64":"{}"}}]}}"#,
            encoded(old),
            encoded(new)
        )
    }

    #[test]
    fn header_prefers_the_agents_narration() {
        let theme = theme();
        let view = OpView::from_request(&op(
            OP_EXEC,
            "exec",
            "",
            r#"{"argv":["make","test"],"env":{}}"#,
        ));
        assert_eq!(
            view.header(Some("Running the test suite"), &theme)
                .to_string(),
            "⏺ Running the test suite"
        );
        assert_eq!(
            view.header(Some("  "), &theme).to_string(),
            "⏺ exec make test"
        );
    }

    #[test]
    fn command_display_hides_the_plumbing() {
        assert_eq!(
            command_display(r#"sh -c UFO_WALK_ROOT=/w export UFO_WALK_ROOT r='...'"#),
            "list files"
        );
        assert_eq!(
            command_display("sh -c FOO=1 BAR_2=x make test"),
            "exec make test"
        );
        assert_eq!(command_display("make\ntest"), "exec make test");
        assert_eq!(command_display("sh -c FOO=1"), "exec FOO=1");
        assert_eq!(command_display("a=b echo hi"), "exec echo hi");
        assert_eq!(command_display("ls 1=2"), "exec ls 1=2");
    }

    #[test]
    fn header_names_the_act_a_member_reads() {
        let theme = theme();
        let cases = [
            (
                op(OP_EXEC, "exec", "", r#"{"argv":["make","test"],"env":{}}"#),
                "⏺ exec make test",
            ),
            (op(OP_WRITE, "write", "/w/a.rs", ""), "⏺ write /w/a.rs"),
            (op(OP_READ, "read", "/w/a.rs", ""), "⏺ read /w/a.rs"),
            (
                op(OP_FILE, "edit", "", r#"{"path":"/w/a.rs","edits":[]}"#),
                "⏺ edit /w/a.rs",
            ),
            (
                op(OP_FILE, "read", "", r#"{"path":"/w/a.rs"}"#),
                "⏺ read /w/a.rs",
            ),
            (
                op(
                    OP_FILE,
                    "grep",
                    "",
                    r#"{"pattern":"fn run","path":"/w/src"}"#,
                ),
                "⏺ grep fn run /w/src",
            ),
            (
                op(OP_FILE, "grep", "", r#"{"pattern":"fn run"}"#),
                "⏺ grep fn run",
            ),
            (
                op(
                    OP_FILE,
                    "glob",
                    "",
                    r#"{"pattern":"**/*.rs","workspace":"/w"}"#,
                ),
                "⏺ glob **/*.rs",
            ),
            (
                op(OP_FILE, "changes", "", r#"{"workspace":"/w"}"#),
                "⏺ changes",
            ),
        ];
        for (request, expected) in cases {
            let view = OpView::from_request(&request);
            assert_eq!(view.header(None, &theme).to_string(), expected);
        }
    }

    #[test]
    fn edit_body_diffs_the_pair_with_word_emphasis() {
        let theme = theme();
        let params = edit_params(
            "/w/a.rs",
            "let total = one + two\n",
            "let total = one + three\n",
        );
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        let body = view.body(Ok(br#"{"replacements":1}"#), &theme);
        assert_eq!(
            rendered(&body),
            vec!["  -let total = one + two", "  +let total = one + three"]
        );
        assert_eq!(reversed(&body[0]), vec!["two"]);
        assert_eq!(reversed(&body[1]), vec!["three"]);
    }

    #[test]
    fn edit_body_carries_context_and_multi_line_changes() {
        let theme = theme();
        let params = edit_params(
            "/w/a.rs",
            "one\ntwo\nthree\nfour\nfive\nsix\n",
            "one\ntwo\nTHREE\nFOUR\nfive\nsix\n",
        );
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        assert_eq!(
            rendered(&view.body(Ok(b"{}"), &theme)),
            vec![
                "   one", "   two", "  -three", "  -four", "  +THREE", "  +FOUR", "   five",
                "   six",
            ]
        );
    }

    #[test]
    fn edit_body_folds_past_the_cap() {
        let theme = theme();
        let old: String = (1..=60).map(|n| format!("line {n}\n")).collect();
        let new: String = (1..=60).map(|n| format!("row {n}\n")).collect();
        let params = edit_params("/w/a.rs", &old, &new);
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        let body = rendered(&view.body(Ok(b"{}"), &theme));
        assert_eq!(body.len(), BODY_MAX_LINES);
        assert_eq!(body[BODY_MAX_LINES - 1], "  … +81 lines");
    }

    #[test]
    fn edit_body_states_a_refusal() {
        let theme = theme();
        let params = edit_params("/w/a.rs", "old\n", "new\n");
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        let body = view.body(Ok(br#"{"error":"old_string not found"}"#), &theme);
        assert_eq!(rendered(&body), vec!["  old_string not found"]);
    }

    #[test]
    fn exec_body_shows_the_tail_over_a_fold() {
        let theme = theme();
        let output: String = (1..=8).map(|n| format!("line {n}\n")).collect();
        let view = OpView::from_request(&op(OP_EXEC, "exec", "", r#"{"argv":["make"]}"#));
        let body = view.body(Ok(exec_reply(0, &output).as_bytes()), &theme);
        assert_eq!(
            rendered(&body),
            vec![
                "  … +3 lines",
                "  line 4",
                "  line 5",
                "  line 6",
                "  line 7",
                "  line 8",
            ]
        );
    }

    #[test]
    fn exec_body_states_a_nonzero_exit_and_stays_silent_when_empty() {
        let theme = theme();
        let view = OpView::from_request(&op(OP_EXEC, "exec", "", r#"{"argv":["make"]}"#));
        assert_eq!(
            rendered(&view.body(Ok(exec_reply(2, "boom\n").as_bytes()), &theme)),
            vec!["  boom", "  exit 2"]
        );
        assert!(view
            .body(Ok(exec_reply(0, "").as_bytes()), &theme)
            .is_empty());
    }

    #[test]
    fn read_and_write_bodies_stay_empty() {
        let theme = theme();
        let read = OpView::from_request(&op(OP_READ, "read", "/w/a.rs", ""));
        assert!(read.body(Ok(b"file bytes"), &theme).is_empty());
        let write = OpView::from_request(&op(OP_WRITE, "write", "/w/a.rs", ""));
        assert!(write.body(Ok(b""), &theme).is_empty());
    }

    #[test]
    fn failure_body_states_the_refusal() {
        let theme = theme();
        let view = OpView::from_request(&op(OP_WRITE, "write", "/tmp/x", ""));
        let body = view.body(Err("EIO: could not write /tmp/x"), &theme);
        assert_eq!(rendered(&body), vec!["  EIO: could not write /tmp/x"]);
    }

    #[test]
    fn malformed_params_name_the_act_alone() {
        let theme = theme();
        let cases = [
            (op(OP_EXEC, "exec", "", "{not json"), "⏺ exec"),
            (op(OP_EXEC, "exec", "", r#"{"argv":"make"}"#), "⏺ exec"),
            (op(OP_FILE, "edit", "", "{"), "⏺ edit"),
            (op(OP_FILE, "grep", "", "[]"), "⏺ grep"),
            (op("mystery", "mystery", "", ""), "⏺ mystery"),
        ];
        for (request, expected) in cases {
            let view = OpView::from_request(&request);
            assert_eq!(view.header(None, &theme).to_string(), expected);
        }
        let edit = OpView::from_request(&op(OP_FILE, "edit", "", "{"));
        assert!(edit.body(Ok(b"{}"), &theme).is_empty());
        assert!(edit.body(Ok(b"not json"), &theme).is_empty());
        let broken = edit_params("/w/a.rs", "old\n", "new\n").replace("old_string_b64", "old_b64");
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &broken));
        assert_eq!(rendered(&view.body(Ok(b"{}"), &theme)), vec!["  +new"]);
        let exec = OpView::from_request(&op(OP_EXEC, "exec", "", r#"{"argv":["make"]}"#));
        assert!(exec.body(Ok(b"not json"), &theme).is_empty());
        assert!(exec.body(Ok(br#"{"stdout_b64":"!!"}"#), &theme).is_empty());
    }

    #[test]
    fn standard_alphabet_edits_still_decode() {
        let theme = theme();
        let params = format!(
            r#"{{"path":"/w/a.rs","edits":[{{"old_string_b64":"{}","new_string_b64":"{}"}}]}}"#,
            STANDARD.encode(b"a?b\n"),
            STANDARD.encode(b"a?c\n")
        );
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        assert_eq!(
            rendered(&view.body(Ok(b"{}"), &theme)),
            vec!["  -a?b", "  +a?c"]
        );
    }
}
