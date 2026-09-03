"""The Pipedream-backed OAuth provider and the browser bridge route its consent leg runs through.

ufo's connect flow wants a synchronous `authorize_url` and a two-legged handoff, but minting a
Pipedream Connect token is an async API call — so `authorize_url` points the member's browser at
this extension's `oauth` route instead. The route (async) mints the token, pinning the success and
error redirects back to itself, and redirects on to Pipedream's hosted Connect Link scoped to the
provider's app — riding the deploy's own OAuth client when its env is set, else Pipedream's shared
one (verified live for Gmail's restricted scopes); a connector whose token the sandbox rides refuses
to mint consent on the shared client, since that account would release no token. Pipedream
documents no redirect param carrying
the account id, so each sealed connect state gets a distinct external user. The success leg resolves
that user's newest account and hands its id to core; `exchange` retrieves that exact id and
reasserts the state-scoped owner and app before a grant binds. Overlapping callbacks therefore
cannot select another flow's account.

`exchange` reads two things beside the id, and they fail differently on purpose. The account label
is cosmetic — a connection with no label works — so a failed read leaves it unnamed. The commit
identity is not: a connector whose token a sandbox commits with is unusable without one, and
swallowing that read would connect an account whose first commit dies on an empty ident with
nothing to say why. So it raises, and the member connects again.

The flow reads top to bottom: authorize_url → oauth_route (start leg, then return leg) →
exchange."""

import os
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse
from uuid import UUID

from ufo.sdk.connectors import OAuthAccount
from ufo.sdk.context import ExtensionContext
from ufo.sdk.http import Request, Response
from ufo_ext_pipedream import client as pipedream
from ufo_ext_pipedream.client import CONNECTORS
from ufo_ext_pipedream.token import commit_identity

OAUTH_ROUTE_PATH = "oauth"
OAUTH_ROUTE_MOUNT = "/ext/pipedream/oauth"
OUTCOME_PARAM = "outcome"
OUTCOME_CONNECTED = "connected"
OUTCOME_FAILED = "failed"
CONNECT_LINK_APP_PARAM = "app"
CONNECT_LINK_OAUTH_APP_PARAM = "oauthAppId"
CONNECT_LINK_POPUPS_PARAM = "popups"
REDIRECT_STATUS = 302
FAILED_CONSENT_STATUS = 502


@dataclass(frozen=True)
class PipedreamOAuthProvider:
    """One provider's OAuth descriptor keyed into `serve`'s connect registry. `host` is the
    provider's own API host the derived grant admits and meters; `app` is the Pipedream app slug
    the consent leg opens. `authorize_url` is pure — it points the browser at the async `oauth`
    route — and `exchange` binds only the exact account owned by this sealed state's external user.
    Connector calls execute through Pipedream server-side; the grant stores the account id, never a
    secret."""

    provider: str
    host: str
    app: str

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"provider": self.provider, "state": state, "callback": redirect_uri})
        return f"{_origin(redirect_uri)}{OAUTH_ROUTE_MOUNT}?{query}"

    async def exchange(
        self, code: str, _redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        external_user = pipedream.connection_user_id(workspace_id, state)
        client = pipedream.pipedream_client()
        account = await client.connected_account(code, external_user)
        if account.app != self.app:
            raise pipedream.PipedreamError(
                403, f"connected account {code!r} belongs to {account.app!r}, not {self.app!r}"
            )
        try:
            label = await client.account_label(account.account_id)
        except Exception:
            label = None
        spec = CONNECTORS.get(self.provider)
        commit = (
            None if spec is None else await commit_identity(spec, account.account_id, workspace_id)
        )
        return OAuthAccount(account_id=account.account_id, account_label=label, commit=commit)


async def oauth_route(ctx: ExtensionContext, request: Request) -> Response:
    """The browser bridge, both legs. Start leg (no outcome marker yet): mint a Connect token for
    this sealed state's external user with both return legs pinned to this route, and redirect the
    member to the hosted Connect Link scoped to the provider's app — in redirect mode
    (`popups=false`), so the one window the member opened rides through the provider's consent and
    back instead of the hosted page spawning a popup it then outlives. Return leg
    (`outcome=connected`): resolve that user's account and redirect its id to core; core retrieves
    the exact account and reasserts its owner and app. A failed consent is answered loud, never by
    re-minting consent. The sealed `state` and core `callback` ride through untouched, so the grant
    still binds to the member, agent, and conversation."""
    params = request.query_params
    state = params.get("state", "")
    callback = params.get("callback", "")
    if not state or not callback:
        return Response(status_code=400, content="connect bridge is missing state or callback")
    provider = params.get("provider", "")
    spec = CONNECTORS.get(provider)
    if spec is None:
        return Response(status_code=404, content=f"unknown connector provider {provider!r}")
    outcome = params.get(OUTCOME_PARAM, "")
    if outcome == OUTCOME_CONNECTED:
        account = await pipedream.pipedream_client().newest_account(
            pipedream.connection_user_id(ctx.store.workspace_id, state), spec.app
        )
        landing = f"{callback}?{urlencode({'state': state, 'code': account.account_id})}"
        return Response(status_code=REDIRECT_STATUS, headers={"location": landing})
    if outcome:
        return Response(
            status_code=FAILED_CONSENT_STATUS,
            content=f"connector consent did not complete (outcome {outcome!r}) — "
            "the account was not connected. Close this tab.",
        )
    bridge = f"{_origin(callback)}{OAUTH_ROUTE_MOUNT}"
    ride_through = {"provider": provider, "state": state, "callback": callback}
    token = await pipedream.pipedream_client().connect_token(
        external_user_id=pipedream.connection_user_id(ctx.store.workspace_id, state),
        success_redirect_uri=(
            f"{bridge}?{urlencode({**ride_through, OUTCOME_PARAM: OUTCOME_CONNECTED})}"
        ),
        error_redirect_uri=f"{bridge}?{urlencode({**ride_through, OUTCOME_PARAM: OUTCOME_FAILED})}",
    )
    link = (
        f"{token.connect_link_url}&"
        f"{urlencode({CONNECT_LINK_APP_PARAM: spec.app, CONNECT_LINK_POPUPS_PARAM: 'false'})}"
    )
    oauth_app_id = os.environ.get(spec.custom_oauth_env) if spec.custom_oauth_env else None
    if spec.cli_env is not None and not oauth_app_id:
        raise RuntimeError(
            f"{spec.custom_oauth_env} must be set to connect {provider!r}: Pipedream releases the "
            "token its CLI rides only for an account connected on this deploy's own OAuth client"
        )
    if oauth_app_id:
        link = f"{link}&{urlencode({CONNECT_LINK_OAUTH_APP_PARAM: oauth_app_id})}"
    return Response(status_code=REDIRECT_STATUS, headers={"location": link})


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"connect callback {url!r} needs a scheme and host to bridge OAuth")
    return f"{parsed.scheme}://{parsed.netloc}"
