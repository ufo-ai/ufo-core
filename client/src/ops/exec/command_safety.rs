// Derived from OpenAI Codex shell-command revision 2e4675919ee9c90a0b1360e0826fe7117d71cebb.
// Modified to retain only Bash forced-rm classification. Copyright 2025 OpenAI.
// Licensed under Apache-2.0; see client/licenses/openai-codex.txt.

use std::path::Path;

use tree_sitter::Node;
use tree_sitter::Parser;
use tree_sitter_bash::LANGUAGE as BASH;

const MAX_WRAPPER_DEPTH: usize = 8;

pub(super) fn dangerous_command_match(command: &[String]) -> bool {
    dangerous_command_match_with_depth(command, 0)
}

fn dangerous_command_match_with_depth(command: &[String], wrapper_depth: usize) -> bool {
    if wrapper_depth > MAX_WRAPPER_DEPTH {
        return true;
    }
    if dangerous_exec_match(command, wrapper_depth) {
        return true;
    }
    literal_shell_commands(command).is_some_and(|commands| {
        commands
            .iter()
            .any(|command| dangerous_command_match_with_depth(command, wrapper_depth + 1))
    })
}

fn dangerous_exec_match(command: &[String], wrapper_depth: usize) -> bool {
    match command
        .first()
        .and_then(|command| executable_name(command))
        .as_deref()
    {
        Some("rm") => rm_args_include_force_option(&command[1..]),
        Some("sudo") => dangerous_command_match_with_depth(&command[1..], wrapper_depth + 1),
        Some("env") => dangerous_env_match(command, wrapper_depth),
        Some("trap") => dangerous_trap_match(command, wrapper_depth),
        _ => false,
    }
}

fn dangerous_env_match(command: &[String], wrapper_depth: usize) -> bool {
    let mut command_index = 1;
    while let Some(argument) = command.get(command_index) {
        if argument == "--" {
            command_index += 1;
            break;
        }
        if matches!(argument.as_str(), "-i" | "--ignore-environment")
            || argument
                .split_once('=')
                .is_some_and(|(name, _)| !name.is_empty() && !name.starts_with('-'))
        {
            command_index += 1;
            continue;
        }
        break;
    }
    dangerous_command_match_with_depth(&command[command_index..], wrapper_depth + 1)
}

fn dangerous_trap_match(command: &[String], wrapper_depth: usize) -> bool {
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
    dangerous_command_match_with_depth(
        &["sh".to_string(), "-c".to_string(), action.clone()],
        wrapper_depth + 1,
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
    let [shell, flag, script] = command else {
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

    #[test]
    fn deeply_nested_wrappers_fail_closed() {
        for (depth, expected) in [(MAX_WRAPPER_DEPTH, true), (MAX_WRAPPER_DEPTH + 1, true)] {
            let command = std::iter::repeat_n("env", depth)
                .chain(["rm", "-rf", "/tmp/example"])
                .map(str::to_owned)
                .collect::<Vec<_>>();
            assert_eq!(dangerous_command_match(&command), expected, "{command:?}");
        }
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
            argv(&["bash", "-lc", "echo 'rm -rf /tmp/example'"]),
            argv(&["bash", "-lc", "cmd=rm; $cmd -rf /tmp/example"]),
            argv(&["bash", "-lc", "if then rm -rf /tmp/example"]),
            argv(&["env", "TARGET=/tmp/example", "rm", "-r", "/tmp/example"]),
            argv(&["bash", "-lc", "trap 'echo rm -rf /tmp/example' EXIT"]),
        ] {
            assert!(!dangerous_command_match(&command), "{command:?}");
        }
    }
}
