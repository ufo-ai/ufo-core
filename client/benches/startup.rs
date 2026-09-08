use std::process::{Command, Stdio};

use syntect::highlighting::ThemeSet;
use syntect::parsing::SyntaxSet;

use ufo::ui::conversations::row_text;
use ufo::wire::ConversationRow;

fn main() {
    divan::main();
}

#[divan::bench]
fn run_to_the_help_screen() {
    let status = Command::new(env!("CARGO_BIN_EXE_ufo"))
        .arg("--help")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .expect("the client runs");
    assert!(status.success(), "--help exits 0");
}

#[divan::bench]
fn load_the_syntax_assets() {
    divan::black_box((
        SyntaxSet::load_defaults_newlines(),
        ThemeSet::load_defaults(),
    ));
}

#[divan::bench(args = [10, 300])]
fn draw_the_conversation_rows(bencher: divan::Bencher, rows: usize) {
    let listed = listed(rows);
    bencher.bench(|| {
        divan::black_box(
            listed
                .iter()
                .map(|row| row_text(row, 1_700_000_000))
                .collect::<Vec<String>>(),
        )
    });
}

fn listed(rows: usize) -> Vec<ConversationRow> {
    (0..rows)
        .map(|row| ConversationRow {
            id: format!("00000000-0000-4000-8000-{row:012}"),
            title: format!("conversation {row} opened with a question"),
            surface: if row % 2 == 0 { "slack" } else { "web" }.to_string(),
            surface_label: (row % 2 == 0).then(|| "#general".to_string()),
            speaker: (row % 3 == 0).then(|| "Nate Ford".to_string()),
            agent: if row % 5 == 0 { "notes" } else { "assistant" }.to_string(),
            main: row % 5 != 0,
            last_at: 1_699_000_000.0 + row as f64,
            postable: true,
            channel: None,
        })
        .collect()
}
