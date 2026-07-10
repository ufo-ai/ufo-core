"""Pipedream Connect the connector broker: the async client, the provider registry, the errors.

Pipedream brokers managed OAuth (Connect Link hosted consent), pre-built provider actions, and a
Connect Proxy that injects the account's credential server-side — the token never leaves Pipedream,
so a grant stores only the connected-account id (`apn_…`), never a secret. Exactly the Composio
model with one difference that earns the second broker: Pipedream's Google client is verified for
restricted Gmail scopes, which Google blocks on Composio's shared client.

`connect_token` mints the hosted consent leg (pinning the success/error return legs);
`newest_account` and `connected_account` are the ownership reads — every lookup asserts the
account's `external_id` is this workspace's external user (the project token can read any account
in the project, so the assertion is the confused-deputy guard) and no token is ever read.
`list_actions`/`action_definition` are the catalog the dynamic tools search and describe;
`run_action` executes one server-side with the account bound through its component's app prop
(`authProvisionId`). The client speaks Pipedream's Connect REST API over httpx, authenticating
itself per call with a client-credentials access token cached process-wide; the deploy's OAuth
client and project are read loud from the environment (one Pipedream project per deploy, the analog
of the Composio key)."""

import json
import os
import time
from dataclasses import dataclass

import httpx

PIPEDREAM_API_BASE = "https://api.pipedream.com/v1"
PIPEDREAM_CLIENT_ID_ENV = "PIPEDREAM_CLIENT_ID"
PIPEDREAM_CLIENT_SECRET_ENV = "PIPEDREAM_CLIENT_SECRET"
PIPEDREAM_PROJECT_ID_ENV = "PIPEDREAM_PROJECT_ID"
PIPEDREAM_ENVIRONMENT_ENV = "PIPEDREAM_ENVIRONMENT"
DEFAULT_ENVIRONMENT = "production"
ENVIRONMENT_HEADER = "x-pd-environment"
EXTERNAL_USER_PREFIX = "ufo_"
PIPEDREAM_TIMEOUT_SECONDS = 30.0
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
ACTION_SEARCH_LIMIT = 10
MAX_RUN_ARGUMENTS_BYTES = 1024 * 1024
APP_PROP_TYPE = "app"


@dataclass(frozen=True)
class ConnectorSpec:
    """One brokered connector: the member-facing label, the Pipedream app slug whose managed OAuth
    grants the account, and the provider's own API `host` the derived grant admits and meters —
    direct-provider-host, never Pipedream's backend. `custom_oauth_env` names the env var that may
    hold the deploy's own OAuth client id (`oa_…`) registered with Pipedream; set, the consent leg
    rides that client instead of Pipedream's shared one (which Google's consent accepts for
    restricted Gmail scopes — a member org that blocks it connects through the deploy's own)."""

    label: str
    app: str
    host: str
    custom_oauth_env: str | None = None


CONNECTORS: dict[str, ConnectorSpec] = {
    "gmail": ConnectorSpec(
        "Gmail", "gmail", "gmail.googleapis.com", custom_oauth_env="PIPEDREAM_GMAIL_OAUTH_APP_ID"
    ),
}


class PipedreamError(RuntimeError):
    """A Pipedream API call failed or answered a shape the broker cannot use — fail loud, never a
    silent empty grant."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"pipedream {status}: {body}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ConnectToken:
    """One minted Connect token: the short-lived token itself and the hosted Connect Link URL the
    member's browser opens to run consent."""

    token: str
    connect_link_url: str


@dataclass(frozen=True)
class ConnectedAccount:
    """A connected account as the ownership check reads it: the stable `apn_…` id and the app slug
    it authenticates (an unhealthy or foreign account raises instead of returning)."""

    account_id: str
    app: str


_ACCESS_TOKENS: dict[str, tuple[str, float]] = {}


@dataclass(frozen=True)
class PipedreamClient:
    """Pipedream Connect over httpx. Each call opens and closes its own client so a transport
    override (a test's MockTransport) is honoured and no connection leaks; the client-credentials
    access token that authenticates this deploy to Pipedream is minted through `access_token` and
    cached process-wide until near expiry. The cache is unlocked — two concurrent cold-start mints
    both yield valid tokens and the last write wins, so a lock would buy nothing."""

    client_id: str
    client_secret: str
    project_id: str
    environment: str = DEFAULT_ENVIRONMENT
    transport: httpx.AsyncBaseTransport | None = None

    async def access_token(self) -> str:
        cached = _ACCESS_TOKENS.get(self.client_id)
        if cached is not None and cached[1] - TOKEN_EXPIRY_MARGIN_SECONDS > time.monotonic():
            return cached[0]
        async with self._http() as http:
            payload = _body(
                await http.post(
                    "/oauth/token",
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
            )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise PipedreamError(502, f"oauth token grant carried no access_token: {payload!r}")
        expires_in = payload.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
        _ACCESS_TOKENS[self.client_id] = (token, time.monotonic() + lifetime)
        return token

    async def connect_token(
        self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str
    ) -> ConnectToken:
        payload = await self._post(
            f"/connect/{self.project_id}/tokens",
            {
                "external_user_id": external_user_id,
                "success_redirect_uri": success_redirect_uri,
                "error_redirect_uri": error_redirect_uri,
            },
        )
        token = payload.get("token")
        link = payload.get("connect_link_url")
        if not isinstance(token, str) or not token or not isinstance(link, str) or not link:
            raise PipedreamError(502, f"connect token carried no token/link: {payload!r}")
        return ConnectToken(token=token, connect_link_url=link)

    async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount:
        """The account read the confused-deputy guard rides: the project token can read any account
        in the project, so ownership is asserted from the account's `external_id` — an account owned
        by another external user is refused before any grant binds or any page is fetched, and no
        token is ever read."""
        payload = await self._get(f"/connect/{self.project_id}/accounts/{account_id}")
        record = _dict(payload.get("data")) or payload
        return _owned_account(record, account_id, external_user_id)

    async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount:
        """The account a just-completed consent produced: the newest of `external_user_id`'s
        accounts on `app`. The list is scoped to the workspace's own external user, so a foreign
        account cannot be named into the lookup at all — the id never rides the untrusted return
        leg (Pipedream documents no redirect param carrying it)."""
        payload = await self._get(
            f"/connect/{self.project_id}/accounts",
            params={"external_user_id": external_user_id, "app": app},
        )
        accounts = payload.get("data")
        records = [_dict(record) for record in accounts] if isinstance(accounts, list) else []
        if not records:
            raise PipedreamError(404, f"no {app!r} account is connected for {external_user_id!r}")
        newest = max(records, key=lambda record: str(record.get("created_at") or ""))
        account_id = newest.get("id")
        if not isinstance(account_id, str) or not account_id:
            raise PipedreamError(502, f"connected account carried no id: {newest!r}")
        return _owned_account(newest, account_id, external_user_id)

    async def list_actions(
        self, app: str, query: str = "", limit: int = ACTION_SEARCH_LIMIT
    ) -> dict[str, object]:
        params = {"app": app, "limit": str(limit)}
        if query:
            params["q"] = query
        return await self._get(f"/connect/{self.project_id}/actions", params=params)

    async def action_definition(self, key: str) -> dict[str, object]:
        return await self._get(f"/connect/{self.project_id}/components/{key}")

    async def run_action(
        self,
        key: str,
        external_user_id: str,
        configured_props: dict[str, object],
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "id": key,
            "external_user_id": external_user_id,
            "configured_props": configured_props,
        }
        if len(json.dumps(body).encode()) > MAX_RUN_ARGUMENTS_BYTES:
            raise ValueError("connector tool arguments exceed the Pipedream run payload bound")
        return await self._post(f"/connect/{self.project_id}/actions/run", body)

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        token = await self.access_token()
        async with self._http(token) as http:
            return _body(await http.get(path, params=params))

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        token = await self.access_token()
        async with self._http(token) as http:
            return _body(await http.post(path, json=body))

    def _http(self, token: str | None = None) -> httpx.AsyncClient:
        """Every authenticated Connect call carries the bearer AND the environment header; the one
        unauthenticated call (`/oauth/token`) takes neither."""
        headers = (
            {"authorization": f"Bearer {token}", ENVIRONMENT_HEADER: self.environment}
            if token is not None
            else {}
        )
        return httpx.AsyncClient(
            base_url=PIPEDREAM_API_BASE,
            headers=headers,
            timeout=PIPEDREAM_TIMEOUT_SECONDS,
            transport=self.transport,
        )


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _owned_account(
    record: dict[str, object], account_id: str, external_user_id: str
) -> ConnectedAccount:
    """Assert `record` is `external_user_id`'s own healthy account and project it. `external_id`
    is Pipedream's echo of the external user the account was connected under — the one ownership
    fact the guard needs."""
    owner = record.get("external_id")
    if not isinstance(owner, str) or owner != external_user_id:
        raise PipedreamError(
            403,
            f"connected account {account_id!r} is owned by {owner!r}, not {external_user_id!r}",
        )
    if record.get("healthy") is False:
        raise PipedreamError(409, f"connected account {account_id!r} is unhealthy")
    app = _dict(record.get("app"))
    app_slug = app.get("name_slug")
    return ConnectedAccount(
        account_id=account_id, app=app_slug if isinstance(app_slug, str) else ""
    )


def _body(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        raise PipedreamError(response.status_code, response.text)
    if not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise PipedreamError(response.status_code, f"pipedream answered a non-object: {payload!r}")
    return payload


def pipedream_client() -> PipedreamClient:
    """The deploy's Pipedream Connect client, keyed from the environment. Raises when any of the
    OAuth client or project values is unset — a connector's consent leg cannot run without them, so
    the OAuth route and `exchange` fail loud rather than mint a link or a grant against no
    broker."""
    client_id = os.environ.get(PIPEDREAM_CLIENT_ID_ENV)
    client_secret = os.environ.get(PIPEDREAM_CLIENT_SECRET_ENV)
    project_id = os.environ.get(PIPEDREAM_PROJECT_ID_ENV)
    if not client_id or not client_secret or not project_id:
        raise RuntimeError(
            f"{PIPEDREAM_CLIENT_ID_ENV}, {PIPEDREAM_CLIENT_SECRET_ENV}, and "
            f"{PIPEDREAM_PROJECT_ID_ENV} are required to broker a connector's OAuth"
        )
    return PipedreamClient(
        client_id=client_id,
        client_secret=client_secret,
        project_id=project_id,
        environment=os.environ.get(PIPEDREAM_ENVIRONMENT_ENV, DEFAULT_ENVIRONMENT),
    )
