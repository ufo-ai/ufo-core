"""The provider-facing OAuth callback the shared fleet mounts.

The connector handoff's own return leg, drawn by the page every return leg shares
(`ufo.sdk.callback_page`). It states what landed and then takes the member to their connectors,
where the account they just granted now stands. It never sends them off to prod an agent: consent
is finished in a browser that carries only the sealed state, and there is not always a conversation
or an agent waiting behind it.

The consent window the portal opened closes itself, as it always did: the member never navigated off
the screen behind it, and that screen has already moved on underneath them. Every other tab walked
here from a chat link, and a browser refuses to close one of those, so that tab is carried to the
connectors screen instead of being asked to shut itself. It arrives naming the account that landed,
because a screen reached by a fresh page load has nothing else to say what just happened.

A deploy with no public base URL or no browser surface has no connectors screen to reach, so the
page there states what landed and the one thing left to do: close the tab, and carry on in the
conversation where the message reached one.

The mark is the portal's own file, byte for byte, and core serves it here because this page is
reached with no session and no frontend build behind it. It was hand-minified once and drew
wrongly: the counters in the letters are cut by the `fill-rule: evenodd` its `<defs>` stylesheet
carries, and rounding the coordinates moved the shapes against each other. A logo is drawn artwork,
so it is copied rather than rewritten, and a test holds the two copies identical."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from ufo.runtime.access.grants import (
    ConnectStateInvalid,
    ConnectUnavailable,
    UnknownProvider,
    installed_connect_flow,
)
from ufo.sdk.callback_page import (
    CLOSE_THIS_PAGE,
    CONVERSATION_CONTINUES,
    PageLink,
    callback_page,
)

CONNECT_CALLBACK_PATH = "/v1/connect/callback"
CONNECT_LOGO_FILE = Path(__file__).parent / "assets" / "ufo-logo.svg"
CONNECT_LOGO_CACHE = "public, max-age=31536000, immutable"
CONNECTORS_SCREEN_FRAGMENT = "#/connectors"
CONNECTORS_SCREEN_LABEL = "Go to your connectors"
CONNECTED_PARAM = "connected"


def portal_url(public_base_url: str | None, home_surface: str | None) -> str | None:
    """The deploy's browser portal, or None where it has no public base or installs no browser
    surface — a self-host or dev node, which has no screen to send anyone to.

    Core owns both halves of the path: `/surface/<name>` is its own mount and `home_surface` is the
    manifest flag naming the one surface a browser belongs on. Composed once at boot and carried on
    the connect flow, because the callback answers a browser that holds no session to read it
    from."""
    if not public_base_url or home_surface is None:
        return None
    return f"{public_base_url.rstrip('/')}/surface/{home_surface}"


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
    if flow.portal_url:
        arrival = urlencode({CONNECTED_PARAM: named})
        return callback_page(
            headline=f"{named} connected.",
            link=PageLink(
                label=CONNECTORS_SCREEN_LABEL,
                url=f"{flow.portal_url}{CONNECTORS_SCREEN_FRAGMENT}",
            ),
            forward=f"{flow.portal_url}?{arrival}{CONNECTORS_SCREEN_FRAGMENT}",
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
