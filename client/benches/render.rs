use base64::Engine;

use ufo::ops::OP_FILE;
use ufo::ui::markdown;
use ufo::ui::theme::{ColorMode, Scheme, Theme};
use ufo::ui::toolrender::{OpState, OpView};
use ufo::wire::OpRequest;

const WIDTH: u16 = 100;

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

fn main() {
    divan::main();
}

fn theme() -> Theme {
    Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
}

fn edit_params(old: &str, new: &str) -> String {
    let encode = |value: &str| base64::engine::general_purpose::STANDARD.encode(value);
    format!(
        r#"{{"path":"/w/wire.rs","edits":[{{"old_string_b64":"{}","new_string_b64":"{}"}}]}}"#,
        encode(old),
        encode(new)
    )
}

#[divan::bench]
fn render_a_reply_without_code(bencher: divan::Bencher) {
    let theme = theme();
    let prose = REPLY
        .split("```")
        .next()
        .expect("the prose before the fence");
    bencher
        .counter(divan::counter::BytesCount::of_str(prose))
        .bench(|| divan::black_box(markdown::render(prose, &theme, WIDTH)));
}

#[divan::bench]
fn render_a_reply(bencher: divan::Bencher) {
    let theme = theme();
    bencher
        .counter(divan::counter::BytesCount::of_str(REPLY))
        .bench(|| divan::black_box(markdown::render(REPLY, &theme, WIDTH)));
}

#[divan::bench]
fn render_an_edits_diff(bencher: divan::Bencher) {
    let theme = theme();
    let old: String = (1..=60).map(|n| format!("line {n}\n")).collect();
    let new: String = (1..=60).map(|n| format!("row {n}\n")).collect();
    let view = OpView::from_request(&OpRequest {
        op_id: "op1".to_string(),
        kind: OP_FILE.to_string(),
        name: "edit".to_string(),
        timeout_s: 60,
        arg: String::new(),
        params: edit_params(&old, &new),
        call_id: String::new(),
    });
    bencher.bench(|| divan::black_box(view.body(Ok(b"{}"), OpState::Done, &theme)));
}
