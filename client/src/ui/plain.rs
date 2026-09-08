
use std::collections::HashSet;
use std::io::{self, BufRead, Write};

use crate::ui::retained::rollup_line;
use crate::ui::{narrates_activity, run_label};

pub struct Plain {
    open: bool,
    steps: usize,
    runs_counted: HashSet<String>,
    thinking: bool,
}

impl Plain {
    pub fn new() -> Plain {
        Plain {
            open: false,
            steps: 0,
            runs_counted: HashSet::new(),
            thinking: false,
        }
    }

    pub fn say(&mut self, text: &str) {
        self.line_break();
        println!("{text}");
    }

    pub fn note(&mut self, text: &str) {
        if narrates_activity(text) {
            match run_label(text) {
                None => {
                    self.steps += usize::from(self.thinking) + 1;
                    self.thinking = false;
                }
                Some(label) => {
                    self.steps += usize::from(self.runs_counted.insert(label.to_string()));
                }
            }
        }
        self.say(text);
    }

    pub fn activity(&mut self, text: &str, run: Option<&str>) {
        match run {
            None => {
                self.steps += 1;
                self.thinking = false;
            }
            Some(label) => {
                self.steps += usize::from(self.runs_counted.insert(label.to_string()));
            }
        }
        self.say(text);
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

    pub fn txt(&mut self, chunk: &str) {
        print!("{chunk}");
        let _ = io::stdout().flush();
        if !chunk.is_empty() {
            self.open = !chunk.ends_with('\n');
            self.thinking |= !chunk.trim().is_empty();
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
        let steps = std::mem::take(&mut self.steps);
        self.runs_counted.clear();
        self.thinking = false;
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
