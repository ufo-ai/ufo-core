use std::ops::Range;
use std::time::{Duration, Instant};

use ratatui::text::{Line, Span};

use crate::pr::Pr;
use crate::ui::osc;
use crate::ui::theme::Theme;
use crate::ui::wrap;

pub const SPINNER_FRAMES: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const SEPARATOR: &str = " · ";
const CHANNEL_COLS: usize = 8;
const TITLE_COLS: usize = 128;
const KEEPALIVE: Duration = Duration::from_secs(1);
const MINUTE: u64 = 60;
const HOUR: u64 = 60 * MINUTE;

#[derive(Debug, Clone)]
pub enum Activity {
    Idle,
    WaitingInput,
    Working {
        since: Instant,
        status: String,
    },
    Reconnecting {
        attempt: u32,
        of: u32,
        retry_in_s: u64,
    },
}

pub struct StatusRow {
    pub activity: Activity,
    tick: usize,
}

impl StatusRow {
    pub fn new() -> StatusRow {
        StatusRow {
            activity: Activity::Idle,
            tick: 0,
        }
    }

    pub fn on_tick(&mut self) {
        self.tick = self.tick.wrapping_add(1);
    }

    pub fn phase(&self) -> usize {
        self.tick
    }

    pub fn render(&self, theme: &Theme, width: u16) -> Line<'static> {
        match &self.activity {
            Activity::Idle | Activity::WaitingInput => Line::raw(""),
            Activity::Working { since, status } => {
                let frame = SPINNER_FRAMES[self.tick % SPINNER_FRAMES.len()];
                let ran = elapsed(since.elapsed());
                let fixed = 2 + wrap::width(frame) + wrap::width(SEPARATOR) + wrap::width(&ran);
                let status = wrap::clip(status, (width as usize).saturating_sub(fixed));
                let said = if status.is_empty() {
                    format!(" {ran}")
                } else {
                    format!(" {status}{SEPARATOR}{ran}")
                };
                Line::from(vec![
                    Span::styled(format!(" {frame}"), theme.accent),
                    Span::styled(said, theme.muted),
                ])
            }
            Activity::Reconnecting {
                attempt,
                of,
                retry_in_s,
            } => Line::styled(
                format!(" Retrying ({attempt}/{of}) in {retry_in_s}s"),
                theme.warning,
            ),
        }
    }
}

impl Default for StatusRow {
    fn default() -> StatusRow {
        StatusRow::new()
    }
}

pub fn elapsed(ran: Duration) -> String {
    let secs = ran.as_secs();
    match secs {
        s if s < MINUTE => format!("{s}s"),
        s if s < HOUR => format!("{}m{:02}s", s / MINUTE, s % MINUTE),
        s => format!("{}h{:02}m", s / HOUR, (s % HOUR) / MINUTE),
    }
}

pub const LIST_HINT: &str = "⌃K list";
const HINT_INSET: usize = 1;

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FooterHits {
    pub pr: Option<Range<usize>>,
    pub list: Option<Range<usize>>,
}

pub fn footer(
    theme: &Theme,
    width: u16,
    workspace_host: &str,
    channel: &str,
    pr: Option<&Pr>,
    hint: Option<&str>,
) -> (Line<'static>, FooterHits) {
    if workspace_host.is_empty() {
        return (Line::raw(""), FooterHits::default());
    }
    let width = width as usize;
    let separator = wrap::width(SEPARATOR);
    let hint = hint.filter(|hint| width > separator + wrap::width(hint) + HINT_INSET);
    let hint_width = hint.map_or(0, wrap::width);
    let room = if hint.is_some() {
        width - HINT_INSET - hint_width - separator
    } else {
        width
    };
    let channel = wrap::clip(channel, CHANNEL_COLS);
    let stated = if channel.is_empty() {
        workspace_host.to_string()
    } else {
        format!("{workspace_host}{SEPARATOR}{channel}")
    };
    let mut spans = Vec::new();
    let mut used;
    let mut hits = FooterHits::default();
    let linked = pr.and_then(|pr| {
        let label = format!("PR #{}", pr.number);
        let at = wrap::width(&stated) + separator;
        let full = at + wrap::width(&label);
        (full <= room).then_some((pr, label, at, full))
    });
    match linked {
        Some((pr, label, at, full)) => {
            spans.push(Span::styled(format!("{stated}{SEPARATOR}"), theme.muted));
            spans.push(Span::styled(
                format!("{}{label}{}", osc::link_open(&pr.url), osc::LINK_CLOSE),
                theme.pr,
            ));
            hits.pr = Some(at..full);
            used = full;
        }
        None => {
            let text = wrap::clip(&stated, room).to_string();
            used = wrap::width(&text);
            spans.push(Span::styled(text, theme.muted));
        }
    }
    if let Some(hint) = hint {
        let end = width - HINT_INSET;
        spans.push(Span::raw(" ".repeat(end - used - hint_width)));
        spans.push(Span::styled(hint.to_string(), theme.muted));
        hits.list = Some(end - hint_width..end);
        used = end;
    }
    spans.push(Span::raw(" ".repeat(width.saturating_sub(used))));
    (Line::from(spans), hits)
}

pub struct Signals {
    pub enabled: bool,
}

impl Signals {
    pub fn title(&self, title: &str) -> String {
        if !self.enabled {
            return String::new();
        }
        let stated: String = title.chars().filter(|ch| !ch.is_control()).collect();
        format!("\x1b]0;{}\x07", wrap::clip(&stated, TITLE_COLS))
    }

    pub fn progress_on(&self) -> String {
        if !self.enabled {
            return String::new();
        }
        "\x1b]9;4;3\x07".to_string()
    }

    pub fn progress_off(&self) -> String {
        if !self.enabled {
            return String::new();
        }
        "\x1b]9;4;0\x07".to_string()
    }

    pub fn bell(&self) -> String {
        if !self.enabled {
            return String::new();
        }
        "\x07".to_string()
    }
}

pub struct Progress {
    last_emit: Option<Instant>,
}

impl Progress {
    pub fn new() -> Progress {
        Progress { last_emit: None }
    }

    pub fn tick(&mut self, signals: &Signals, now: Instant) -> String {
        if !signals.enabled {
            return String::new();
        }
        if self
            .last_emit
            .is_some_and(|last| now.saturating_duration_since(last) < KEEPALIVE)
        {
            return String::new();
        }
        self.last_emit = Some(now);
        signals.progress_on()
    }

    pub fn off(&mut self, signals: &Signals) -> String {
        match self.last_emit.take() {
            Some(_) => signals.progress_off(),
            None => String::new(),
        }
    }
}

impl Default for Progress {
    fn default() -> Progress {
        Progress::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme, Theme};

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::Plain, Scheme::Dark)
    }

    fn working(status: &str, ran: Duration) -> StatusRow {
        let since = Instant::now()
            .checked_sub(ran)
            .expect("the monotonic clock is older than the test duration");
        StatusRow {
            activity: Activity::Working {
                since,
                status: status.to_string(),
            },
            tick: 0,
        }
    }

    #[test]
    fn elapsed_states_the_coarsest_unit() {
        assert_eq!(elapsed(Duration::from_secs(0)), "0s");
        assert_eq!(elapsed(Duration::from_secs(59)), "59s");
        assert_eq!(elapsed(Duration::from_secs(60)), "1m00s");
        assert_eq!(elapsed(Duration::from_secs(72)), "1m12s");
        assert_eq!(elapsed(Duration::from_secs(3599)), "59m59s");
        assert_eq!(elapsed(Duration::from_secs(3600)), "1h00m");
        assert_eq!(elapsed(Duration::from_secs(3720)), "1h02m");
    }

    #[test]
    fn working_row_states_the_status_and_the_wait() {
        let row = working("reading files", Duration::from_secs(72));
        let text = row.render(&theme(), 80).to_string();
        assert_eq!(text, " ⠋ reading files · 1m12s");
    }

    #[test]
    fn working_row_without_a_status_is_the_spinner_and_the_wait() {
        let row = working("", Duration::from_secs(3));
        assert_eq!(row.render(&theme(), 80).to_string(), " ⠋ 3s");
    }

    #[test]
    fn working_row_clips_the_status_to_the_width() {
        let row = working(
            "reading every file in the repository",
            Duration::from_secs(3),
        );
        let line = row.render(&theme(), 20);
        assert_eq!(line.to_string(), " ⠋ reading ever · 3s");
        assert_eq!(line.width(), 20);
    }

    #[test]
    fn narrow_row_drops_the_status_before_the_wait() {
        let row = working("reading files", Duration::from_secs(3));
        assert_eq!(row.render(&theme(), 8).to_string(), " ⠋ 3s");
    }

    #[test]
    fn a_tick_advances_the_spinner() {
        let mut row = working("", Duration::from_secs(1));
        let first = row.render(&theme(), 80).to_string();
        row.on_tick();
        assert_ne!(row.render(&theme(), 80).to_string(), first);
    }

    #[test]
    fn idle_and_waiting_rows_are_empty() {
        let mut row = StatusRow::new();
        assert!(row.render(&theme(), 80).to_string().is_empty());
        row.activity = Activity::WaitingInput;
        assert!(row.render(&theme(), 80).to_string().is_empty());
    }

    #[test]
    fn reconnect_row_states_the_countdown() {
        let mut row = StatusRow::new();
        row.activity = Activity::Reconnecting {
            attempt: 2,
            of: 3,
            retry_in_s: 1,
        };
        assert_eq!(
            row.render(&theme(), 80).to_string(),
            " Retrying (2/3) in 1s"
        );
    }

    #[test]
    fn footer_states_the_host_and_the_clipped_channel_with_the_list_hint_at_the_end() {
        let (line, hits) = footer(
            &theme(),
            40,
            "acme.ufo.dev",
            "engineering-standup",
            None,
            Some(LIST_HINT),
        );
        assert_eq!(
            line.to_string(),
            format!("{:<32}{LIST_HINT} ", "acme.ufo.dev · engineer")
        );
        assert_eq!(line.width(), 40);
        assert_eq!(
            hits,
            FooterHits {
                pr: None,
                list: Some(32..39),
            }
        );
    }

    #[test]
    fn footer_without_a_channel_is_the_host_alone() {
        let (line, _) = footer(&theme(), 30, "acme.ufo.dev", "", None, Some(LIST_HINT));
        assert_eq!(
            line.to_string(),
            format!("{:<22}{LIST_HINT} ", "acme.ufo.dev")
        );
    }

    #[test]
    fn footer_clips_the_host_before_the_hint_and_drops_the_hint_when_nothing_fits() {
        let (line, hits) = footer(
            &theme(),
            20,
            "acme.ufo.dev",
            "general",
            None,
            Some(LIST_HINT),
        );
        assert_eq!(line.to_string(), format!("acme.ufo.   {LIST_HINT} "));
        assert_eq!(hits.list, Some(12..19));
        let (line, hits) = footer(
            &theme(),
            10,
            "acme.ufo.dev",
            "general",
            None,
            Some(LIST_HINT),
        );
        assert_eq!(line.to_string(), "acme.ufo.d");
        assert_eq!(hits.list, None);
    }

    #[test]
    fn footer_without_a_host_is_nothing() {
        let (line, hits) = footer(&theme(), 40, "", "general", None, Some(LIST_HINT));
        assert!(line.to_string().is_empty());
        assert_eq!(hits, FooterHits::default());
    }

    #[test]
    fn footer_states_the_pr_as_a_link_and_its_columns() {
        let pr = Pr {
            number: 1892,
            url: "https://github.com/acme/repo/pull/1892".to_string(),
        };
        let (line, hits) = footer(
            &theme(),
            44,
            "acme.ufo.dev",
            "general",
            Some(&pr),
            Some(LIST_HINT),
        );
        assert_eq!(
            line.to_string(),
            format!(
                "acme.ufo.dev · general · {}PR #1892{}{}{LIST_HINT} ",
                osc::link_open(&pr.url),
                osc::LINK_CLOSE,
                " ".repeat(44 - 1 - 33 - 7)
            )
        );
        assert_eq!(
            hits,
            FooterHits {
                pr: Some(25..33),
                list: Some(36..43),
            }
        );
    }

    #[test]
    fn footer_under_the_page_carries_no_list_hint() {
        let (line, hits) = footer(&theme(), 40, "acme.ufo.dev", "general", None, None);
        assert_eq!(
            line.to_string(),
            format!("{:<40}", "acme.ufo.dev · general")
        );
        assert_eq!(hits, FooterHits::default());
    }

    #[test]
    fn footer_drops_the_pr_before_the_host_and_channel() {
        let pr = Pr {
            number: 1892,
            url: "https://github.com/acme/repo/pull/1892".to_string(),
        };
        let (line, hits) = footer(
            &theme(),
            36,
            "acme.ufo.dev",
            "general",
            Some(&pr),
            Some(LIST_HINT),
        );
        assert_eq!(
            line.to_string(),
            format!("{:<28}{LIST_HINT} ", "acme.ufo.dev · general")
        );
        assert_eq!(hits.pr, None);
        assert_eq!(hits.list, Some(28..35));
    }

    #[test]
    fn progress_repeats_once_a_second() {
        let signals = Signals { enabled: true };
        let mut progress = Progress::new();
        let start = Instant::now();
        assert_eq!(progress.tick(&signals, start), signals.progress_on());
        assert!(progress
            .tick(&signals, start + Duration::from_millis(999))
            .is_empty());
        assert_eq!(
            progress.tick(&signals, start + Duration::from_secs(1)),
            signals.progress_on()
        );
        assert!(progress
            .tick(&signals, start + Duration::from_millis(1500))
            .is_empty());
    }

    #[test]
    fn progress_off_clears_once_and_only_after_on() {
        let signals = Signals { enabled: true };
        let mut progress = Progress::new();
        assert!(progress.off(&signals).is_empty());
        progress.tick(&signals, Instant::now());
        assert_eq!(progress.off(&signals), signals.progress_off());
        assert!(progress.off(&signals).is_empty());
    }

    #[test]
    fn signals_are_silent_when_disabled() {
        let signals = Signals { enabled: false };
        assert!(signals.title("t").is_empty());
        assert!(signals.progress_on().is_empty());
        assert!(signals.progress_off().is_empty());
        assert!(signals.bell().is_empty());
        let mut progress = Progress::new();
        assert!(progress.tick(&signals, Instant::now()).is_empty());
        assert!(progress.off(&signals).is_empty());
    }

    #[test]
    fn title_drops_control_characters() {
        let signals = Signals { enabled: true };
        assert_eq!(signals.title("ship\x07 it\n"), "\x1b]0;ship it\x07");
    }
}
