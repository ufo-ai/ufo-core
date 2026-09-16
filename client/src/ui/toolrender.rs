use std::path::Path;
use std::time::{Duration, Instant};

use base64::engine::general_purpose::{STANDARD, URL_SAFE};
use base64::Engine as _;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use serde_json::Value;
use similar::{ChangeTag, TextDiff};

use crate::ops::{OP_EXEC, OP_FILE, OP_READ, OP_WRITE};
use crate::ui::status;
use crate::ui::theme::Theme;
use crate::wire::OpRequest;

const RESULT_TAIL_LINES: usize = 5;

const MARKER: &str = "⏺ ";
const INDENT: &str = "  ";
const COMMAND_PREFIX: &str = "$ ";
const SUFFIX_SEPARATOR: &str = " · ";
const CLOCK_FROM: Duration = Duration::from_secs(2);
const BODY_MAX_LINES: usize = 40;
const DIFF_CONTEXT_LINES: usize = 2;

/// How an op stands: still running, ended by its own exit, refused, stopped by the client's
/// deadline, or stopped by the turn ending over it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum OpState {
    Running,
    Done,
    Failed,
    TimedOut,
    Stopped,
}

/// One op this terminal ran for the turn, placed in the segment it started in after the record
/// step it started behind. The reply is rendered once, when it lands, into the bounded body the
/// row keeps; the bytes are not.
pub struct OpRow {
    pub op: OpRequest,
    pub segment: usize,
    pub after_step: usize,
    started: Instant,
    took: Option<Duration>,
    body: Vec<Line<'static>>,
    pub state: OpState,
}

impl OpRow {
    pub fn started(op: &OpRequest, segment: usize, after_step: usize) -> OpRow {
        OpRow::started_at(op, segment, after_step, Instant::now())
    }

    pub fn started_at(
        op: &OpRequest,
        segment: usize,
        after_step: usize,
        started: Instant,
    ) -> OpRow {
        OpRow {
            op: op.clone(),
            segment,
            after_step,
            started,
            took: None,
            body: Vec::new(),
            state: OpState::Running,
        }
    }

    pub fn finish(&mut self, reply: &Result<Vec<u8>, String>, theme: &Theme) {
        self.took = Some(self.started.elapsed());
        self.state = outcome(&self.op, reply);
        let reply = match reply {
            Ok(bytes) => Ok(bytes.as_slice()),
            Err(failure) => Err(failure.as_str()),
        };
        self.body = OpView::from_request(&self.op).body(reply, self.state, theme);
    }

    pub fn stop(&mut self) {
        if self.state == OpState::Running {
            self.took = Some(self.started.elapsed());
            self.state = OpState::Stopped;
        }
    }

    pub fn running(&self) -> bool {
        self.state == OpState::Running
    }

    /// The row: a dot that is the state, the agent's label or the act itself, a clock once the
    /// op has run two seconds, then the command under a label, then what the op answered.
    pub fn rows(
        &self,
        label: Option<&str>,
        cwd: &Path,
        phase: usize,
        now: Instant,
        theme: &Theme,
    ) -> Vec<Line<'static>> {
        let view = OpView::from_request(&self.op);
        let dot = match self.state {
            OpState::Running if phase.is_multiple_of(2) => theme.accent,
            OpState::Running => theme.muted,
            OpState::Done => theme.tool_title,
            OpState::Failed => theme.error,
            OpState::TimedOut | OpState::Stopped => theme.warning,
        };
        let said = label.map(str::trim).filter(|said| !said.is_empty());
        let title = said.map_or_else(|| view.title(cwd), str::to_string);
        let mut header = vec![
            Span::styled(MARKER, dot),
            Span::styled(title, theme.tool_title),
        ];
        if let Some(suffix) = self.suffix(now) {
            header.push(Span::styled(
                format!("{SUFFIX_SEPARATOR}{suffix}"),
                theme.muted,
            ));
        }
        let mut rows = vec![Line::from(header)];
        if said.is_some() {
            if let Some(command) = view.command_line(cwd) {
                rows.push(Line::styled(format!("{INDENT}{command}"), theme.muted));
            }
        }
        rows.extend(self.body.iter().cloned());
        rows
    }

    fn suffix(&self, now: Instant) -> Option<String> {
        match self.state {
            OpState::Running => {
                let ran = now.saturating_duration_since(self.started);
                (ran >= CLOCK_FROM).then(|| status::elapsed(ran))
            }
            OpState::Done | OpState::Failed => self
                .took
                .filter(|took| *took >= CLOCK_FROM)
                .map(status::elapsed),
            OpState::TimedOut => Some(format!("timed out after {}s", self.op.timeout_s)),
            OpState::Stopped => Some("stopped".to_string()),
        }
    }
}

fn outcome(op: &OpRequest, reply: &Result<Vec<u8>, String>) -> OpState {
    let Ok(bytes) = reply else {
        return OpState::Failed;
    };
    let result: Value = serde_json::from_slice(bytes).unwrap_or(Value::Null);
    match op.kind.as_str() {
        OP_EXEC if result.get("timed_out").and_then(Value::as_bool) == Some(true) => {
            OpState::TimedOut
        }
        OP_EXEC => match result.get("exit_code").and_then(Value::as_i64) {
            Some(0) | None => OpState::Done,
            Some(_) => OpState::Failed,
        },
        OP_FILE if result.get("error").is_some() => OpState::Failed,
        _ => OpState::Done,
    }
}

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

    /// Whether the op is a step the member reads: a command the model wrote — the carrier marks
    /// it with `safety_argv`, the one field that says whose text the command is — or a file op
    /// issued for a tool call, which names the call it serves. Everything else the runtime issues
    /// for itself: probes of a detached command, log reads, file walks, runtime writes, skill
    /// archives, and the change scan it runs once the turn has ended.
    pub fn is_step(op: &OpRequest) -> bool {
        match op.kind.as_str() {
            OP_FILE => !op.call_id.is_empty(),
            OP_EXEC => OpView::from_request(op).model_command().is_some(),
            _ => false,
        }
    }

    /// The act, as the header states it without a label: the command after `$`, or the file op
    /// and its path relative to the workspace.
    pub fn title(&self, cwd: &Path) -> String {
        let params = self.parsed();
        match self.kind.as_str() {
            OP_EXEC => self
                .command_line(cwd)
                .unwrap_or_else(|| named("exec", &argv(&params))),
            OP_WRITE | OP_READ => named(&self.kind, &relative(&self.arg, cwd)),
            OP_FILE => match self.name.as_str() {
                "grep" => named(
                    "grep",
                    format!(
                        "{} {}",
                        field(&params, "pattern"),
                        relative(field(&params, "path"), cwd)
                    )
                    .trim(),
                ),
                "glob" => named("glob", field(&params, "pattern")),
                "changes" => "changes".to_string(),
                "edit" => {
                    let path = relative(field(&params, "path"), cwd);
                    match self.edit_stats(&params) {
                        Some((0, 0)) | None => named("edit", &path),
                        Some((added, removed)) => {
                            format!("{} +{added} −{removed}", named("edit", &path))
                        }
                    }
                }
                name => named(name, &relative(field(&params, "path"), cwd)),
            },
            other => other.to_string(),
        }
    }

    /// The model's command as it wrote it, after `$`: its first line, and without the `cd` into
    /// the workspace the terminal already stands in.
    pub fn command_line(&self, cwd: &Path) -> Option<String> {
        let command = self.model_command()?;
        let (first, rest) = command.split_once('\n').unwrap_or((&command, ""));
        let first = without_workspace_cd(first.trim(), cwd);
        let shown = if rest.trim().is_empty() {
            first.to_string()
        } else {
            format!("{first}…")
        };
        Some(format!("{COMMAND_PREFIX}{shown}"))
    }

    fn model_command(&self) -> Option<String> {
        let params = self.parsed();
        let safety = params.get("safety_argv")?.as_array()?;
        let command = safety.last()?.as_str()?;
        (!command.trim().is_empty()).then(|| command.to_string())
    }

    pub fn body(
        &self,
        reply: Result<&[u8], &str>,
        state: OpState,
        theme: &Theme,
    ) -> Vec<Line<'static>> {
        let reply = match reply {
            Ok(reply) => reply,
            Err(failure) => return vec![Line::styled(format!("{INDENT}{failure}"), theme.error)],
        };
        match self.kind.as_str() {
            OP_EXEC => exec_tail(reply, state, theme),
            OP_FILE => self.file_result(reply, theme),
            _ => Vec::new(),
        }
    }

    fn parsed(&self) -> Value {
        serde_json::from_str(&self.params).unwrap_or(Value::Null)
    }

    fn edit_stats(&self, params: &Value) -> Option<(usize, usize)> {
        let edits = params.get("edits")?.as_array()?;
        let mut added = 0;
        let mut removed = 0;
        for edit in edits {
            for (tag, _) in changed_rows(
                &decoded(edit.get("old_string_b64")),
                &decoded(edit.get("new_string_b64")),
            ) {
                match tag {
                    ChangeTag::Insert => added += 1,
                    ChangeTag::Delete => removed += 1,
                    ChangeTag::Equal => {}
                }
            }
        }
        Some((added, removed))
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

fn relative(path: &str, cwd: &Path) -> String {
    Path::new(path)
        .strip_prefix(cwd)
        .ok()
        .filter(|rest| !rest.as_os_str().is_empty())
        .map_or_else(
            || path.to_string(),
            |rest| rest.to_string_lossy().into_owned(),
        )
}

/// `cd <dir> && rest` with `<dir>` the workspace the terminal stands in reads as `rest`; a `cd`
/// anywhere else is the model's own and stays.
fn without_workspace_cd<'a>(command: &'a str, cwd: &Path) -> &'a str {
    let Some(after_cd) = command.strip_prefix("cd ") else {
        return command;
    };
    let Some((dir, rest)) = after_cd.split_once(" && ") else {
        return command;
    };
    let dir = dir.trim().trim_matches(['"', '\'']);
    if !cwd.as_os_str().is_empty() && Path::new(dir) == cwd {
        rest.trim_start()
    } else {
        command
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

fn field<'a>(params: &'a Value, key: &str) -> &'a str {
    params.get(key).and_then(Value::as_str).unwrap_or("")
}

fn exec_tail(reply: &[u8], state: OpState, theme: &Theme) -> Vec<Line<'static>> {
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
    if state == OpState::TimedOut {
        return body;
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

    fn cwd() -> &'static Path {
        Path::new("/w")
    }

    fn op(kind: &str, name: &str, arg: &str, params: &str) -> OpRequest {
        OpRequest {
            op_id: "op1".into(),
            kind: kind.into(),
            name: name.into(),
            timeout_s: 60,
            arg: arg.into(),
            params: params.into(),
            call_id: String::new(),
        }
    }

    fn exec(command: &str) -> OpRequest {
        op(
            OP_EXEC,
            "exec",
            "",
            &serde_json::json!({
                "argv": ["ufo", "run", "--task", "/t", "--", "bash", "-lc", command],
                "env": {},
                "safety_argv": ["bash", "-lc", command],
            })
            .to_string(),
        )
    }

    fn rendered(lines: &[Line<'static>]) -> Vec<String> {
        lines.iter().map(Line::to_string).collect()
    }

    fn encoded(text: &str) -> String {
        URL_SAFE.encode(text.as_bytes())
    }

    fn exec_reply(exit_code: i32, stdout: &str) -> Result<Vec<u8>, String> {
        Ok(format!(
            r#"{{"exit_code":{exit_code},"timed_out":false,"stdout_b64":"{}","stderr_b64":""}}"#,
            STANDARD.encode(stdout.as_bytes())
        )
        .into_bytes())
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

    fn rows(row: &OpRow, label: Option<&str>) -> Vec<String> {
        rendered(&row.rows(label, cwd(), 0, Instant::now(), &theme()))
    }

    #[test]
    fn a_step_is_the_models_own_command_or_one_of_its_file_tools() {
        assert!(OpView::is_step(&exec("make test")));
        assert!(!OpView::is_step(&op(
            OP_EXEC,
            "exec",
            "",
            r#"{"argv":["sh","-c","pid=$(cat \"$1.pid\") || exit 1","sh","/t"],"env":{}}"#
        )));
        let mut edit = op(OP_FILE, "edit", "", r#"{"path":"/w/a.rs","edits":[]}"#);
        assert!(
            !OpView::is_step(&edit),
            "a file op the runtime issued for itself, such as the change scan after a turn, names no call"
        );
        edit.call_id = "c1".to_string();
        assert!(OpView::is_step(&edit));
        assert!(!OpView::is_step(&op(
            OP_WRITE,
            "write",
            "/w/runs/x/tool-output/1",
            ""
        )));
        assert!(!OpView::is_step(&op(OP_READ, "read", "/w/a.png", "")));
        assert!(!OpView::is_step(&op("skills", "", "", "{}")));
    }

    #[test]
    fn the_command_line_is_the_models_text_less_the_cd_into_the_workspace() {
        let view = OpView::from_request(&exec("cd /w && make test"));
        assert_eq!(view.command_line(cwd()).as_deref(), Some("$ make test"));
        assert_eq!(
            view.command_line(Path::new("/elsewhere")).as_deref(),
            Some("$ cd /w && make test")
        );
        let view = OpView::from_request(&exec("cd /w/sub && make test"));
        assert_eq!(
            view.command_line(cwd()).as_deref(),
            Some("$ cd /w/sub && make test")
        );
        let view = OpView::from_request(&exec("make\ntest"));
        assert_eq!(view.command_line(cwd()).as_deref(), Some("$ make…"));
        let view = OpView::from_request(&exec("pid=$(cat \"$1.pid\") || exit 1"));
        assert_eq!(
            view.command_line(cwd()).as_deref(),
            Some("$ pid=$(cat \"$1.pid\") || exit 1"),
            "the model's text is shown as written, never taken apart"
        );
        let view = OpView::from_request(&op(
            OP_EXEC,
            "exec",
            "",
            r#"{"argv":["sh","-c","ls"],"env":{}}"#,
        ));
        assert_eq!(view.command_line(cwd()), None);
        assert_eq!(view.title(cwd()), "exec sh -c ls");
    }

    #[test]
    fn a_labelled_row_states_the_label_over_the_command_and_its_output() {
        let mut row = OpRow::started(&exec("wc -l"), 0, 0);
        row.finish(&exec_reply(0, "42\n"), &theme());
        assert_eq!(
            rows(&row, Some("counting the rows")),
            ["⏺ counting the rows", "  $ wc -l", "  42"]
        );
        assert_eq!(rows(&row, None), ["⏺ $ wc -l", "  42"]);
        assert_eq!(
            rows(&row, Some("  ")),
            ["⏺ $ wc -l", "  42"],
            "a blank label is no label"
        );
    }

    #[test]
    fn a_running_row_shows_its_clock_once_it_has_run_two_seconds() {
        let now = Instant::now();
        let young = OpRow::started_at(&exec("make"), 0, 0, now - Duration::from_secs(1));
        assert_eq!(rows(&young, None), ["⏺ $ make"]);
        let old = OpRow::started_at(&exec("make"), 0, 0, now - Duration::from_secs(12));
        assert_eq!(
            rendered(&old.rows(None, cwd(), 0, now, &theme())),
            ["⏺ $ make · 12s"]
        );
        let theme = theme();
        let lit = old.rows(None, cwd(), 0, now, &theme);
        let dim = old.rows(None, cwd(), 1, now, &theme);
        assert_eq!(lit[0].spans[0].style, theme.accent);
        assert_eq!(dim[0].spans[0].style, theme.muted);
    }

    #[test]
    fn a_finished_row_keeps_its_clock_only_when_it_ran_long() {
        let now = Instant::now();
        let mut quick = OpRow::started_at(&exec("make"), 0, 0, now);
        quick.finish(&exec_reply(0, ""), &theme());
        assert_eq!(rows(&quick, None), ["⏺ $ make"]);
        let mut slow = OpRow::started_at(&exec("make"), 0, 0, now - Duration::from_secs(13));
        slow.finish(&exec_reply(0, "ok\n"), &theme());
        assert_eq!(rows(&slow, None), ["⏺ $ make · 13s", "  ok"]);
    }

    #[test]
    fn a_failed_row_is_red_and_keeps_its_exit_line() {
        let theme = theme();
        let mut row = OpRow::started(&exec("make"), 0, 0);
        row.finish(&exec_reply(2, "boom\n"), &theme);
        let lines = row.rows(None, cwd(), 0, Instant::now(), &theme);
        assert_eq!(rendered(&lines), ["⏺ $ make", "  boom", "  exit 2"]);
        assert_eq!(lines[0].spans[0].style, theme.error);
        assert_eq!(lines[2].style, theme.error);
    }

    #[test]
    fn a_timed_out_row_says_so_instead_of_the_signal_it_died_of() {
        let theme = theme();
        let mut row = OpRow::started(&exec("git push"), 0, 0);
        row.finish(
            &Ok(br#"{"exit_code":137,"timed_out":true,"stdout_b64":"","stderr_b64":""}"#.to_vec()),
            &theme,
        );
        let lines = row.rows(None, cwd(), 0, Instant::now(), &theme);
        assert_eq!(rendered(&lines), ["⏺ $ git push · timed out after 60s"]);
        assert_eq!(lines[0].spans[0].style, theme.warning);
    }

    #[test]
    fn a_stopped_row_says_so() {
        let theme = theme();
        let mut row = OpRow::started(&exec("make"), 0, 0);
        row.stop();
        let lines = row.rows(None, cwd(), 0, Instant::now(), &theme);
        assert_eq!(rendered(&lines), ["⏺ $ make · stopped"]);
        assert_eq!(lines[0].spans[0].style, theme.warning);
        let mut done = OpRow::started(&exec("make"), 0, 0);
        done.finish(&exec_reply(0, ""), &theme);
        done.stop();
        assert_eq!(
            rows(&done, None),
            ["⏺ $ make"],
            "a finished op is not stopped"
        );
    }

    #[test]
    fn a_refused_op_states_the_refusal_in_red() {
        let theme = theme();
        let mut row = OpRow::started(
            &op(
                OP_FILE,
                "edit",
                "",
                &edit_params("/w/a.rs", "old\n", "new\n"),
            ),
            0,
            0,
        );
        row.finish(&Err("EIO: could not write /w/a.rs".to_string()), &theme);
        let lines = row.rows(None, cwd(), 0, Instant::now(), &theme);
        assert_eq!(
            rendered(&lines),
            ["⏺ edit a.rs +1 −1", "  EIO: could not write /w/a.rs"]
        );
        assert_eq!(lines[0].spans[0].style, theme.error);
    }

    #[test]
    fn file_ops_name_the_act_and_the_path_relative_to_the_workspace() {
        let cases = [
            (
                op(OP_FILE, "read", "", r#"{"path":"/w/src/a.rs"}"#),
                "read src/a.rs",
            ),
            (
                op(OP_FILE, "read", "", r#"{"path":"/elsewhere/a.rs"}"#),
                "read /elsewhere/a.rs",
            ),
            (
                op(
                    OP_FILE,
                    "grep",
                    "",
                    r#"{"pattern":"fn run","path":"/w/src"}"#,
                ),
                "grep fn run src",
            ),
            (
                op(OP_FILE, "grep", "", r#"{"pattern":"fn run"}"#),
                "grep fn run",
            ),
            (
                op(
                    OP_FILE,
                    "glob",
                    "",
                    r#"{"pattern":"**/*.rs","workspace":"/w"}"#,
                ),
                "glob **/*.rs",
            ),
            (
                op(OP_FILE, "changes", "", r#"{"workspace":"/w"}"#),
                "changes",
            ),
            (
                op(OP_FILE, "edit", "", r#"{"path":"/w/a.rs","edits":[]}"#),
                "edit a.rs",
            ),
            (op(OP_WRITE, "write", "/w/a.rs", ""), "write a.rs"),
        ];
        for (request, expected) in cases {
            assert_eq!(OpView::from_request(&request).title(cwd()), expected);
        }
    }

    #[test]
    fn an_edit_header_counts_the_lines_it_changed() {
        let params = edit_params("/w/a.rs", "one\ntwo\nthree\n", "one\nTWO\n");
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        assert_eq!(view.title(cwd()), "edit a.rs +1 −2");
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
        let body = view.body(Ok(br#"{"replacements":1}"#), OpState::Done, &theme);
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
            rendered(&view.body(Ok(b"{}"), OpState::Done, &theme)),
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
        let body = rendered(&view.body(Ok(b"{}"), OpState::Done, &theme));
        assert_eq!(body.len(), BODY_MAX_LINES);
        assert_eq!(body[BODY_MAX_LINES - 1], "  … +81 lines");
    }

    #[test]
    fn edit_body_states_a_refusal() {
        let theme = theme();
        let params = edit_params("/w/a.rs", "old\n", "new\n");
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &params));
        let body = view.body(
            Ok(br#"{"error":"old_string not found"}"#),
            OpState::Failed,
            &theme,
        );
        assert_eq!(rendered(&body), vec!["  old_string not found"]);
    }

    #[test]
    fn exec_body_shows_the_tail_over_a_fold() {
        let theme = theme();
        let output: String = (1..=8).map(|n| format!("line {n}\n")).collect();
        let view = OpView::from_request(&exec("make"));
        let body = view.body(
            Ok(exec_reply(0, &output).expect("a reply").as_slice()),
            OpState::Done,
            &theme,
        );
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
    fn exec_body_stays_silent_when_empty() {
        let theme = theme();
        let view = OpView::from_request(&exec("make"));
        assert!(view
            .body(
                Ok(exec_reply(0, "").expect("a reply").as_slice()),
                OpState::Done,
                &theme
            )
            .is_empty());
    }

    #[test]
    fn read_and_write_bodies_stay_empty() {
        let theme = theme();
        let read = OpView::from_request(&op(OP_READ, "read", "/w/a.rs", ""));
        assert!(read
            .body(Ok(b"file bytes"), OpState::Done, &theme)
            .is_empty());
        let write = OpView::from_request(&op(OP_WRITE, "write", "/w/a.rs", ""));
        assert!(write.body(Ok(b""), OpState::Done, &theme).is_empty());
    }

    #[test]
    fn malformed_params_name_the_act_alone() {
        let cases = [
            (op(OP_EXEC, "exec", "", "{not json"), "exec"),
            (op(OP_EXEC, "exec", "", r#"{"argv":"make"}"#), "exec"),
            (op(OP_FILE, "edit", "", "{"), "edit"),
            (op(OP_FILE, "grep", "", "[]"), "grep"),
            (op("mystery", "mystery", "", ""), "mystery"),
        ];
        for (request, expected) in cases {
            assert_eq!(OpView::from_request(&request).title(cwd()), expected);
        }
        let theme = theme();
        let edit = OpView::from_request(&op(OP_FILE, "edit", "", "{"));
        assert!(edit.body(Ok(b"{}"), OpState::Done, &theme).is_empty());
        assert!(edit.body(Ok(b"not json"), OpState::Done, &theme).is_empty());
        let broken = edit_params("/w/a.rs", "old\n", "new\n").replace("old_string_b64", "old_b64");
        let view = OpView::from_request(&op(OP_FILE, "edit", "", &broken));
        assert_eq!(
            rendered(&view.body(Ok(b"{}"), OpState::Done, &theme)),
            vec!["  +new"]
        );
        let exec = OpView::from_request(&exec("make"));
        assert!(exec.body(Ok(b"not json"), OpState::Done, &theme).is_empty());
        assert!(exec
            .body(Ok(br#"{"stdout_b64":"!!"}"#), OpState::Done, &theme)
            .is_empty());
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
            rendered(&view.body(Ok(b"{}"), OpState::Done, &theme)),
            vec!["  -a?b", "  +a?c"]
        );
    }
}
