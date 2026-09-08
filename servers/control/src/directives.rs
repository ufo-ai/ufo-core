use std::collections::HashMap;

pub const PROMPT: &str = ">";
pub const CLIENT_VERSION_ENV: &str = "UFO_CLIENT_VERSION";

const INSTALLED_HEADER: &str = "x-ufo-installed";
const SCRIPT_HEADER: &str = "x-ufo-script";
const INSTALLED: &str = "1";

/// A field's own tab or newline would end the field or the line, so both are escaped and the backslash
/// that spells them is escaped first. A carriage return is dropped: `\r\n` would leave a stray byte.
pub fn directive(verb: &str, fields: &[&str]) -> Vec<u8> {
    let mut line = String::from(verb);
    for field in fields {
        line.push('\t');
        line.push_str(
            &field
                .replace('\\', "\\\\")
                .replace('\t', "\\t")
                .replace('\r', "")
                .replace('\n', "\\n"),
        );
    }
    line.push('\n');
    line.into_bytes()
}

pub fn render(lines: &[Vec<u8>]) -> Vec<u8> {
    lines.concat()
}

pub fn header_value<'a>(headers: &'a HashMap<String, String>, name: &str) -> Option<&'a str> {
    let lowered = name.to_ascii_lowercase();
    headers
        .iter()
        .find(|(key, _)| key.to_ascii_lowercase() == lowered)
        .map(|(_, value)| value.as_str())
}

/// `served` is read from `CLIENT_VERSION_ENV` once at startup and threaded here — empty in local dev,
/// where no version is served and no client is asked to update.
pub fn client_install(headers: &HashMap<String, String>, served: &str) -> Vec<u8> {
    if header_value(headers, INSTALLED_HEADER) != Some(INSTALLED) {
        return directive("install", &[]);
    }
    if !served.is_empty() && header_value(headers, SCRIPT_HEADER).unwrap_or("").trim() != served {
        return directive("install", &[]);
    }
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn headers(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(key, value)| (key.to_string(), value.to_string()))
            .collect()
    }

    #[test]
    fn a_verb_alone_is_one_line() {
        assert_eq!(directive("install", &[]), b"install\n");
    }

    #[test]
    fn fields_are_tab_separated() {
        assert_eq!(directive("say", &["hello"]), b"say\thello\n");
        assert_eq!(
            directive("choose", &["pick one", "a", "b"]),
            b"choose\tpick one\ta\tb\n"
        );
    }

    #[test]
    fn the_backslash_is_escaped_before_what_it_spells() {
        assert_eq!(directive("say", &["a\\tb"]), b"say\ta\\\\tb\n");
    }

    #[test]
    fn separators_inside_a_field_cannot_end_it() {
        assert_eq!(directive("say", &["a\tb"]), b"say\ta\\tb\n");
        assert_eq!(directive("say", &["a\nb"]), b"say\ta\\nb\n");
        assert_eq!(directive("say", &["a\r\nb"]), b"say\ta\\nb\n");
    }

    #[test]
    fn render_concatenates_in_order() {
        let screen = render(&[directive("say", &["one"]), directive("say", &["two"])]);
        assert_eq!(screen, b"say\tone\nsay\ttwo\n");
    }

    #[test]
    fn header_lookup_ignores_case() {
        let sent = headers(&[("X-Ufo-Installed", "1")]);
        assert_eq!(header_value(&sent, "x-ufo-installed"), Some("1"));
        assert_eq!(header_value(&sent, "x-ufo-script"), None);
    }

    #[test]
    fn an_uninstalled_client_is_told_to_install() {
        assert_eq!(client_install(&headers(&[]), ""), b"install\n");
        assert_eq!(
            client_install(&headers(&[("x-ufo-installed", "0")]), "0.1.21"),
            b"install\n"
        );
    }

    #[test]
    fn an_installed_client_is_left_alone_when_no_version_is_served() {
        assert!(client_install(&headers(&[("x-ufo-installed", "1")]), "").is_empty());
    }

    #[test]
    fn a_stale_installed_client_is_told_to_update() {
        let stale = headers(&[("x-ufo-installed", "1"), ("x-ufo-script", "0.1.20")]);
        assert_eq!(client_install(&stale, "0.1.21"), b"install\n");
    }

    #[test]
    fn a_current_installed_client_is_left_alone() {
        let current = headers(&[("x-ufo-installed", "1"), ("x-ufo-script", " 0.1.21 ")]);
        assert!(client_install(&current, "0.1.21").is_empty());
    }
}
