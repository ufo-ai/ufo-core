"""The page a provider's browser return leg answers with.

The one member-facing page that answers with no session behind it. A member reaches it from
wherever they were talking — a Slack thread, the CLI — and the browser they finish consent in
carries nothing but the state the connect tool sealed, so the page can address them only by what
just happened. It says that, and the one thing left to do. Every return leg draws it: the
connector callback core serves, the Slack install, the GitHub App install.

It carries no stylesheet and no font, because the member is waiting on it in a browser they opened
for this one moment, often on a phone off a Slack thread. The mark is the one thing it fetches —
core's own copy, from the same origin every return leg is reached on, on the connection already
open and cached for good after that: inlined it would be 19 KB on a 700-byte page, and nothing in
front of this deploy compresses a response. `color-scheme` is what makes the page answer in the
reader's own theme at no cost, and the link is outlined in `currentColor` for the same reason.

Getting the member out of the tab takes both halves. `close` closes the tab from the document, and
a browser honours that only for a tab a script opened — the install walks the member from a chat
link through the provider to here, so that tab is usually theirs and the close is refused. `link`
is the half that always works, because a link needs no permission: it carries them back to the
conversation and leaves the tab behind them. A page that still asks the member for something takes
neither, so the detail line stays in front of them.
"""

from dataclasses import dataclass
from html import escape
from string import Template

from ufo.sdk.http import HTMLResponse

CONNECT_LOGO_PATH = "/v1/connect/logo.svg"
CLOSE_THIS_PAGE = "You can close this page. The conversation continues."
CALLBACK_PAGE_MAX_BYTES = 1_024
CLOSE_AFTER_MS = 2_000
CLOSE_SCRIPT = f"<script>setTimeout(()=>window.close(),{CLOSE_AFTER_MS})</script>"
LINK_STYLE = (
    "a{display:inline-block;margin-top:1.4rem;padding:.55rem 1.1rem;border:1px solid;"
    "border-radius:.35rem;color:inherit;text-decoration:none;font-weight:600}"
)
CALLBACK_PAGE = Template(
    "<!doctype html><html lang=en><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>$headline</title><style>"
    ":root{color-scheme:light dark}"
    "body{font:16px/1.6 system-ui,sans-serif;margin:0;min-height:100svh;display:grid;"
    "place-content:center;padding:2rem;text-align:center}"
    "img{width:110px;height:auto;margin:0 auto 1.25rem;opacity:.9}"
    "@media(prefers-color-scheme:dark){img{filter:invert(1)}}"
    "h1{font:inherit;font-weight:600;margin:0 0 .4rem}"
    "p{margin:0;opacity:.65;max-width:26rem}"
    "$link_style</style>"
    '<img src="' + CONNECT_LOGO_PATH + '" alt=ufo width=110 height=28>'
    "<h1>$headline</h1>$detail$link$close</html>"
)


@dataclass(frozen=True)
class PageLink:
    """The way back to the conversation: what the member reads, and where it takes them."""

    label: str
    url: str


def callback_page(
    *,
    headline: str,
    detail: str = "",
    link: PageLink | None = None,
    status: int = 200,
    close: bool = False,
) -> HTMLResponse:
    """The page for one return leg: what happened, then the one thing left to do — a `detail` line
    the member acts on themselves, or a `link` that acts for them. `close` takes the tab away where
    the browser permits it, and goes only on a page that asks the member for nothing."""
    return HTMLResponse(
        CALLBACK_PAGE.substitute(
            headline=escape(headline),
            detail=f"<p>{escape(detail)}</p>" if detail else "",
            link_style=LINK_STYLE if link else "",
            link=f'<a href="{escape(link.url)}">{escape(link.label)}</a>' if link else "",
            close=CLOSE_SCRIPT if close else "",
        ),
        status_code=status,
    )
