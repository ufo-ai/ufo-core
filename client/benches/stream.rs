//! What a reply costs as it arrives. Every delta pays the hold-back that keeps a half-arrived
//! block off the screen, and every finished block pays the commit into the transcript — many
//! times a second while the agent is answering.

use ufo::ui::markdown::StreamRenderer;
use ufo::ui::retained::{Entry, Retained};
use ufo::ui::theme::{ColorMode, Scheme, Theme};

const WIDTH: u16 = 100;
const ROWS: usize = 40;
const DELTA: usize = 24;

const REPLY: &str = "\
The parser reads one line at a time, so a turn's cost scales with its lines.

- The wire hands over directives.
- The renderer turns them into styled lines.
- The screen keeps what it already painted.

```rust
fn parse_line(line: &str) -> Directive {
    let mut fields = line.split('\\t');
    match fields.next() {
        Some(\"txt\") => Directive::Txt(field(&fields, 0)),
        _ => Directive::Unknown,
    }
}
```

The tail re-wraps whole, which is why the width is part of the measurement.
";

const BLOCK: &str =
    "One committed block of a streamed answer, wrapped whole into the transcript.\n";

fn main() {
    divan::main();
}

fn theme() -> Theme {
    Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
}

/// The reply cut into the deltas a stream delivers, ASCII so the cuts land on characters.
fn deltas() -> Vec<&'static str> {
    let mut deltas = Vec::new();
    let mut rest = REPLY;
    while !rest.is_empty() {
        let (head, tail) = rest.split_at(rest.len().min(DELTA));
        deltas.push(head);
        rest = tail;
    }
    deltas
}

#[divan::bench]
fn hold_back_a_streamed_reply(bencher: divan::Bencher) {
    let deltas = deltas();
    bencher
        .counter(divan::counter::BytesCount::of_str(REPLY))
        .with_inputs(StreamRenderer::default)
        .bench_local_refs(|stream| {
            for delta in &deltas {
                divan::black_box(stream.push(delta));
            }
            divan::black_box(stream.finish())
        });
}

#[divan::bench(args = [10, 200])]
fn commit_into_the_transcript(bencher: divan::Bencher, turns: usize) {
    let theme = theme();
    bencher
        .with_inputs(|| {
            let mut retained = Retained::new(WIDTH);
            for turn in 0..turns {
                retained.push(Entry::Markdown(format!("Turn {turn} already committed.\n")));
            }
            retained.window(ROWS, &[], &theme);
            retained
        })
        .bench_local_refs(|retained| {
            retained.extend_markdown(BLOCK);
            divan::black_box(retained.window(ROWS, &[], &theme))
        });
}
