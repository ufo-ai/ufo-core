//! What a member waits for before anything is on screen: the binary loading, its statics
//! initializing, and the arguments parsing. `--help` is the one path that reaches the screen
//! without the wire, so it is the whole cost and nothing else. Beside it sit the two loads a first
//! screen can pay — the syntax assets a fenced code block needs, and the conversation log
//! `--resume` reads.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use syntect::highlighting::ThemeSet;
use syntect::parsing::SyntaxSet;

use ufo::ui::history::{list_conversations, record_conversation, PastConversation};

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

/// The first fenced code block in a reply pays this, once per process, behind the renderer's
/// `OnceLock`.
#[divan::bench]
fn load_the_syntax_assets() {
    divan::black_box((
        SyntaxSet::load_defaults_newlines(),
        ThemeSet::load_defaults(),
    ));
}

#[divan::bench(args = [10, 500])]
fn list_the_resume_conversations(bencher: divan::Bencher, rows: usize) {
    let home = logged(rows);
    bencher.bench(|| divan::black_box(list_conversations(&home)));
    let _ = fs::remove_dir_all(&home);
}

fn logged(rows: usize) -> PathBuf {
    let home =
        std::env::temp_dir().join(format!("ufo-bench-history-{rows}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&home);
    fs::create_dir_all(&home).expect("a scratch home");
    for row in 0..rows {
        record_conversation(
            &home,
            &PastConversation {
                channel: format!("host.{row}"),
                opened_epoch: 1_700_000_000 + row as u64,
                first_message: format!("conversation {row} opened with a question"),
            },
        );
    }
    home
}
