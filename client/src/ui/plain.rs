//! The line printer behind pipes, `TERM=dumb`, and `UFO_PLAIN`: every directive is plain lines
//! on stdout, prompts read stdin, nothing repaints.

use std::io::{self, BufRead, Write};

/// Plain output state: whether a streamed line is still open.
pub struct Plain {
    open: bool,
}

impl Plain {
    pub fn new() -> Plain {
        Plain { open: false }
    }

    pub fn say(&mut self, text: &str) {
        self.line_break();
        println!("{text}");
    }

    pub fn note(&mut self, text: &str) {
        self.say(text);
    }

    /// A member message replayed from the transcript.
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
    }

    /// Read one line under `prompt`; None on EOF.
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

    /// Numbered menu; a reply outside 1..=n passes through as free text.
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

    /// Read one secret without echo; `None` on EOF, `Some("")` when skipped. Any reachable
    /// terminal goes through the raw-mode reader — crossterm falls back to the tty when stdin is
    /// a pipe, which is what a `curl | sh` install leaves behind — so the typed value never
    /// echoes; only a fully headless run reads a line from stdin, where no terminal echoes.
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
