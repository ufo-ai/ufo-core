use ufo::ui::editor::{AskState, Key};
use ufo::ui::retained::{Entry, Retained};
use ufo::ui::theme::{ColorMode, Scheme, Theme};

const WIDTH: u16 = 100;
const NARROWER: u16 = 76;
const ROWS: usize = 40;

fn main() {
    divan::main();
}

fn theme() -> Theme {
    Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
}

fn code_answer(turn: usize) -> String {
    const ANSWER: &str = r#"Turn TURN:

```rust
fn parse_line_TURN(line: &str) -> Directive {
    let mut fields = line.split('\t');
    Directive::Txt(field(&fields, 0))
}
```
"#;
    ANSWER.replace("TURN", &turn.to_string())
}

fn painted(turns: usize, theme: &Theme) -> Retained {
    let mut retained = Retained::new(WIDTH);
    for turn in 0..turns {
        retained.push(Entry::Member(format!(
            "what does turn {turn} cost to paint?"
        )));
        retained.push(Entry::Markdown(format!(
            "Turn {turn} reads one line at a time.\n\n- the wire hands over directives\n\
             - the renderer styles them\n- the screen keeps what it painted\n"
        )));
    }
    retained.window(ROWS, &[], theme);
    retained
}

#[divan::bench(args = [10, 200, 2000])]
fn paint_the_window(bencher: divan::Bencher, turns: usize) {
    let theme = theme();
    bencher
        .with_inputs(|| painted(turns, &theme))
        .bench_local_refs(|retained| divan::black_box(retained.window(ROWS, &[], &theme)));
}

#[divan::bench(args = [10, 200, 2000])]
fn rewrap_on_resize(bencher: divan::Bencher, turns: usize) {
    let theme = theme();
    bencher
        .with_inputs(|| painted(turns, &theme))
        .bench_local_refs(|retained| {
            retained.set_width(NARROWER);
            divan::black_box(retained.window(ROWS, &[], &theme))
        });
}

#[divan::bench(args = [10, 200])]
fn rewrap_a_transcript_of_code(bencher: divan::Bencher, turns: usize) {
    let theme = theme();
    bencher
        .with_inputs(|| {
            let mut retained = Retained::new(WIDTH);
            for turn in 0..turns {
                retained.push(Entry::Member(format!("show me turn {turn}")));
                retained.push(Entry::Markdown(code_answer(turn)));
            }
            retained.window(ROWS, &[], &theme);
            retained
        })
        .bench_local_refs(|retained| {
            retained.set_width(NARROWER);
            divan::black_box(retained.window(ROWS, &[], &theme))
        });
}

#[divan::bench(args = [0, 200, 2000])]
fn type_a_key(bencher: divan::Bencher, draft: usize) {
    bencher
        .with_inputs(|| {
            let mut ask = AskState::default();
            for character in "the draft a member is still writing "
                .chars()
                .cycle()
                .take(draft)
            {
                ask.apply(Key::Char(character), &[], WIDTH as usize);
            }
            ask
        })
        .bench_local_refs(|ask| {
            ask.apply(Key::Char('t'), &[], WIDTH as usize);
            divan::black_box(ask.render(WIDTH as usize))
        });
}

#[divan::bench(args = [1_000, 20_000])]
fn paste_into_the_composer(bencher: divan::Bencher, bytes: usize) {
    let pasted = "a log line the member pasted\n".repeat(bytes / 28);
    bencher
        .counter(divan::counter::BytesCount::of_str(&pasted))
        .with_inputs(AskState::default)
        .bench_local_refs(|ask| {
            ask.apply(Key::Paste(pasted.clone()), &[], WIDTH as usize);
            divan::black_box(ask.render(WIDTH as usize))
        });
}
