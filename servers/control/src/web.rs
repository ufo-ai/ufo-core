use serde::Serialize;

pub const WEB_CHANNEL: &str = "web";
pub const ONBOARD_SESSION_COOKIE: &str = "__Host-ufo_onboard";

pub const LOGIN_PAGE: &str = include_str!("login.html");

pub const LOGO_PATH: &str = "/login/logo.svg";
pub const LOGO_BYTES: &[u8] = include_bytes!("assets/ufo-logo.svg");
pub const ASSET_CACHE: &str = "public, max-age=31536000, immutable";

pub const MARK_PATH: &str = "/login/mark.svg";
pub const MARK_BYTES: &[u8] = include_bytes!("assets/ufo-mark.svg");

pub const ILLUSTRATION_PATH: &str = "/login/sign-in.webp";
pub const ILLUSTRATION_BYTES: &[u8] = include_bytes!("assets/sign-in.webp");

pub const LOGO_PNG_PATH: &str = "/login/logo.png";
pub const LOGO_PNG_BYTES: &[u8] = include_bytes!("assets/ufo-logo.png");
pub const LOGO_WIDTH: u32 = 72;
pub const LOGO_HEIGHT: u32 = 18;

pub const SHARE_HOME_PATH: &str = "/share/og-home.jpg";
pub const SHARE_HOME_BYTES: &[u8] = include_bytes!("assets/og-home.jpg");
pub const SHARE_SITE_PATH: &str = "/share/og-site.jpg";
pub const SHARE_SITE_BYTES: &[u8] = include_bytes!("assets/og-site.jpg");

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RenderedDirective {
    pub verb: String,
    pub fields: Vec<String>,
}

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
        assert!(!LOGIN_PAGE.contains("class=\"card\""));
        let board = "#board { display: flex; flex-direction: column; gap: 16px; }";
        assert!(LOGIN_PAGE.contains(board));
    }

    #[test]
    fn the_login_page_words_every_step_itself() {
        assert!(LOGIN_PAGE.contains("'Enter your email to continue.'"));
        assert!(LOGIN_PAGE
            .contains("const sent = 'Enter the verification code we sent to your email address';"));
        assert!(!LOGIN_PAGE.contains("Enter your email:"));
        assert!(!LOGIN_PAGE.contains("Enter the code:"));
    }

    #[test]
    fn the_code_step_takes_only_a_whole_code_and_sends_it_without_a_second_act() {
        assert!(LOGIN_PAGE.contains("const SLOTS = 6;"));
        assert!(LOGIN_PAGE.contains(r"answer.value.replace(/\D/g, '').slice(0, SLOTS)"));
        assert!(LOGIN_PAGE.contains("go.disabled = answer.value.length !== SLOTS;"));
        assert!(LOGIN_PAGE.contains("promptRow.requestSubmit();"));
    }

    #[test]
    fn the_login_page_states_every_refusal_in_one_alert() {
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
