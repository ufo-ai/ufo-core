//! The turn record's fold, the reference's twin: every `frame` directive the wire carries decodes
//! to the frame it names and folds into the record `record.rs` renders from core's models. The
//! conformance fixture core writes holds this fold to the reference one, case by case.

use std::time::{SystemTime, UNIX_EPOCH};

use serde::Deserialize;

use crate::record::{
    ActivityEvent, Meter, SourceRef, Step, SubagentRun, TerminalFrame, TurnEnd, TurnRecord,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct RunFrame {
    pub turn_id: String,
    pub parent_turn_id: String,
    pub conversation_id: String,
    pub profile: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub activity: String,
    #[serde(default)]
    pub status: String,
}

impl RunFrame {
    pub fn label(&self) -> &str {
        if self.name.is_empty() {
            &self.profile
        } else {
            &self.name
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
#[allow(clippy::large_enum_variant)]
pub enum Frame {
    Message { text: String },
    Activity { text: String, call_id: String },
    Sources { items: Vec<SourceRef> },
    SubagentActivity(RunFrame),
    Reply { id: String, text: String },
    Comment { id: String, text: String },
    Absorbed { arrivals: Vec<String> },
    Resumed { attempt: String },
    Cost { tokens: i64, cost_micro_usd: i64 },
    Terminal(TerminalFrame),
    Parked { message: String },
}

#[derive(Deserialize)]
struct Text {
    text: String,
}

#[derive(Deserialize)]
struct Labelled {
    text: String,
    #[serde(default)]
    call_id: String,
}

#[derive(Deserialize)]
struct Items {
    items: Vec<SourceRef>,
}

#[derive(Deserialize)]
struct Said {
    id: String,
    #[serde(default)]
    text: String,
}

#[derive(Deserialize)]
struct Arrivals {
    arrivals: Vec<String>,
}

#[derive(Deserialize)]
struct Attempt {
    #[serde(default)]
    attempt: String,
}

#[derive(Deserialize)]
struct Cost {
    tokens: i64,
    cost_micro_usd: i64,
}

#[derive(Deserialize)]
struct Parked {
    message: String,
}

impl Frame {
    /// The frame an event and its JSON name, or None for an event the record does not fold or a
    /// payload without the fields its kind requires.
    pub fn decode(event: &str, data: &str) -> Option<Frame> {
        match event {
            "message" => serde_json::from_str::<Text>(data)
                .ok()
                .map(|held| Frame::Message { text: held.text }),
            "activity" => serde_json::from_str::<Labelled>(data)
                .ok()
                .map(|held| Frame::Activity {
                    text: held.text,
                    call_id: held.call_id,
                }),
            "sources" => serde_json::from_str::<Items>(data)
                .ok()
                .map(|held| Frame::Sources { items: held.items }),
            "subagent_activity" => serde_json::from_str::<RunFrame>(data)
                .ok()
                .map(Frame::SubagentActivity),
            "reply" => serde_json::from_str::<Said>(data)
                .ok()
                .map(|held| Frame::Reply {
                    id: held.id,
                    text: held.text,
                }),
            "comment" => serde_json::from_str::<Said>(data)
                .ok()
                .map(|held| Frame::Comment {
                    id: held.id,
                    text: held.text,
                }),
            "absorbed" => serde_json::from_str::<Arrivals>(data)
                .ok()
                .map(|held| Frame::Absorbed {
                    arrivals: held.arrivals,
                }),
            "resumed" => serde_json::from_str::<Attempt>(data)
                .ok()
                .map(|held| Frame::Resumed {
                    attempt: held.attempt,
                }),
            "cost" => serde_json::from_str::<Cost>(data)
                .ok()
                .map(|held| Frame::Cost {
                    tokens: held.tokens,
                    cost_micro_usd: held.cost_micro_usd,
                }),
            "terminal" => serde_json::from_str::<TerminalFrame>(data)
                .ok()
                .map(Frame::Terminal),
            "parked" => serde_json::from_str::<Parked>(data)
                .ok()
                .map(|held| Frame::Parked {
                    message: held.message,
                }),
            _ => None,
        }
    }
}

pub fn empty(id: Option<String>) -> TurnRecord {
    TurnRecord {
        id,
        steps: Vec::new(),
        runs: Vec::new(),
        meter: None,
        end: None,
    }
}

fn source_key(source: &SourceRef) -> &str {
    match source.url.as_deref() {
        Some(url) if !url.is_empty() => url,
        _ => source.r#ref.as_deref().unwrap_or(""),
    }
}

fn consulted(held: &mut Vec<SourceRef>, items: &[SourceRef]) {
    for item in items {
        if !held.iter().any(|seen| source_key(seen) == source_key(item)) {
            held.push(item.clone());
        }
    }
}

pub fn close_open(steps: &mut [Step]) {
    for step in steps {
        match step {
            Step::Text { open, .. } | Step::Tool { open, .. } => *open = false,
            _ => {}
        }
    }
}

/// The text step the next words extend: the newest step once the replies delivered beside it are
/// looked past, when that step is text still open.
fn open_text(steps: &[Step]) -> Option<usize> {
    for (index, step) in steps.iter().enumerate().rev() {
        match step {
            Step::Reply { .. } | Step::Comment { .. } => continue,
            Step::Text { open: true, .. } => return Some(index),
            _ => return None,
        }
    }
    None
}

fn opening(record: &mut TurnRecord, step: Step) {
    close_open(&mut record.steps);
    record.steps.push(step);
}

fn holds_run(runs: &[SubagentRun], turn_id: &str) -> bool {
    runs.iter()
        .any(|run| run.turn_id.as_deref() == Some(turn_id) || holds_run(&run.subagents, turn_id))
}

pub fn find_run<'a>(runs: &'a [SubagentRun], turn_id: &str) -> Option<&'a SubagentRun> {
    for run in runs {
        if run.turn_id.as_deref() == Some(turn_id) {
            return Some(run);
        }
        if let Some(nested) = find_run(&run.subagents, turn_id) {
            return Some(nested);
        }
    }
    None
}

fn advance_run(run: &mut SubagentRun, frame: &RunFrame) {
    if !frame.activity.is_empty() {
        run.events.push(ActivityEvent {
            kind: "activity".to_string(),
            text: frame.activity.clone(),
        });
        run.current = Some(frame.activity.clone());
    }
    if !frame.status.is_empty() {
        run.running = false;
        run.current = None;
    }
}

fn apply_run_frame(runs: &mut Vec<SubagentRun>, frame: &RunFrame, owner: Option<&str>) {
    if owner != Some(frame.parent_turn_id.as_str()) && holds_run(runs, &frame.parent_turn_id) {
        for run in runs.iter_mut() {
            let parent = run.turn_id.as_deref() == Some(frame.parent_turn_id.as_str())
                || holds_run(&run.subagents, &frame.parent_turn_id);
            if parent {
                let under = run.turn_id.clone();
                apply_run_frame(&mut run.subagents, frame, under.as_deref());
            }
        }
        return;
    }
    if let Some(run) = runs
        .iter_mut()
        .find(|run| run.turn_id.as_deref() == Some(frame.turn_id.as_str()))
    {
        advance_run(run, frame);
        return;
    }
    let mut fresh = SubagentRun {
        profile: frame.profile.clone(),
        name: Some(frame.name.clone()),
        conversation_id: frame.conversation_id.clone(),
        events: Vec::new(),
        output: String::new(),
        subagents: Vec::new(),
        running: true,
        turn_id: Some(frame.turn_id.clone()),
        parent_turn_id: Some(frame.parent_turn_id.clone()),
        current: None,
    };
    advance_run(&mut fresh, frame);
    runs.push(fresh);
}

/// The record after one frame, or false when the frame is a fault the record cannot hold — a frame
/// after the turn's end, a reply with no words — and the record stands as it was. A run naming a
/// parent the record does not hold is kept at the root and reported the same way.
pub fn fold(record: &mut TurnRecord, frame: &Frame, at: &str) -> bool {
    if record.end.is_some() {
        return false;
    }
    match frame {
        Frame::Message { text } => {
            if text.is_empty() {
                return true;
            }
            match open_text(&record.steps) {
                Some(index) => {
                    if let Step::Text { text: held, .. } = &mut record.steps[index] {
                        held.push_str(text);
                    }
                }
                None => opening(
                    record,
                    Step::Text {
                        text: text.clone(),
                        open: true,
                    },
                ),
            }
            true
        }
        Frame::Activity { text, call_id } => {
            if text.is_empty() {
                return true;
            }
            opening(
                record,
                Step::Tool {
                    label: Some(text.clone()),
                    call_id: call_id.clone(),
                    sources: Vec::new(),
                    open: true,
                },
            );
            true
        }
        Frame::Sources { items } => {
            if let Some(Step::Tool {
                sources,
                open: true,
                ..
            }) = record.steps.last_mut()
            {
                consulted(sources, items);
                return true;
            }
            let mut sources = Vec::new();
            consulted(&mut sources, items);
            opening(
                record,
                Step::Tool {
                    label: None,
                    call_id: String::new(),
                    sources,
                    open: true,
                },
            );
            true
        }
        Frame::SubagentActivity(run) => {
            let orphan = record.id.as_deref() != Some(run.parent_turn_id.as_str())
                && !holds_run(&record.runs, &run.parent_turn_id);
            let owner = record.id.clone();
            apply_run_frame(&mut record.runs, run, owner.as_deref());
            !orphan
        }
        Frame::Reply { id, text } | Frame::Comment { id, text } => {
            if text.is_empty() {
                return false;
            }
            let held = record.steps.iter().any(|step| match step {
                Step::Reply { id: seen, .. } | Step::Comment { id: seen, .. } => seen == id,
                _ => false,
            });
            if held {
                return true;
            }
            record.steps.push(match frame {
                Frame::Comment { .. } => Step::Comment {
                    id: id.clone(),
                    text: text.clone(),
                },
                _ => Step::Reply {
                    id: id.clone(),
                    text: text.clone(),
                },
            });
            true
        }
        Frame::Absorbed { arrivals } => {
            let fresh: Vec<String> = arrivals
                .iter()
                .filter(|arrival| {
                    !record.steps.iter().any(|step| match step {
                        Step::Drain { arrivals: held } => held.contains(arrival),
                        _ => false,
                    })
                })
                .cloned()
                .collect();
            if fresh.is_empty() {
                return true;
            }
            opening(record, Step::Drain { arrivals: fresh });
            true
        }
        Frame::Resumed { attempt } => {
            opening(
                record,
                Step::Resumed {
                    attempt: attempt.clone(),
                },
            );
            true
        }
        Frame::Cost {
            tokens,
            cost_micro_usd,
        } => {
            record.meter = Some(Meter {
                tokens: *tokens,
                cost_micro_usd: *cost_micro_usd,
            });
            true
        }
        Frame::Terminal(terminal) => {
            close_open(&mut record.steps);
            record.end = Some(TurnEnd::Terminal {
                frame: terminal.clone(),
                at: at.to_string(),
            });
            true
        }
        Frame::Parked { message } => {
            close_open(&mut record.steps);
            record.end = Some(TurnEnd::Parked {
                message: message.clone(),
            });
            true
        }
    }
}

/// The step the turn is on: its newest, the replies delivered beside it looked past.
pub fn current_step(record: &TurnRecord) -> Option<&Step> {
    record
        .steps
        .iter()
        .rev()
        .find(|step| !matches!(step, Step::Reply { .. } | Step::Comment { .. }))
}

pub fn runs_that_worked(runs: &[SubagentRun]) -> usize {
    runs.iter()
        .map(|run| usize::from(!run.events.is_empty()) + runs_that_worked(&run.subagents))
        .sum()
}

/// How many steps a settled turn took, as a log states it at the turn's end: every labelled call,
/// every passage of words a call followed, and every run that stated a step of its own — the
/// answer's own words are not a step.
pub fn step_count(record: &TurnRecord) -> usize {
    let mut steps = 0;
    let mut words = 0;
    for step in &record.steps {
        match step {
            Step::Text { .. } => words += 1,
            Step::Tool { label: Some(_), .. } => {
                steps += words + 1;
                words = 0;
            }
            _ => {}
        }
    }
    steps + runs_that_worked(&record.runs)
}

/// Now, as the record's end stamps it: an RFC 3339 instant in UTC, computed off the clock alone
/// since the client carries no calendar crate.
pub fn utc_now_rfc3339() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|held| held.as_secs() as i64)
        .unwrap_or(0);
    let days = seconds.div_euclid(86_400);
    let rest = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let of_era = shifted.rem_euclid(146_097);
    let year_of_era = (of_era - of_era / 1460 + of_era / 36_524 - of_era / 146_096) / 365;
    let day_of_year = of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_index + 2) / 5 + 1) as u32;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    } as u32;
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    const FOLD_FIXTURE: &str =
        include_str!("../../extensions/web/frontend/tests/fixtures/fold.json");
    const RECORD_FIXTURE: &str =
        include_str!("../../extensions/web/frontend/tests/fixtures/record.json");
    const TURN_ID: &str = "33333333-3333-4333-8333-333333333333";

    #[derive(Deserialize)]
    struct Row {
        event: String,
        data: String,
    }

    #[derive(Deserialize)]
    struct Case {
        name: String,
        frames: Vec<Row>,
        record: Value,
    }

    /// The shape both folds are compared in: no null and no absent field differ, since the
    /// reference dumps without its nulls.
    fn canonical(value: Value) -> Value {
        match value {
            Value::Array(items) => Value::Array(items.into_iter().map(canonical).collect()),
            Value::Object(fields) => Value::Object(
                fields
                    .into_iter()
                    .filter(|(_, held)| !held.is_null())
                    .map(|(key, held)| (key, canonical(held)))
                    .collect(),
            ),
            other => other,
        }
    }

    #[test]
    fn replays_every_case_of_the_reference_fold() {
        let cases: Vec<Case> = serde_json::from_str(FOLD_FIXTURE).expect("the fixture parses");
        assert!(cases.len() >= 5, "the fixture holds the reference cases");
        for case in cases {
            let at = case
                .record
                .pointer("/end/at")
                .and_then(Value::as_str)
                .unwrap_or("2026-09-15T12:00:00Z")
                .to_string();
            let mut record = empty(Some(TURN_ID.to_string()));
            for row in &case.frames {
                let frame = Frame::decode(&row.event, &row.data)
                    .unwrap_or_else(|| panic!("{}: {} did not decode", case.name, row.event));
                assert!(
                    fold(&mut record, &frame, &at),
                    "{}: a fault on {}",
                    case.name,
                    row.event
                );
            }
            let folded = canonical(serde_json::to_value(&record).expect("the record serializes"));
            assert_eq!(folded, canonical(case.record), "{}", case.name);
        }
    }

    #[test]
    fn the_record_fixture_round_trips_through_the_rendered_types() {
        let held: Value = serde_json::from_str(RECORD_FIXTURE).expect("the fixture parses");
        let record: TurnRecord = serde_json::from_str(RECORD_FIXTURE).expect("the record parses");
        let again = serde_json::to_value(&record).expect("the record serializes");
        assert_eq!(canonical(again), canonical(held));
        assert_eq!(record.steps.len(), 8);
        assert_eq!(step_count(&record), 3);
    }

    #[test]
    fn a_fault_leaves_the_record_standing() {
        let mut record = empty(Some(TURN_ID.to_string()));
        let done = Frame::decode("terminal", r#"{"status":"done","text":"Done."}"#).unwrap();
        assert!(fold(&mut record, &done, "2026-09-15T12:00:00Z"));
        let before = record.clone();
        assert!(!fold(
            &mut record,
            &Frame::Message {
                text: "more".into()
            },
            ""
        ));
        assert_eq!(record, before);
        let mut fresh = empty(None);
        assert!(!fold(
            &mut fresh,
            &Frame::Reply {
                id: "r".into(),
                text: String::new()
            },
            ""
        ));
        assert!(fresh.steps.is_empty());
        assert!(Frame::decode("nonsense", "{}").is_none());
        assert!(Frame::decode("activity", "not json").is_none());
    }

    #[test]
    fn the_clock_stamps_an_instant_the_record_can_hold() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(20_711), (2026, 9, 15));
        let now = utc_now_rfc3339();
        assert_eq!(now.len(), 20);
        assert!(now.ends_with('Z'));
    }
}
