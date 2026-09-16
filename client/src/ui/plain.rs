use std::collections::BTreeSet;
use std::io::{self, BufRead, Write};

use crate::fold::{self, Frame};
use crate::record::TurnRecord;
use crate::ui::retained::rollup_line;
use crate::ui::{meter_line, RESUMED_NOTE, SENT_BY_UFO};

pub struct Plain {
    open: bool,
    record: TurnRecord,
}

impl Plain {
    pub fn new() -> Plain {
        Plain {
            open: false,
            record: fold::empty(None),
        }
    }

    pub fn say(&mut self, text: &str) {
        self.line_break();
        println!("{text}");
    }

    pub fn note(&mut self, text: &str) {
        self.say(text);
    }

    /// One live frame off the wire, folded into the turn record and printed as the line it is: a
    /// step's label, a run's step under its name, the meter, a resume, the places a step read, or
    /// a delivered reply. A drain prints nothing, and a frame that will not decode prints nothing.
    pub fn frame(&mut self, event: &str, data: &str) {
        let Some(frame) = Frame::decode(event, data) else {
            return;
        };
        fold::fold(&mut self.record, &frame, &fold::utc_now_rfc3339());
        match frame {
            Frame::Message { text } => self.txt(&text),
            Frame::Activity { text } if !text.is_empty() => self.say(&text),
            Frame::Activity { .. } | Frame::Absorbed { .. } => {}
            Frame::SubagentActivity(run) if !run.activity.is_empty() => {
                self.say(&format!("{}: {}", run.label(), run.activity));
            }
            Frame::SubagentActivity(_) => {}
            Frame::Cost {
                tokens,
                cost_micro_usd,
            } => self.status(&meter_line(tokens, cost_micro_usd)),
            Frame::Resumed { .. } => self.say(RESUMED_NOTE),
            Frame::Reply { text, .. } | Frame::Comment { text, .. } => self.say(&text),
            Frame::Sources { .. } | Frame::Terminal(_) | Frame::Parked { .. } => {}
        }
    }

    pub fn member(&mut self, text: &str) {
        self.line_break();
        for (index, row) in text.lines().enumerate() {
            if index == 0 {
                println!("\u{203a} {row}");
            } else {
                println!("  {row}");
            }
        }
    }

    pub fn fired(&mut self, text: &str) {
        self.line_break();
        println!("{SENT_BY_UFO}");
        for row in text.lines() {
            println!("  {row}");
        }
    }

    pub fn txt(&mut self, chunk: &str) {
        print!("{chunk}");
        let _ = io::stdout().flush();
        if !chunk.is_empty() {
            self.open = !chunk.ends_with('\n');
        }
    }

    pub fn status(&mut self, text: &str) {
        self.line_break();
        println!("  {text}");
    }

    pub fn file(&mut self, name: &str, size: &str, url: &str) {
        if url.is_empty() {
            self.say(&format!("shared {name} ({size} bytes)"));
        } else {
            self.say(&format!("shared {name} ({size} bytes) {url}"));
        }
    }

    pub fn end_stream(&mut self) {
        self.line_break();
        let steps = fold::step_count(&self.record);
        self.record = fold::empty(None);
        if steps > 0 {
            println!("{}", rollup_line(steps));
        }
    }

    pub fn ask(&mut self, prompt: &str) -> Option<String> {
        self.line_break();
        eprint!("{prompt} ");
        let _ = io::stderr().flush();
        let mut line = String::new();
        match io::stdin().lock().read_line(&mut line) {
            Ok(0) | Err(_) => None,
            Ok(_) => Some(line.trim_end_matches(['\r', '\n']).to_string()),
        }
    }

    pub fn menu(&mut self, prompt: &str, options: &[String]) -> Option<String> {
        self.line_break();
        println!("{prompt}");
        for (index, option) in options.iter().enumerate() {
            println!("  {}) {option}", index + 1);
        }
        let answer = self.ask("›")?;
        if answer.is_empty() {
            return None;
        }
        match answer.parse::<usize>() {
            Ok(number) if (1..=options.len()).contains(&number) => {
                Some(options[number - 1].clone())
            }
            _ => Some(answer),
        }
    }

    pub fn menu_selected(&mut self, prompt: &str, options: &[String]) -> Option<(String, usize)> {
        loop {
            self.line_break();
            println!("{prompt}");
            for (index, option) in options.iter().enumerate() {
                println!("  {}) {option}", index + 1);
            }
            let answer = self.ask("›")?;
            if answer.is_empty() {
                return None;
            }
            if let Some(index) = answer
                .parse::<usize>()
                .ok()
                .and_then(|number| number.checked_sub(1))
                .filter(|index| *index < options.len())
            {
                return Some((options[index].clone(), index));
            }
        }
    }

    pub fn menu_many(&mut self, prompt: &str, options: &[String]) -> Option<String> {
        self.line_break();
        println!("{prompt}");
        for (index, option) in options.iter().enumerate() {
            println!("  {}) {option}", index + 1);
        }
        loop {
            let answer = self.ask("Select numbers, separated by commas:")?;
            if answer.is_empty() {
                return None;
            }
            let selected = answer
                .split(',')
                .map(str::trim)
                .map(|token| {
                    token
                        .parse::<usize>()
                        .ok()
                        .and_then(|number| number.checked_sub(1))
                        .filter(|index| *index < options.len())
                })
                .collect::<Option<BTreeSet<_>>>();
            let Some(selected) = selected else {
                continue;
            };
            return Some(
                options
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| selected.contains(index))
                    .map(|(_, option)| option.as_str())
                    .collect::<Vec<_>>()
                    .join(", "),
            );
        }
    }

    pub fn secret(&mut self, prompt: &str) -> Option<String> {
        use crossterm::event::{read, Event, KeyCode, KeyEventKind, KeyModifiers};
        use crossterm::tty::IsTty;

        self.line_break();
        if !io::stdin().is_tty() && !io::stderr().is_tty() {
            eprint!("{prompt} (hidden): ");
            let _ = io::stderr().flush();
            let mut line = String::new();
            return match io::stdin().lock().read_line(&mut line) {
                Ok(0) | Err(_) => None,
                Ok(_) => Some(line.trim_end_matches(['\r', '\n']).to_string()),
            };
        }
        let _ = crossterm::terminal::enable_raw_mode();
        eprint!("\r{prompt} (hidden): ");
        let _ = io::stderr().flush();
        let mut value = String::new();
        let entered = loop {
            let event = match read() {
                Ok(event) => event,
                Err(_) => break None,
            };
            let Event::Key(key) = event else { continue };
            if key.kind == KeyEventKind::Release {
                continue;
            }
            let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
            match key.code {
                KeyCode::Enter => break Some(std::mem::take(&mut value)),
                KeyCode::Backspace => {
                    value.pop();
                }
                KeyCode::Char('u') if ctrl => value.clear(),
                KeyCode::Char('c') | KeyCode::Char('d') if ctrl => break None,
                KeyCode::Char(ch) if !ctrl => value.push(ch),
                _ => {}
            }
        };
        let _ = crossterm::terminal::disable_raw_mode();
        eprint!("\r\n");
        let _ = io::stderr().flush();
        entered
    }

    fn line_break(&mut self) {
        if self.open {
            println!();
            self.open = false;
        }
    }
}

impl Default for Plain {
    fn default() -> Plain {
        Plain::new()
    }
}
