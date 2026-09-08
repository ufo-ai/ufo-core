"""The Composio-backed OAuth provider and the browser bridge route its consent leg runs through.

ufo's connect flow wants a synchronous `authorize_url` and a two-legged handoff, but minting a
Composio connect link is an async API call — so `authorize_url` points the member's browser at this
extension's `oauth` route instead. The route (async) mints the link and redirects on to Composio's
hosted consent; Composio redirects back to the same route appending `connected_account_id` (and
`status`) to the callback it was given, and the route hands that id to core's callback as the
`code`. `exchange` then confirms that account's ownership and toolkit before binding it. The flow
reads top to bottom: authorize_url → oauth_route (start leg, then return leg) → exchange."""

from dataclasses import dataclass
from urllib.parse import urlencode, urlparse
from uuid import UUID

from ufo.sdk.connectors import OAuthAccount
from ufo.sdk.context import ExtensionContext
from ufo.sdk.http import Request, Response
from ufo_ext_composio import client as composio

OAUTH_ROUTE_PATH = "oauth"
OAUTH_ROUTE_MOUNT = "/ext/composio/oauth"
COMPOSIO_ACCOUNT_PARAM = "connected_account_id"
COMPOSIO_STATUS_PARAM = "status"
REDIRECT_STATUS = 302
FAILED_CONSENT_STATUS = 502
NO_CONSENT_LINK_STATUS = 502
BROKER_ERROR_CAP = 400


@dataclass(frozen=True)
class ComposioOAuthProvider:
    """One provider's OAuth descriptor keyed into `serve`'s connect registry. `provider` is the
    Composio managed-auth slug the consent leg opens; `host` is the provider's own API host the
    derived grant admits and meters (empty for a brokered grant, which admits no provider host since
    tools execute server-side). `authorize_url` is pure — it points the browser at the
    async `oauth` route — and `exchange` binds the consented account (by its id) once Composio
    confirms it is owned by this workspace's brokered user — the same `EXTERNAL_USER_PREFIX`-scoped
    id `oauth_route` minted the consent link against — so a foreign account id injected on the
    return leg binds no grant. The account's token stays with Composio; connector tools execute
    through it server-side, so no secret is read or stored."""

    provider: str
    host: str

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"provider": self.provider, "state": state, "callback": redirect_uri})
        return f"{_origin(redirect_uri)}{OAUTH_ROUTE_MOUNT}?{query}"

    async def exchange(
        self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str
    ) -> OAuthAccount:
        expected_user = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
        client = composio.composio_client()
        account = await client.connected_account(code, expected_user, self.provider)
        try:
            label = await client.account_label(code)
        except Exception:
            label = None
        return OAuthAccount(account_id=account.account_id, account_label=label)


async def oauth_route(ctx: ExtensionContext, request: Request) -> Response:
    """The browser bridge, both legs. Start leg (no Composio-appended params yet): mint the
    Composio connect link for the requested provider's toolkit, scoped to this workspace's Composio
    user, and redirect the member to the hosted consent — telling Composio to return here. Return
    leg (Composio appended `connected_account_id`): redirect on to core's connect callback, handing
    the account id as the `code` core's `exchange` reads. A return leg carrying `status` but no
    account id is a consent that did not complete — answered loud, never by re-minting consent. The
    sealed `state` and core `callback` ride through untouched, so the grant still binds to the
    member, agent, and conversation.

    A broker that cannot mint the link answers with what Composio said, on the page the member is
    already looking at: the failure names the toolkit and Composio's own words (the auth config the
    project does not hold, a refused request), which is what an operator needs to repair the
    deploy. An unanswered `ComposioError` reaches the member as a bare Internal Server Error that
    names nothing."""
    params = request.query_params
    state = params.get("state", "")
    callback = params.get("callback", "")
    if not state or not callback:
        return Response(status_code=400, content="connect bridge is missing state or callback")
    account_id = params.get(COMPOSIO_ACCOUNT_PARAM, "")
    if account_id:
        landing = f"{callback}?{urlencode({'state': state, 'code': account_id})}"
        return Response(status_code=REDIRECT_STATUS, headers={"location": landing})
    status = params.get(COMPOSIO_STATUS_PARAM, "")
    if status:
        return Response(
            status_code=FAILED_CONSENT_STATUS,
            content=f"connector consent did not complete (status {status!r}) — "
            "the account was not connected. Close this tab.",
        )
    provider = params.get("provider", "")
    if not provider:
        return Response(status_code=404, content="connect bridge is missing a provider")
    return_url = (
        f"{_origin(callback)}{OAUTH_ROUTE_MOUNT}"
        f"?{urlencode({'provider': provider, 'state': state, 'callback': callback})}"
    )
    try:
        redirect = await composio.composio_client().connect_link(
            toolkit=provider,
            user_id=f"{composio.EXTERNAL_USER_PREFIX}{ctx.store.workspace_id}",
            callback_url=return_url,
        )
    except composio.ComposioError as error:
        return Response(
            status_code=NO_CONSENT_LINK_STATUS,
            content=f"{provider} cannot be connected on this deploy: "
            f"{error.body[:BROKER_ERROR_CAP]} Close this tab and tell an admin.",
        )
    return Response(status_code=REDIRECT_STATUS, headers={"location": redirect})


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"connect callback {url!r} needs a scheme and host to bridge OAuth")
    return f"{parsed.scheme}://{parsed.netloc}"
