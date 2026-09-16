use ufo::fold::Frame;
use ufo::ui::markdown::committed_split;
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
    "One committed block of a streamed answer, wrapped whole into the transcript.\n\n";
const AT: &str = "2026-09-16T00:00:00Z";

fn main() {
    divan::main();
}

fn theme() -> Theme {
    Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
}

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
fn split_a_streamed_reply_at_every_delta(bencher: divan::Bencher) {
    let deltas = deltas();
    bencher
        .counter(divan::counter::BytesCount::of_str(REPLY))
        .with_inputs(String::new)
        .bench_local_refs(|held| {
            for delta in &deltas {
                held.push_str(delta);
                divan::black_box(committed_split(held));
            }
            divan::black_box(committed_split(held).0.len())
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
            retained.begin_turn();
            retained.window(ROWS, &[], &theme);
            retained
        })
        .bench_local_refs(|retained| {
            retained.fold(
                &Frame::Message {
                    text: BLOCK.to_string(),
                },
                AT,
            );
            divan::black_box(retained.window(ROWS, &[], &theme))
        });
}
