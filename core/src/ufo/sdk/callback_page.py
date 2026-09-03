"""The page a provider's browser return leg answers with.

The one member-facing page that answers with no session behind it. A member reaches it from
wherever they were talking — a Slack thread, the CLI — and the browser they finish consent in
carries nothing but the state the connect tool sealed, so the page can address them only by what
just happened. It says that, and the one thing left to do. Every return leg draws it: the
connector callback core serves and the Slack install.

`CLOSE_THIS_PAGE` is the line any leg may say, because a return leg is often finished from a browser
with no conversation behind it at all — an install link opened from an email, a deploy that wires no
resumption. `CONVERSATION_CONTINUES` names a conversation on top of it, so only a leg that has
already told one may draw it.

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
conversation and leaves the tab behind them. `forward` decides between the two by asking which
window it is standing in, so the small window the portal opened over a screen takes itself away
while the tab a member walked here in is carried on to a screen. A page that still asks the member
for something takes none of the three, so the detail line stays in front of them.
"""

import json
from dataclasses import dataclass
from html import escape
from string import Template

from ufo.sdk.http import HTMLResponse

CONNECT_LOGO_PATH = "/v1/connect/logo.svg"
CLOSE_THIS_PAGE = "Close this tab."
CONVERSATION_CONTINUES = "Close this tab and return to the conversation."
CALLBACK_PAGE_MAX_BYTES = 1_024
CLOSE_AFTER_MS = 2_000
CLOSE_SCRIPT = f"<script>setTimeout(()=>window.close(),{CLOSE_AFTER_MS})</script>"
CONSENT_WINDOW_MARK = "ufo-consent-window"
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


def forward_script(url: str) -> str:
    """The script that takes the member off the page: the consent window the portal opened closes
    itself, and every other window goes to `url`.

    `CONSENT_WINDOW_MARK` is what tells the two apart. The portal writes it into its own session
    storage before it opens a consent window, and a window opened by `window.open` starts with a
    copy of that storage — measured in Chrome 151 to survive the whole consent walk, out to the
    provider's origin and back, and measured absent in a tab opened by a link, with or without an
    opener. Every window a member reaches from a chat surface is that second kind, so the mark is
    exactly the deploy's own consent window and nothing else.

    Neither of the two obvious readings would do. `window.opener` is gone by the time this page is
    read — a provider serving consent under `Cross-Origin-Opener-Policy` severs it, and a plain
    link-opened tab measured `false` as well. Asking for the close and forwarding whatever survives
    reads the browser's own policy instead of the fact wanted: Blink refuses `window.close` only for
    a window it did not open, and it counts a `target=_blank` tab as one it opened, so a member
    following a connect link from a chat surface in a browser tab would have that tab shut in their
    face rather than be carried anywhere.

    Storage a browser refuses to answer reads as no mark, which forwards: a member who cannot be
    carried anywhere is the one case worth getting wrong in the direction of a screen.

    The URL is written as a JavaScript string literal, with `<` escaped as well, so a URL holding a
    quote or a closing tag cannot leave the script."""
    literal = json.dumps(url).replace("<", "\\u003c")
    mark = json.dumps(CONSENT_WINDOW_MARK)
    return (
        "<script>setTimeout(()=>{let mine;"
        f"try{{mine=sessionStorage.getItem({mark})}}catch(e){{}}"
        f"if(mine)window.close();else location.replace({literal})}},{CLOSE_AFTER_MS})</script>"
    )


def callback_page(
    *,
    headline: str,
    detail: str = "",
    link: PageLink | None = None,
    status: int = 200,
    close: bool = False,
    forward: str = "",
) -> HTMLResponse:
    """The page for one return leg: what happened, then the one thing left to do — a `detail` line
    the member acts on themselves, or a `link` that acts for them. `close` takes the tab away where
    the browser permits it, and goes only on a page that asks the member for nothing. `forward`
    closes the deploy's own consent window and carries every other window to that URL, and
    supersedes `close`."""
    return HTMLResponse(
        CALLBACK_PAGE.substitute(
            headline=escape(headline),
            detail=f"<p>{escape(detail)}</p>" if detail else "",
            link_style=LINK_STYLE if link else "",
            link=f'<a href="{escape(link.url)}">{escape(link.label)}</a>' if link else "",
            close=forward_script(forward) if forward else (CLOSE_SCRIPT if close else ""),
        ),
        status_code=status,
    )
