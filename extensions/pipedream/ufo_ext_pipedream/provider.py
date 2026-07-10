"""The Pipedream-backed OAuth provider and the browser bridge route its consent leg runs through.

ufo's connect flow wants a synchronous `authorize_url` and a two-legged handoff, but minting a
Pipedream Connect token is an async API call — so `authorize_url` points the member's browser at
this extension's `oauth` route instead. The route (async) mints the token, pinning the success and
error redirects back to itself, and redirects on to Pipedream's hosted Connect Link scoped to the
provider's app — riding the deploy's own OAuth client when its env is set, else Pipedream's shared
one (verified live for Gmail's restricted scopes). Pipedream documents no redirect param carrying
the account id, so the return
leg carries only this bridge's own outcome marker: the success leg hands core a fixed
`code` and `exchange` resolves the account server-side — the newest of this workspace's external
user's accounts on the app, ownership asserted from the account's `external_id` — so nothing an
attacker appends to the return leg can name a foreign account into the grant. The flow reads top to
bottom: authorize_url → oauth_route (start leg, then return leg) → exchange."""

import os
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse
from uuid import UUID

from ufo.sdk.connectors import OAuthAccount
from ufo.sdk.context import ExtensionContext
from ufo.sdk.http import Request, Response
from ufo_ext_pipedream import client as pipedream
from ufo_ext_pipedream.client import CONNECTORS

OAUTH_ROUTE_PATH = "oauth"
OAUTH_ROUTE_MOUNT = "/ext/pipedream/oauth"
OUTCOME_PARAM = "outcome"
OUTCOME_CONNECTED = "connected"
OUTCOME_FAILED = "failed"
CONNECT_LINK_APP_PARAM = "app"
CONNECT_LINK_OAUTH_APP_PARAM = "oauthAppId"
REDIRECT_STATUS = 302
FAILED_CONSENT_STATUS = 502


@dataclass(frozen=True)
class PipedreamOAuthProvider:
    """One provider's OAuth descriptor keyed into `serve`'s connect registry. `host` is the
    provider's own API host the derived grant admits and meters; `app` is the Pipedream app slug
    the consent leg opens. `authorize_url` is pure — it points the browser at the async `oauth`
    route — and `exchange` binds the account the completed consent produced, resolved server-side
    from this workspace's external user (never from the return leg). The account's token stays with
    Pipedream; connector calls execute through it server-side, so no secret is read or stored."""

    provider: str
    host: str
    app: str

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"provider": self.provider, "state": state, "callback": redirect_uri})
        return f"{_origin(redirect_uri)}{OAUTH_ROUTE_MOUNT}?{query}"

    async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID) -> OAuthAccount:
        external_user = f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}"
        account = await pipedream.pipedream_client().newest_account(external_user, self.app)
        return OAuthAccount(account_id=account.account_id)


async def oauth_route(ctx: ExtensionContext, request: Request) -> Response:
    """The browser bridge, both legs. Start leg (no outcome marker yet): mint a Connect token for
    this workspace's external user with both return legs pinned to this route, and redirect the
    member to the hosted Connect Link scoped to the provider's app. Return leg
    (`outcome=connected`): redirect on to core's connect callback, handing the fixed outcome as the
    `code` — core's `exchange` resolves the account server-side, so the return leg carries no
    account id to trust. A failed consent is answered loud, never by re-minting consent. The sealed
    `state` and core `callback` ride through untouched, so the grant still binds to the member,
    agent, and conversation."""
    params = request.query_params
    state = params.get("state", "")
    callback = params.get("callback", "")
    if not state or not callback:
        return Response(status_code=400, content="connect bridge is missing state or callback")
    outcome = params.get(OUTCOME_PARAM, "")
    if outcome == OUTCOME_CONNECTED:
        landing = f"{callback}?{urlencode({'state': state, 'code': OUTCOME_CONNECTED})}"
        return Response(status_code=REDIRECT_STATUS, headers={"location": landing})
    if outcome:
        return Response(
            status_code=FAILED_CONSENT_STATUS,
            content=f"connector consent did not complete (outcome {outcome!r}) — "
            "return to chat and ask the agent to connect again",
        )
    provider = params.get("provider", "")
    spec = CONNECTORS.get(provider)
    if spec is None:
        return Response(status_code=404, content=f"unknown connector provider {provider!r}")
    bridge = f"{_origin(callback)}{OAUTH_ROUTE_MOUNT}"
    ride_through = {"provider": provider, "state": state, "callback": callback}
    token = await pipedream.pipedream_client().connect_token(
        external_user_id=f"{pipedream.EXTERNAL_USER_PREFIX}{ctx.store.workspace_id}",
        success_redirect_uri=(
            f"{bridge}?{urlencode({**ride_through, OUTCOME_PARAM: OUTCOME_CONNECTED})}"
        ),
        error_redirect_uri=f"{bridge}?{urlencode({**ride_through, OUTCOME_PARAM: OUTCOME_FAILED})}",
    )
    link = f"{token.connect_link_url}&{urlencode({CONNECT_LINK_APP_PARAM: spec.app})}"
    oauth_app_id = os.environ.get(spec.custom_oauth_env) if spec.custom_oauth_env else None
    if oauth_app_id:
        link = f"{link}&{urlencode({CONNECT_LINK_OAUTH_APP_PARAM: oauth_app_id})}"
    return Response(status_code=REDIRECT_STATUS, headers={"location": link})


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"connect callback {url!r} needs a scheme and host to bridge OAuth")
    return f"{parsed.scheme}://{parsed.netloc}"
