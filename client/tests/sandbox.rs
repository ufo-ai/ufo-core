#![cfg(any(target_os = "macos", target_os = "linux"))]

use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_ufo")
}

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("ufo-sandbox-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

fn confined(writes: &[&PathBuf], script: &str) -> std::process::Output {
    let mut command = Command::new(binary());
    command.arg("sandbox");
    for root in writes {
        command.arg("--write").arg(root);
    }
    command.args(["--", "sh", "-c", script]).output().unwrap()
}

#[test]
fn a_write_inside_a_named_root_lands_and_one_outside_is_refused() {
    let inside = scratch("inside");
    let outside = scratch("outside");
    let landed = inside.join("ok.txt");
    let refused = outside.join("no.txt");

    let output = confined(
        &[&inside],
        &format!(
            "printf ok > '{}' && printf no > '{}'",
            landed.display(),
            refused.display()
        ),
    );

    assert_ne!(output.status.code(), Some(0), "{output:?}");
    assert_eq!(fs::read_to_string(&landed).unwrap(), "ok");
    assert!(!refused.exists());
}

#[test]
fn reads_outside_the_roots_and_the_command_exit_code_pass_through() {
    let inside = scratch("reads");
    let readable = std::env::current_exe().unwrap();

    let output = confined(
        &[&inside],
        &format!("head -c 4 '{}' > /dev/null && exit 7", readable.display()),
    );

    assert_eq!(output.status.code(), Some(7), "{output:?}");
}

#[test]
fn a_child_process_is_confined_like_its_parent() {
    let inside = scratch("child");
    let outside = scratch("child-outside");
    let refused = outside.join("no.txt");

    let output = confined(
        &[&inside],
        &format!(
            "sh -c \"printf no > '{}'\"; test ! -e '{}'",
            refused.display(),
            refused.display()
        ),
    );

    assert_eq!(output.status.code(), Some(0), "{output:?}");
}

#[test]
fn a_named_root_cannot_itself_be_removed() {
    let inside = scratch("root");

    let output = confined(&[&inside], &format!("rmdir '{}'", inside.display()));

    assert_ne!(output.status.code(), Some(0), "{output:?}");
    assert!(inside.is_dir());
}

#[test]
fn usage_is_refused_without_a_command() {
    let output = Command::new(binary())
        .args(["sandbox", "--write", "/tmp", "--"])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("usage: ufo sandbox"));
}
