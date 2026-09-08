use std::io::Write;
use std::time::{Duration, Instant};

use ufo::ui::retained::{Entry, Retained};
use ufo::ui::theme::{ColorMode, Scheme, Theme};

const TURNS: usize = 500;
const ROWS: usize = 40;
const WIDE: u16 = 100;
const NARROW: u16 = 76;
const RUN_SECONDS: u64 = 4;

fn code_answer(turn: usize) -> String {
    const ANSWER: &str = r#"Turn TURN:

```rust
fn parse_line_TURN(line: &str) -> Directive {
    let mut fields = line.split('\t');
    match fields.next() {
        Some("txt") => Directive::Txt(field(&fields, 0)),
        _ => Directive::Unknown,
    }
}
```
"#;
    ANSWER.replace("TURN", &turn.to_string())
}

fn main() {
    let run = Duration::from_secs(
        std::env::args()
            .nth(1)
            .and_then(|seconds| seconds.parse().ok())
            .unwrap_or(RUN_SECONDS),
    );
    let theme = Theme::for_mode(ColorMode::TrueColor, Scheme::Dark);
    let mut retained = Retained::new(WIDE);
    for turn in 0..TURNS {
        retained.push(Entry::Member(format!("show me turn {turn}")));
        retained.push(Entry::Markdown(code_answer(turn)));
    }
    retained.window(ROWS, &[], &theme);

    println!("{TURNS} turns of fenced code, re-wrapped between {WIDE} and {NARROW} columns\n");
    let started = Instant::now();
    let mut frames = 0u32;
    let mut reported = Instant::now();
    let mut slowest = Duration::ZERO;
    while started.elapsed() < run {
        let at = Instant::now();
        retained.set_width(if frames.is_multiple_of(2) {
            NARROW
        } else {
            WIDE
        });
        retained.window(ROWS, &[], &theme);
        let took = at.elapsed();
        slowest = slowest.max(took);
        frames += 1;
        if reported.elapsed() >= Duration::from_millis(250) {
            reported = Instant::now();
            let rate = frames as f64 / started.elapsed().as_secs_f64();
            print!(
                "\r  {frames:>5} reflows   {rate:>7.1}/s   last {:>8.2?}   slowest {:>8.2?}",
                took, slowest
            );
            let _ = std::io::stdout().flush();
        }
    }
    let rate = frames as f64 / started.elapsed().as_secs_f64();
    println!(
        "\r  {frames:>5} reflows   {rate:>7.1}/s   slowest {slowest:>8.2?}                    "
    );
    println!(
        "\n  a 60 fps frame is 16.7ms: {}",
        if slowest > Duration::from_millis(16) {
            "a drag drops frames"
        } else {
            "a drag holds its frames"
        }
    );
}
