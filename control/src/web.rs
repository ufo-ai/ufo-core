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
//! interpolation, so it stays HTML rather than becoming a string literal a reader has to
//! unescape. It words its own steps and reads the machine's question only to know which step
//! is standing, so the terminal keeps its own wording and neither surface is worded for the
//! other. It fetches one file, the mark, and carries its style and script itself.

use serde::Serialize;

pub const WEB_CHANNEL: &str = "web";
pub const ONBOARD_SESSION_COOKIE: &str = "__Host-ufo_onboard";

pub const LOGIN_PAGE: &str = include_str!("login.html");

/// The mark both sign-in pages draw, compiled in and served rather than inlined: the artwork is
/// 20 KB against a 15 KB page, and nothing in front of this deploy compresses a response. It is the
/// portal's own file byte for byte — a logo is drawn artwork, so it is copied rather than rewritten,
/// and a test holds the two copies identical.
pub const LOGO_PATH: &str = "/login/logo.svg";
pub const LOGO_BYTES: &[u8] = include_bytes!("assets/ufo-logo.svg");
pub const LOGO_CACHE: &str = "public, max-age=31536000, immutable";

/// The same mark as a raster, for the one reader that cannot have the vector: mail clients block
/// SVG, so an invitation drawing `LOGO_PATH` shows its recipient nothing. It is twice the size it
/// is drawn at, so it stays sharp where the pixels are doubled, and it carries the artwork alone —
/// a transparent ground lets the card behind it hold the colour.
pub const LOGO_PNG_PATH: &str = "/login/logo.png";
pub const LOGO_PNG_BYTES: &[u8] = include_bytes!("assets/ufo-logo.png");
pub const LOGO_WIDTH: u32 = 72;
pub const LOGO_HEIGHT: u32 = 18;

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
    fn the_login_page_fetches_its_mark_and_nothing_else() {
        // The page carries its own style and script and reaches for one file: the mark, on the
        // connection already open. Inlined, the artwork would be 20 KB on a 15 KB page.
        assert!(LOGIN_PAGE.starts_with("<!doctype html>"));
        assert!(LOGIN_PAGE.contains("</html>"));
        assert!(
            !LOGIN_PAGE.contains("http://") && !LOGIN_PAGE.contains("https://"),
            "the page must load nothing from another origin"
        );
        assert_eq!(LOGIN_PAGE.matches("src=").count(), 1);
        assert!(LOGIN_PAGE.contains(&format!("<img src=\"{LOGO_PATH}\" alt=\"ufo\"")));
    }

    #[test]
    fn the_login_page_words_every_step_itself() {
        // The card states each step in its own words and reads the machine's question only to know
        // which step it stands on, so the terminal keeps its own wording and neither copies the
        // other's fragments.
        assert!(LOGIN_PAGE.contains("'Enter your work email to continue.'"));
        assert!(LOGIN_PAGE
            .contains("const sent = 'Enter the verification code we sent to your email address';"));
        assert!(!LOGIN_PAGE.contains("Enter your work email:"));
        assert!(!LOGIN_PAGE.contains("Enter the code:"));
    }

    #[test]
    fn the_code_step_takes_only_a_whole_code_and_sends_it_without_a_second_act() {
        // A code is digits and nothing else; the act stays shut until the row is whole, and a whole
        // row commits itself once, so a correction after a refusal is the member's own to send.
        assert!(LOGIN_PAGE.contains("const SLOTS = 6;"));
        assert!(LOGIN_PAGE.contains(r"answer.value.replace(/\D/g, '').slice(0, SLOTS)"));
        assert!(LOGIN_PAGE.contains("go.disabled = answer.value.length !== SLOTS;"));
        assert!(LOGIN_PAGE.contains("promptRow.requestSubmit();"));
    }

    #[test]
    fn the_login_page_states_every_refusal_in_one_alert() {
        // Which lines are a refusal comes off the step order rather than the sentence: a machine
        // that did not move the member on refused what they sent.
        assert!(LOGIN_PAGE.contains(r#"<div id="alert" role="alert"></div>"#));
        assert!(LOGIN_PAGE.contains(
            "if (carried && (previous ? stated.order <= previous.order : stated.order > 0)) {"
        ));
        assert!(LOGIN_PAGE.contains("banner.textContent = said();"));
        assert!(!LOGIN_PAGE.contains("Sign in again"));
    }

    #[test]
    fn a_completed_sign_in_posts_the_session_and_opens_the_requested_route() {
        assert!(!LOGIN_PAGE.contains("Open your workspace"));
        assert!(!LOGIN_PAGE.contains("<h1>Signed in</h1>"));
        assert!(LOGIN_PAGE.contains(
            "const firstRun = location.hash === '#/first-run' || params.get('first') === '1';"
        ));
        assert!(LOGIN_PAGE.contains("if (firstRun) gq.set('first', '1');"));
        assert!(LOGIN_PAGE.contains("const portalUrl = workspace + '/surface/web' +"));
        assert!(LOGIN_PAGE.contains("founding || firstRun ? '?first=1' : ''"));
        assert!(LOGIN_PAGE.contains("portal.requestSubmit();"));
    }

    #[test]
    fn a_requested_portal_route_precedes_the_operator_debugger() {
        assert!(LOGIN_PAGE.contains(
            "portal.action = target || artifact || firstRun ? portalUrl : debuggerUrl || portalUrl;"
        ));
    }
}
