"""The Composio-backed OAuth provider and the browser bridge route its consent leg runs through.

selfhost's connect flow wants a synchronous `authorize_url` and a two-legged handoff, but minting a
Composio connect link is an async API call — so `authorize_url` points the member's browser at this
extension's `oauth` route instead. The route (async) mints the link and redirects on to Composio's
hosted consent; Composio redirects back to the same route with the connected-account id, which the
route hands to core's callback as the `code`. `exchange` then reads that account's real access
token. The flow reads top to bottom: authorize_url → oauth_route (start leg, then return leg) →
exchange."""

from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from selfhost.sdk.connectors import OAuthAccount
from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.http import Request, Response
from selfhost_ext_connectors import composio
from selfhost_ext_connectors.composio import CONNECTORS

OAUTH_ROUTE_PATH = "oauth"
OAUTH_ROUTE_MOUNT = "/ext/connectors/oauth"
COMPOSIO_ACCOUNT_PARAM = "connectedAccountId"
REDIRECT_STATUS = 302


@dataclass(frozen=True)
class ComposioOAuthProvider:
    """One provider's OAuth descriptor keyed into `serve`'s connect registry. `host` is the
    provider's own API host the derived grant admits; `toolkit` is the Composio managed-auth slug
    the consent leg opens. `authorize_url` is pure — it points the browser at the async `oauth`
    route — and `exchange` reads the consented account's real token from Composio."""

    provider: str
    host: str
    toolkit: str

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"provider": self.provider, "state": state, "callback": redirect_uri})
        return f"{_origin(redirect_uri)}{OAUTH_ROUTE_MOUNT}?{query}"

    async def exchange(self, code: str, redirect_uri: str) -> OAuthAccount:
        return await composio.composio_client().connected_account(code)


async def oauth_route(ctx: ExtensionContext, request: Request) -> Response:
    """The browser bridge, both legs. Start leg (no connected-account id yet): mint the Composio
    connect link for the requested provider's toolkit, scoped to this workspace's Composio user,
    and redirect the member to the hosted consent — telling Composio to return here. Return leg
    (Composio appended the connected-account id): redirect on to core's connect callback, handing
    the account id as the `code` core's `exchange` reads. The sealed `state` and core `callback`
    ride through untouched, so the grant still binds to the member, agent, and conversation."""
    params = request.query_params
    state = params.get("state", "")
    callback = params.get("callback", "")
    if not state or not callback:
        return Response(status_code=400, content="connect bridge is missing state or callback")
    account_id = params.get(COMPOSIO_ACCOUNT_PARAM, "")
    if account_id:
        landing = f"{callback}?{urlencode({'state': state, 'code': account_id})}"
        return Response(status_code=REDIRECT_STATUS, headers={"location": landing})
    provider = params.get("provider", "")
    spec = CONNECTORS.get(provider)
    if spec is None:
        return Response(status_code=404, content=f"unknown connector provider {provider!r}")
    return_url = (
        f"{_origin(callback)}{OAUTH_ROUTE_MOUNT}"
        f"?{urlencode({'provider': provider, 'state': state, 'callback': callback})}"
    )
    redirect = await composio.composio_client().connect_link(
        toolkit=spec.toolkit,
        user_id=f"{composio.EXTERNAL_USER_PREFIX}{ctx.store.workspace_id}",
        callback_url=return_url,
    )
    return Response(status_code=REDIRECT_STATUS, headers={"location": redirect})


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"connect callback {url!r} needs a scheme and host to bridge OAuth")
    return f"{parsed.scheme}://{parsed.netloc}"
