//! The one frame every message this deploy sends is drawn in.
//!
//! A caller writes words: a subject, paragraphs separated by a blank line, and at most one act. The
//! frame turns those into the exact bytes SES carries, in both renderings, so a campaign and a
//! balance notice cannot look like they came from two products.

use serde::Serialize;

use crate::web::LOGO_PNG_PATH;

/// SES replaces this with the one-click unsubscribe URL for the contact and topic the send names.
/// Without it in the body, `ListManagementOptions` adds the header and no visible link.
pub const UNSUBSCRIBE_PLACEHOLDER: &str = "{{amazonSESUnsubscribeUrl}}";

/// What a caller writes. `unsubscribe` is true for every topic a member may silence: SES replaces
/// the placeholder only for a send that names a contact list and topic, so a transactional message
/// carrying it would reach the member with the raw braces in it.
pub struct Words<'a> {
    pub subject: &'a str,
    pub body: &'a str,
    pub action_label: Option<&'a str>,
    pub action_url: Option<&'a str>,
    pub unsubscribe: bool,
}

/// The exact bytes the send carries, so a preview and the worker cannot disagree.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Message {
    pub subject: String,
    pub text: String,
    pub html: String,
}

pub fn render(words: &Words, apex_host: &str) -> Message {
    let action_text = match (words.action_label, words.action_url) {
        (Some(label), Some(url)) => format!("\n{label}: {url}\n"),
        _ => String::new(),
    };
    let action_html = match (words.action_label, words.action_url) {
        (Some(label), Some(url)) => ACTION_HTML
            .replace("{action_url}", &escaped(url))
            .replace("{action_label}", &escaped(label)),
        _ => String::new(),
    };
    let (footer_text, footer_html) = match words.unsubscribe {
        true => (
            format!("\nUnsubscribe: {UNSUBSCRIBE_PLACEHOLDER}\n"),
            FOOTER_HTML.replace("{unsubscribe}", UNSUBSCRIBE_PLACEHOLDER),
        ),
        false => (String::new(), String::new()),
    };
    let paragraphs = words
        .body
        .split("\n\n")
        .map(str::trim)
        .filter(|block| !block.is_empty())
        .map(|block| {
            format!(
                "<p style=\"margin:0 0 16px;font:15px/1.6 system-ui,sans-serif;color:#191A1A;\">{}</p>\n",
                escaped(block).replace('\n', "<br>")
            )
        })
        .collect::<String>();
    Message {
        subject: words.subject.to_string(),
        text: format!("{}\n{action_text}{footer_text}", words.body),
        html: FRAME_HTML
            .replace("{apex_host}", &escaped(apex_host))
            .replace("{logo_path}", LOGO_PNG_PATH)
            .replace("{paragraphs}", &paragraphs)
            .replace("{action}", &action_html)
            .replace("{footer}", &footer_html),
    }
}

pub fn escaped(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

const FRAME_HTML: &str = r##"<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#FAF9F7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#FAF9F7;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="440" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;max-width:440px;">
<tr><td align="center" style="padding-bottom:20px;">
<img src="https://{apex_host}{logo_path}" alt="ufo" width="72" height="18"
     style="display:block;border:0;width:72px;height:18px;"></td></tr>
<tr><td style="padding:20px;background:#FAF9F7;border:1px solid #EBEAE9;border-radius:4px;">
{paragraphs}{action}</td></tr>
{footer}</table>
</td></tr></table>
</body>
</html>
"##;

const ACTION_HTML: &str = r##"<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" bgcolor="#191A1A" style="border-radius:4px;">
<a href="{action_url}"
   style="display:block;padding:10px 18px;font:500 15px/1.5 system-ui,sans-serif;color:#FAF9F7;text-decoration:none;">{action_label}</a>
</td></tr></table>
"##;

const FOOTER_HTML: &str = r##"<tr><td align="center" style="padding-top:16px;">
<a href="{unsubscribe}"
   style="font:12px/1.5 system-ui,sans-serif;color:#919090;">Unsubscribe</a></td></tr>
"##;
