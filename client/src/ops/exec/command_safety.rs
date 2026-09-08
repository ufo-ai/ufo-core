// Derived from OpenAI Codex shell-command revision 2e4675919ee9c90a0b1360e0826fe7117d71cebb.
// Modified to retain only Bash forced-rm classification. Copyright 2025 OpenAI.
// Licensed under Apache-2.0; see client/licenses/openai-codex.txt.

use std::path::Path;

use tree_sitter::Node;
use tree_sitter::Parser;
use tree_sitter_bash::LANGUAGE as BASH;

const MAX_NESTING_DEPTH: usize = 8;
const SHELL_INVOCATION_WORDS: usize = 3;

pub(super) fn dangerous_command_match(command: &[String]) -> bool {
    dangerous_command_match_with_depth(command, 0)
}

fn dangerous_command_match_with_depth(command: &[String], nesting_depth: usize) -> bool {
    if nesting_depth > MAX_NESTING_DEPTH {
        return true;
    }
    (0..command.len()).any(|start| dangerous_start_match(&command[start..], nesting_depth))
}

fn dangerous_start_match(command: &[String], nesting_depth: usize) -> bool {
    match command
        .first()
        .and_then(|command| executable_name(command))
        .as_deref()
    {
        Some("rm") => rm_args_include_force_option(&command[1..]),
        Some("trap") => dangerous_trap_match(command, nesting_depth),
        Some("eval") => dangerous_script_match(&command[1..].join(" "), nesting_depth),
        _ => literal_shell_commands(command).is_some_and(|commands| {
            commands
                .iter()
                .any(|command| dangerous_command_match_with_depth(command, nesting_depth + 1))
        }),
    }
}

fn dangerous_trap_match(command: &[String], nesting_depth: usize) -> bool {
    let mut action_index = 1;
    if command
        .get(action_index)
        .is_some_and(|argument| argument == "--")
    {
        action_index += 1;
    }
    let Some(action) = command
        .get(action_index)
        .filter(|action| !action.starts_with('-'))
    else {
        return false;
    };
    dangerous_script_match(action, nesting_depth)
}

fn dangerous_script_match(script: &str, nesting_depth: usize) -> bool {
    !script.is_empty()
        && dangerous_command_match_with_depth(
            &["sh".to_string(), "-c".to_string(), script.to_owned()],
            nesting_depth + 1,
        )
}

fn rm_args_include_force_option(args: &[String]) -> bool {
    args.iter()
        .take_while(|arg| arg.as_str() != "--")
        .any(|arg| {
            arg == "--force"
                || arg
                    .strip_prefix('-')
                    .is_some_and(|flags| !flags.starts_with('-') && flags.contains('f'))
        })
}

fn literal_shell_commands(command: &[String]) -> Option<Vec<Vec<String>>> {
    let [shell, flag, script] = command.get(..SHELL_INVOCATION_WORDS)? else {
        return None;
    };
    if !matches!(flag.as_str(), "-lc" | "-c")
        || !matches!(
            shell_name(shell).as_deref(),
            Some("zsh") | Some("bash") | Some("sh")
        )
    {
        return None;
    }
    let language = BASH.into();
    let mut parser = Parser::new();
    parser.set_language(&language).ok()?;
    let tree = parser.parse(script, None)?;
    let root = tree.root_node();
    if root.has_error() {
        return None;
    }

    let mut commands = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        if node.kind() == "command" {
            if let Some(command) = literal_command(node, script) {
                commands.push(command);
            }
        }
        let mut cursor = node.walk();
        stack.extend(node.named_children(&mut cursor));
    }
    Some(commands)
}

fn literal_command(command: Node<'_>, source: &str) -> Option<Vec<String>> {
    let mut words = Vec::new();
    let mut found_command_name = false;
    let mut cursor = command.walk();
    for child in command.named_children(&mut cursor) {
        if child.kind() == "command_name" {
            words.push(literal_shell_word(child.named_child(0)?, source)?);
            found_command_name = true;
        } else if found_command_name {
            if let Some(word) = literal_shell_word(child, source) {
                words.push(word);
            }
        }
    }
    found_command_name.then_some(words)
}

fn literal_shell_word(node: Node<'_>, source: &str) -> Option<String> {
    match node.kind() {
        "word" | "number" if literal_word(node, source) => {
            Some(node.utf8_text(source.as_bytes()).ok()?.to_owned())
        }
        "string" => double_quoted_string(node, source),
        "raw_string" => raw_string(node, source),
        "concatenation" => {
            let mut concatenated = String::new();
            let mut cursor = node.walk();
            for part in node.named_children(&mut cursor) {
                concatenated.push_str(&literal_shell_word(part, source)?);
            }
            (!concatenated.is_empty()).then_some(concatenated)
        }
        _ => None,
    }
}

fn literal_word(node: Node<'_>, source: &str) -> bool {
    let mut cursor = node.walk();
    node.named_children(&mut cursor).next().is_none()
        && node.utf8_text(source.as_bytes()).is_ok_and(|word| {
            !word.starts_with('=')
                && !word.contains(['{', '}', '*', '?', '[', ']', '\\', '~', '^', '#', '$', '`'])
        })
}

fn double_quoted_string(node: Node<'_>, source: &str) -> Option<String> {
    let mut cursor = node.walk();
    if node
        .named_children(&mut cursor)
        .any(|part| part.kind() != "string_content")
    {
        return None;
    }
    let raw = node.utf8_text(source.as_bytes()).ok()?;
    let stripped = raw
        .strip_prefix('"')
        .and_then(|text| text.strip_suffix('"'))?;
    if stripped
        .as_bytes()
        .windows(2)
        .any(|pair| pair[0] == b'\\' && matches!(pair[1], b'$' | b'`' | b'"' | b'\\' | b'\n'))
    {
        return None;
    }
    Some(stripped.to_owned())
}

fn raw_string(node: Node<'_>, source: &str) -> Option<String> {
    node.utf8_text(source.as_bytes())
        .ok()?
        .strip_prefix('\'')
        .and_then(|text| text.strip_suffix('\''))
        .map(str::to_owned)
}

fn shell_name(raw: &str) -> Option<String> {
    let path = Path::new(raw);
    match path.as_os_str().to_str() {
        Some("zsh" | "sh" | "bash") => path.to_str().map(str::to_owned),
        _ => {
            let stem = path.file_stem()?;
            let stem_path = Path::new(stem);
            (stem_path != path)
                .then(|| shell_name(stem.to_str()?))
                .flatten()
        }
    }
}

fn executable_name(raw: &str) -> Option<String> {
    let name = Path::new(raw).file_name()?.to_str()?;
    #[cfg(windows)]
    {
        let name = name.to_ascii_lowercase();
        for suffix in [".exe", ".cmd", ".bat", ".com"] {
            if let Some(stripped) = name.strip_suffix(suffix) {
                return Some(stripped.to_string());
            }
        }
        Some(name)
    }
    #[cfg(not(windows))]
    {
        Some(name.to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_string()).collect()
    }

    #[test]
    fn forced_rm_variants_are_dangerous() {
        for command in [
            argv(&["rm", "-f", "/tmp/example"]),
            argv(&["/bin/rm", "-fr", "/tmp/example"]),
            argv(&["rm", "-r", "-f", "/tmp/example"]),
            argv(&["rm", "--force", "/tmp/example"]),
            argv(&["rm", "/tmp/example", "-f"]),
            argv(&["sudo", "rm", "-rf", "/tmp/example"]),
            argv(&["env", "TARGET=/tmp/example", "rm", "-rf", "/tmp/example"]),
        ] {
            assert!(dangerous_command_match(&command), "{command:?}");
        }
    }

    const WRAPPERS: [&str; 14] = [
        "exec",
        "command",
        "nohup",
        "setsid",
        "doas",
        "sudo",
        "time",
        "eval",
        "xargs",
        "timeout 5",
        "nice -n 10",
        "stdbuf -o0",
        "env TARGET=/tmp/example",
        "find . -name example -exec",
    ];

    #[test]
    fn every_wrapper_before_a_forced_rm_is_dangerous() {
        for wrapper in WRAPPERS {
            let script = format!("{wrapper} rm -rf /tmp/example");
            assert!(
                dangerous_command_match(&argv(&["bash", "-lc", &script])),
                "{script}"
            );
            let flat = script.split(' ').collect::<Vec<_>>();
            assert!(dangerous_command_match(&argv(&flat)), "{flat:?}");
        }
    }

    #[test]
    fn every_wrapper_before_a_nested_shell_is_dangerous() {
        for wrapper in WRAPPERS {
            for tail in ["", " probe"] {
                let script = format!("{wrapper} sh -c 'rm -rf /tmp/example'{tail}");
                assert!(
                    dangerous_command_match(&argv(&["bash", "-lc", &script])),
                    "{script}"
                );
            }
        }
    }

    #[test]
    fn every_wrapper_before_a_benign_command_still_runs() {
        for wrapper in WRAPPERS {
            let script = format!("{wrapper} echo /tmp/example");
            assert!(
                !dangerous_command_match(&argv(&["bash", "-lc", &script])),
                "{script}"
            );
            let flat = script.split(' ').collect::<Vec<_>>();
            assert!(!dangerous_command_match(&argv(&flat)), "{flat:?}");
        }
    }

    #[test]
    fn eval_carrying_a_forced_rm_as_one_string_is_dangerous() {
        for command in [
            argv(&["eval", "rm -rf /tmp/example"]),
            argv(&["bash", "-lc", "eval 'rm -rf /tmp/example'"]),
        ] {
            assert!(dangerous_command_match(&command), "{command:?}");
        }
    }

    #[test]
    fn a_command_named_rm_that_is_a_subcommand_is_refused() {
        for command in [
            argv(&["git", "rm", "-f", "example"]),
            argv(&["docker", "rm", "-f", "example"]),
        ] {
            assert!(dangerous_command_match(&command), "{command:?}");
        }
    }

    #[test]
    fn nesting_beyond_the_bound_fails_closed() {
        let command = argv(&["rm", "-r", "/tmp/example"]);
        assert!(!dangerous_command_match_with_depth(
            &command,
            MAX_NESTING_DEPTH
        ));
        assert!(dangerous_command_match_with_depth(
            &command,
            MAX_NESTING_DEPTH + 1
        ));
    }

    #[test]
    fn forced_rm_in_complex_shell_syntax_is_dangerous() {
        for script in [
            "printf x | rm -rf /tmp/example",
            "if test -d /tmp/example; then rm --force /tmp/example; fi",
            "rm -rf \"$TARGET\" >/dev/null",
            "for target in /tmp/a /tmp/b; do rm -r -f \"$target\"; done",
            "echo \"$(rm -rf /tmp/example)\"",
            "bash -c 'rm -rf /tmp/example'",
            "trap 'rm -rf /tmp/example' EXIT",
        ] {
            let command = argv(&["bash", "-lc", script]);
            assert!(dangerous_command_match(&command), "{script}");
        }
    }

    #[test]
    fn nonforced_or_nonliteral_rm_is_allowed() {
        for command in [
            argv(&["rm", "-r", "/tmp/example"]),
            argv(&["rm", "--", "-f"]),
            argv(&["rm", "--", "-rf", "dist"]),
            argv(&["bash", "-lc", "rm -- -rf dist"]),
            argv(&["bash", "-lc", "R=rm; $R -rf dist"]),
            argv(&["bash", "-lc", "echo 'rm -rf /tmp/example'"]),
            argv(&["bash", "-lc", "cmd=rm; $cmd -rf /tmp/example"]),
            argv(&["bash", "-lc", "if then rm -rf /tmp/example"]),
            argv(&["env", "TARGET=/tmp/example", "rm", "-r", "/tmp/example"]),
            argv(&["bash", "-lc", "trap 'echo \"rm -rf /tmp/example\"' EXIT"]),
        ] {
            assert!(!dangerous_command_match(&command), "{command:?}");
        }
    }

    #[test]
    fn an_unquoted_forced_rm_carried_as_an_argument_is_refused() {
        for command in [
            argv(&["bash", "-lc", "echo rm -rf /tmp/example"]),
            argv(&["bash", "-lc", "trap 'echo rm -rf /tmp/example' EXIT"]),
        ] {
            assert!(dangerous_command_match(&command), "{command:?}");
        }
    }
}
