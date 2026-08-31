"""The provider-facing OAuth callback the shared fleet mounts.

The connector handoff's own return leg, drawn by the page every return leg shares
(`ufo.sdk.callback_page`). It states what landed and then that the member may close the page. It
never sends them off to prod an agent: consent is finished in a browser that carries only the sealed
state, and there is not always a conversation or an agent waiting behind it. Where the message did
reach the conversation the page adds that the conversation carries on, since that is the one case
this code knows a conversation is there to carry on. Either way the page asks the member for
nothing, so it takes the window away where the browser permits it.

The mark is the portal's own file, byte for byte, and core serves it here because this page is
reached with no session and no frontend build behind it. It was hand-minified once and drew
wrongly: the counters in the letters are cut by the `fill-rule: evenodd` its `<defs>` stylesheet
carries, and rounding the coordinates moved the shapes against each other. A logo is drawn artwork,
so it is copied rather than rewritten, and a test holds the two copies identical."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from ufo.runtime.access.grants import (
    ConnectStateInvalid,
    ConnectUnavailable,
    UnknownProvider,
    installed_connect_flow,
)
from ufo.sdk.callback_page import CLOSE_THIS_PAGE, CONVERSATION_CONTINUES, callback_page

CONNECT_CALLBACK_PATH = "/v1/connect/callback"
CONNECT_LOGO_FILE = Path(__file__).parent / "assets" / "ufo-logo.svg"
CONNECT_LOGO_CACHE = "public, max-age=31536000, immutable"

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
    return callback_page(
        headline=f"{named} connected.",
        detail=CONVERSATION_CONTINUES if recorded.resumed else CLOSE_THIS_PAGE,
        close=True,
    )


@callback_router.get("/connect/logo.svg")
async def connect_logo() -> Response:
    """The mark every return leg's page draws, served from core because those pages are reached with
    no session and no frontend build behind them, on this same origin. The web surface's copy is
    fingerprinted by its bundler, so its name changes every build and nothing may link to it."""
    return Response(
        content=CONNECT_LOGO_FILE.read_bytes(),
        media_type="image/svg+xml",
        headers={"cache-control": CONNECT_LOGO_CACHE},
    )
