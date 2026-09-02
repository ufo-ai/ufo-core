//! The web presentation of the onboarding machine the terminal client drives.
//!
//! `GET /login` serves a self-contained sign-in page to a browser holding no session, and forwards
//! one that already holds a session to the portal instead; `POST /v1/onboard/web` advances the
//! identical onboarding state machine (the claim row keyed by the onboarding session) and returns the
//! directive lines as JSON — a second renderer, never a second machine. The browser collects the
//! email and the code inline, exactly as the terminal does: the machine's `say`/`ask`
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
pub const ASSET_CACHE: &str = "public, max-age=31536000, immutable";

/// The mark alone, which the sign-in page draws in its head the way every page of the portal's
/// first run does — the same file the portal draws, copied in for the same reason the logo is.
pub const MARK_PATH: &str = "/login/mark.svg";
pub const MARK_BYTES: &[u8] = include_bytes!("assets/ufo-mark.svg");

/// The artwork the page draws in the half beside the form — the brand's `canon/after` illustration
/// `restaurant-door-two`, its 2048px master resampled to 1536px and encoded as WebP so the whole
/// panel is 83 KB. It is drawn at half a landscape page, so the square master is what the panel
/// crops from. The page names it in the style under the width that splits it rather than in the
/// markup, so a narrow page spends none of those bytes on a panel it does not draw.
pub const ILLUSTRATION_PATH: &str = "/login/sign-in.webp";
pub const ILLUSTRATION_BYTES: &[u8] = include_bytes!("assets/sign-in.webp");

/// The same mark as a raster, for the one reader that cannot have the vector: mail clients block
/// SVG, so an invitation drawing `LOGO_PATH` shows its recipient nothing. It is twice the size it
/// is drawn at, so it stays sharp where the pixels are doubled, and it carries the artwork alone —
/// a transparent ground lets the card behind it hold the colour.
pub const LOGO_PNG_PATH: &str = "/login/logo.png";
pub const LOGO_PNG_BYTES: &[u8] = include_bytes!("assets/ufo-logo.png");
pub const LOGO_WIDTH: u32 = 72;
pub const LOGO_HEIGHT: u32 = 18;

/// The cards a link unfurler draws: one for the marketing page, one for a hosted site's frame page.
/// An unfurler carries no session and follows one absolute URL, so a card cannot live behind the
/// portal's session gate or on a site's own `no-store` origin — it is compiled in and served beside
/// the mark, on the apex origin that already answers anonymously and caches for a year. Both files
/// are generated from the brand's own illustrations and the marketing page's faces, so this crate
/// reads the committed renders rather than keeping a second copy of either;
/// `assets/brand/share/README.md` holds the one command that reproduces them, and it writes them
/// here, beside the other compiled-in artwork, because the gateway image builds from `control` alone
/// and reaches nothing above it.
pub const SHARE_HOME_PATH: &str = "/share/og-home.jpg";
pub const SHARE_HOME_BYTES: &[u8] = include_bytes!("assets/og-home.jpg");
pub const SHARE_SITE_PATH: &str = "/share/og-site.jpg";
pub const SHARE_SITE_BYTES: &[u8] = include_bytes!("assets/og-site.jpg");

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

    fn styles(page: &str) -> &str {
        let opened = page
            .find("<style>")
            .expect("the page carries its own style");
        &page[opened..page.find("</style>").expect("the style block closes")]
    }

    #[test]
    fn the_login_page_fetches_its_mark_and_nothing_else() {
        // The page carries its own style and script and reaches for one element: the mark, on the
        // connection already open. Inlined, the artwork would be 20 KB on a 15 KB page. The
        // illustration is a background the style asks for and the Google mark is inline artwork, so
        // neither is an element the page fetches.
        assert!(LOGIN_PAGE.starts_with("<!doctype html>"));
        assert!(LOGIN_PAGE.contains("</html>"));
        assert!(
            !LOGIN_PAGE.contains("http://") && !LOGIN_PAGE.contains("https://"),
            "the page must load nothing from another origin"
        );
        assert_eq!(LOGIN_PAGE.matches("src=").count(), 1);
        assert!(LOGIN_PAGE.contains(&format!("<img src=\"{MARK_PATH}\" alt=\"ufo\"")));
    }

    #[test]
    fn a_page_with_no_half_for_the_illustration_never_asks_for_it() {
        // A browser fetches what a matching rule draws, so the artwork named only inside the split
        // width is 83 KB a phone never spends. Naming it in the markup instead would spend them:
        // an <img> in a display:none subtree is fetched all the same.
        assert!(!LOGIN_PAGE.contains(&format!("<img src=\"{ILLUSTRATION_PATH}\"")));
        let panel = styles(LOGIN_PAGE)
            .split("@media (min-width: 900px) {")
            .nth(1)
            .expect("the split width has its own block");
        assert!(
            panel.contains(&format!("url({ILLUSTRATION_PATH})")),
            "the artwork must be asked for inside the split width alone"
        );
    }

    #[test]
    fn the_illustration_takes_half_a_landscape_page_and_none_of_a_narrow_one() {
        // The form is what a member came for, so the artwork is drawn only where there is a half to
        // give it: the panel appears at the one width that splits the page, rounded and inset the
        // way the first run's welcome panel is, and it is undrawn below that width.
        assert!(LOGIN_PAGE.contains("#art { display: none; }"));
        assert!(LOGIN_PAGE.contains("@media (min-width: 900px) {"));
        assert!(LOGIN_PAGE.contains("#page { padding: 22px 0 22px 22px; }"));
        assert!(LOGIN_PAGE.find("<div id=\"art\">") < LOGIN_PAGE.find("<div id=\"form-side\">"));
        assert!(LOGIN_PAGE
            .contains("#art { display: block; flex: 1 1 0; min-width: 0; align-self: stretch;"));
        assert!(LOGIN_PAGE.contains("border-radius: var(--radius-panel);"));
        assert!(LOGIN_PAGE.contains("center / cover no-repeat; }"));
    }

    #[test]
    fn the_page_is_headed_and_centred_like_the_first_run() {
        // The sign-in is the screen before the first run, so it is drawn on the same page: a head
        // the mark leads at the height and inset every step of the run uses, and the form and the
        // panel centred in what is left of the viewport.
        assert!(LOGIN_PAGE.contains("grid-template-rows: 64px 1fr;"));
        assert!(
            LOGIN_PAGE.contains("header { display: flex; align-items: center; padding: 0 40px; }")
        );
        assert!(LOGIN_PAGE.contains("header img { display: block; width: 16px; height: 16px; }"));
        assert!(LOGIN_PAGE
            .contains("#page { display: flex; align-items: center; justify-content: center;"));
        assert!(LOGIN_PAGE.contains("--radius-answer: 16px;"));
        assert!(LOGIN_PAGE.contains("border-radius: 9999px;"));
    }

    #[test]
    fn the_form_stands_on_the_page_rather_than_in_a_card() {
        // Half a page is frame enough: the form is the only thing in its half, so a border around
        // it would draw a second edge inside the one the split already draws.
        assert!(!LOGIN_PAGE.contains("class=\"card\""));
        let board = "#board { display: flex; flex-direction: column; gap: 16px; }";
        assert!(LOGIN_PAGE.contains(board));
    }

    #[test]
    fn the_login_page_words_every_step_itself() {
        // The card states each step in its own words and reads the machine's question only to know
        // which step it stands on, so the terminal keeps its own wording and neither copies the
        // other's fragments.
        assert!(LOGIN_PAGE.contains("'Enter your email to continue.'"));
        assert!(LOGIN_PAGE
            .contains("const sent = 'Enter the verification code we sent to your email address';"));
        assert!(!LOGIN_PAGE.contains("Enter your email:"));
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
    fn the_portal_is_the_destination_unless_the_debug_surface_was_asked_for() {
        // The debugger directive says an operator MAY read the debug surface, never that this
        // sign-in is for it: an operator who asked for nothing lands in the portal like every other
        // member. The ask is a query parameter the operator surfaces bounce back here, so the click
        // that wanted the debug surface is the only thing that reaches it.
        assert!(LOGIN_PAGE.contains("const debug = params.get('debug') === '1';"));
        assert!(
            LOGIN_PAGE.contains("portal.action = debug && debuggerUrl ? debuggerUrl : portalUrl;")
        );
        assert!(
            !LOGIN_PAGE.contains("debuggerUrl || portalUrl"),
            "the debugger must never be the destination a sign-in falls back to"
        );
        assert!(LOGIN_PAGE.contains("if (debug) gq.set('debug', '1');"));
    }
}
