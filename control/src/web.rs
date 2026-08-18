//! The web presentation of the onboarding machine the terminal client drives.
//!
//! `GET /login` serves a self-contained sign-in page; `POST /v1/onboard/web` advances the identical
//! onboarding state machine (the claim row keyed by the onboarding session) and returns the
//! directive lines as JSON — a second renderer, never a second machine. The browser collects the
//! work email and the code inline, exactly as the terminal does: the machine's `say`/`ask`
//! directives render as the transcript and the next input, so the page reads and answers them and
//! never leaves for a hosted sign-in page. The session is the `__Host-ufo_onboard` cookie the
//! gateway mints and seals server-side — `__Host-`, so the browser keeps it host-only and refuses to
//! let a sibling host plant it. `POST /v1/onboard/web` mints a fresh one whenever the presented
//! cookie stands behind no live claim, so a claim is only ever started under a session minted here
//! and bound to this browser from the submit that starts it.
//!
//! The page itself is `login.html`, held beside this module and compiled in. It carries no
//! interpolation, so it stays HTML rather than becoming a string literal a reader has to unescape.

use serde::Serialize;

pub const WEB_CHANNEL: &str = "web";
pub const ONBOARD_SESSION_COOKIE: &str = "__Host-ufo_onboard";

pub const LOGIN_PAGE: &str = include_str!("login.html");

/// One directive line, as the page's JSON reads it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RenderedDirective {
    pub verb: String,
    pub fields: Vec<String>,
}

/// The directive lines as JSON-able records — the exact inverse of `directive()`'s escaping, so a
/// field's tabs and newlines survive the line framing.
pub fn parse_directives(payload: &[u8]) -> Vec<RenderedDirective> {
    String::from_utf8_lossy(payload)
        .split('\n')
        .filter(|line| !line.is_empty())
        .map(|line| {
            let mut parts = line.split('\t');
            let verb = parts.next().unwrap_or_default().to_string();
            RenderedDirective {
                verb,
                fields: parts.map(unescape).collect(),
            }
        })
        .collect()
}

/// A backslash escape the wire introduced, undone. A backslash before anything else is a literal
/// the member typed and survives as one, so `\d` stays `\d` rather than being swallowed.
fn unescape(field: &str) -> String {
    let mut out = String::with_capacity(field.len());
    let mut characters = field.chars().peekable();
    while let Some(character) = characters.next() {
        if character != '\\' {
            out.push(character);
            continue;
        }
        match characters.peek() {
            Some('\\') => {
                out.push('\\');
                characters.next();
            }
            Some('t') => {
                out.push('\t');
                characters.next();
            }
            Some('n') => {
                out.push('\n');
                characters.next();
            }
            _ => out.push('\\'),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::directives::{directive, render};

    #[test]
    fn a_line_parses_to_its_verb_and_fields() {
        let parsed = parse_directives(b"say\thello\n");
        assert_eq!(
            parsed,
            vec![RenderedDirective {
                verb: "say".to_string(),
                fields: vec!["hello".to_string()],
            }]
        );
    }

    #[test]
    fn a_verb_alone_carries_no_fields() {
        let parsed = parse_directives(b"install\n");
        assert_eq!(parsed[0].verb, "install");
        assert!(parsed[0].fields.is_empty());
    }

    #[test]
    fn blank_lines_are_dropped() {
        assert!(parse_directives(b"").is_empty());
        assert_eq!(parse_directives(b"say\tone\n\nsay\ttwo\n").len(), 2);
    }

    #[test]
    fn parsing_is_the_exact_inverse_of_the_wire_escaping() {
        // Every field that survives a round trip is one the browser renders as the member typed it.
        for field in [
            "plain",
            "a\tb",
            "a\nb",
            "a\\tb",
            "a\\\\b",
            "trailing\\",
            "\\",
            "mixed\t\nand\\more",
        ] {
            let wire = render(&[directive("say", &[field])]);
            let parsed = parse_directives(&wire);
            assert_eq!(
                parsed[0].fields[0], field,
                "round trip lost {field:?} as {:?}",
                parsed[0].fields[0]
            );
        }
    }

    #[test]
    fn multiple_fields_survive_together() {
        let wire = render(&[directive("choose", &["pick\tone", "a\nb", "c"])]);
        let parsed = parse_directives(&wire);
        assert_eq!(parsed[0].verb, "choose");
        assert_eq!(parsed[0].fields, vec!["pick\tone", "a\nb", "c"]);
    }

    #[test]
    fn the_login_page_is_self_contained_and_carries_the_craft() {
        // The ASCII craft is the mark and stays; a test is what holds it on the page.
        assert!(LOGIN_PAGE.starts_with("<!doctype html>"));
        assert!(LOGIN_PAGE.contains("</html>"));
        assert!(
            !LOGIN_PAGE.contains("http://") && !LOGIN_PAGE.contains("https://"),
            "the page must load nothing from another origin"
        );
    }
}
