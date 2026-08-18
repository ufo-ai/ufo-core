"""The provider-facing OAuth callback the shared fleet mounts.

The one member-facing page that answers with no session behind it. A member reaches it from
wherever they were talking — a Slack thread, the CLI — and the browser they finish consent in
carries nothing but the state the connect tool sealed, so the page can address them only by what
just happened. It says that, and the one thing left to do: close the tab when the agent already has
the work, ask for it when the message did not reach the conversation.

It carries no stylesheet, no font and no script, because the member is waiting on it in a browser
they opened for this one moment, often on a phone off a Slack thread. The mark is the one thing it
fetches, on the connection already open and cached for good after that: inlined it would be 20 KB
on a 600-byte page, and nothing in front of this deploy compresses a response. `color-scheme` is
what makes the page answer in the reader's own theme at no cost.

The mark is the portal's own file, byte for byte. It was hand-minified once and drew wrongly: the
counters in the letters are cut by the `fill-rule: evenodd` its `<defs>` stylesheet carries, and
rounding the coordinates moved the shapes against each other. A logo is drawn artwork, so it is
copied rather than rewritten, and a test holds the two copies identical."""

from html import escape
from pathlib import Path
from string import Template

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from ufo.grants import (
    ConnectStateInvalid,
    ConnectUnavailable,
    UnknownProvider,
    installed_connect_flow,
)

CONNECT_CALLBACK_PATH = "/v1/connect/callback"
CONNECT_LOGO_PATH = "/v1/connect/logo.svg"
CONNECT_LOGO_FILE = Path(__file__).parent / "assets" / "ufo-logo.svg"
CONNECT_LOGO_CACHE = "public, max-age=31536000, immutable"
CLOSE_THIS_PAGE = "You can close this page. The conversation continues."
ASK_TO_CONTINUE = "Return to your conversation and ask the agent to continue."
CONNECT_PAGE_MAX_BYTES = 1_024
CONNECT_PAGE = Template(
    "<!doctype html><html lang=en><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Connected</title><style>"
    ":root{color-scheme:light dark}"
    "body{font:16px/1.6 system-ui,sans-serif;margin:0;min-height:100svh;display:grid;"
    "place-content:center;padding:2rem;text-align:center}"
    "img{width:110px;height:auto;margin:0 auto 1.25rem;opacity:.9}"
    "@media(prefers-color-scheme:dark){img{filter:invert(1)}}"
    "h1{font:inherit;font-weight:600;margin:0 0 .4rem}"
    "p{margin:0;opacity:.65;max-width:26rem}"
    "</style>"
    '<img src="' + CONNECT_LOGO_PATH + '" alt=ufo width=110 height=28>'
    "<h1>$headline</h1><p>$detail</p></html>"
)

callback_router = APIRouter(prefix="/v1")


@callback_router.get("/connect/callback")
async def connect_callback(state: str = "", code: str = "") -> HTMLResponse:
    """Complete the OAuth handoff the provider redirects to: verify the sealed state, exchange the
    code for the broker-owned account, and land the grant. The grant a turn's `connect_account`
    began lands here. State-verified, not bearer-authenticated — the browser carries only the state
    the connect tool sealed with the speaking member, agent, and conversation."""
    try:
        flow = installed_connect_flow()
    except ConnectUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if not state or not code:
        raise HTTPException(400, "missing state or code")
    try:
        recorded = await flow.complete(state=state, code=code)
    except ConnectStateInvalid as error:
        raise HTTPException(400, str(error)) from error
    except UnknownProvider:
        raise HTTPException(404, "connector provider is not installed") from None
    named = (
        f"{recorded.label} · {recorded.account_label}"
        if recorded.account_label
        else (recorded.label)
    )
    return HTMLResponse(
        CONNECT_PAGE.substitute(
            headline=escape(f"{named} connected."),
            detail=CLOSE_THIS_PAGE if recorded.resumed else ASK_TO_CONTINUE,
        )
    )


@callback_router.get("/connect/logo.svg")
async def connect_logo() -> Response:
    """The mark the callback page draws, served from core because that page is core's own and is
    reached with no session and no frontend build behind it. The web surface's copy is fingerprinted
    by its bundler, so its name changes with every build and nothing may link to it."""
    return Response(
        content=CONNECT_LOGO_FILE.read_bytes(),
        media_type="image/svg+xml",
        headers={"cache-control": CONNECT_LOGO_CACHE},
    )
