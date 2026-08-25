"""Pipedream Connect the connector broker: the async client, the provider registry, the errors.

Pipedream brokers managed OAuth (Connect Link hosted consent), pre-built provider actions, and a
Connect Proxy that injects the account's credential server-side — the token never leaves Pipedream,
so a grant stores only the connected-account id (`apn_…`), never a secret. Exactly the Composio
model; this second broker holds an explicit allowlist (`CONNECTORS`) of providers Composio's open
namespace does not serve: one whose consent Composio's shared client cannot pass (Gmail: Google
blocks restricted Gmail scopes, so the deploy's own Google OAuth client rides Pipedream Connect via
`custom_oauth_env`), or one Composio withholds by judgment where Pipedream's actions cover the gap
(Linear: `linear-search-issues` and `linear-list-workflow-states` reach issue state, which
Composio's toolkit cannot filter by; Attio: `attio-create-update-record` and the person/task/note
writes reach what Composio's read-only grant cannot; Discord: the `discord-send-message` family
posts to a channel, which Composio's identity-only user OAuth cannot). A provider no broker holds
managed auth for at all is not an entry here — it authenticates with a workspace key through the
`keyed_connectors` extension.

`connect_token` mints the hosted consent leg (pinning the success/error return legs);
`newest_account` and `connected_account` correlate its return to the state-scoped external user,
while `workspace_account` admits execution only for an account connected under that workspace.
The project token can read any account in the project, so these ownership assertions are the
confused-deputy guard; no provider token is ever read.
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
from hashlib import sha256
from uuid import UUID

import httpx

from ufo.sdk.connectors import GrantUnusable

PIPEDREAM_API_BASE = "https://api.pipedream.com/v1"
PIPEDREAM_CLIENT_ID_ENV = "PIPEDREAM_CLIENT_ID"
PIPEDREAM_CLIENT_SECRET_ENV = "PIPEDREAM_CLIENT_SECRET"
PIPEDREAM_PROJECT_ID_ENV = "PIPEDREAM_PROJECT_ID"
PIPEDREAM_ENVIRONMENT_ENV = "PIPEDREAM_ENVIRONMENT"
DEFAULT_ENVIRONMENT = "production"
ENVIRONMENT_HEADER = "x-pd-environment"
EXTERNAL_USER_PREFIX = "ufo_"
CONNECTION_ID_CHARS = 32
LOWER_HEX_DIGITS = frozenset("0123456789abcdef")
PIPEDREAM_TIMEOUT_SECONDS = 30.0
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
ACTION_PAGE_LIMIT = 100
MAX_LISTED_ACTIONS = 500
MAX_RUN_ARGUMENTS_BYTES = 1024 * 1024
APP_PROP_TYPE = "app"
STASH_NEW = "NEW"
FILESTASH_UPLOADS_EXPORT = "$filestash_uploads"
PIPEDREAM_TRANSFER_HOSTS = ("pipedream-file-stash-production.s3.us-east-1.amazonaws.com",)


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
    "attio": ConnectorSpec("Attio", "attio", "api.attio.com"),
    "discord": ConnectorSpec("Discord", "discord", "discord.com"),
    "gmail": ConnectorSpec(
        "Gmail", "gmail", "gmail.googleapis.com", custom_oauth_env="PIPEDREAM_GMAIL_OAUTH_APP_ID"
    ),
    "linear": ConnectorSpec("Linear", "linear", "api.linear.app"),
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
    """A healthy connected account with its stable id, app, and Pipedream external owner."""

    account_id: str
    app: str
    external_user_id: str


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

    async def account_label(self, account_id: str) -> str | None:
        payload = await self._get(f"/connect/{self.project_id}/accounts/{account_id}")
        record = _dict(payload.get("data")) or payload
        name = record.get("name")
        return name if isinstance(name, str) and name else None

    async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount:
        """Read an account granted to one of this workspace's connection users."""
        payload = await self._get(f"/connect/{self.project_id}/accounts/{account_id}")
        record = _dict(payload.get("data")) or payload
        account = _account(record, account_id)
        if not _workspace_owns_external_user(workspace_id, account.external_user_id):
            raise PipedreamError(
                403,
                f"connected account {account_id!r} is not owned by workspace {workspace_id}",
            )
        return account

    async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount:
        """The account a just-completed consent produced for one state-scoped external user."""
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

    async def list_actions(self, app: str, query: str = "") -> tuple[dict[str, object], ...]:
        """Every action row the app catalogs for `query`, following `page_info.end_cursor` to the
        end of the listing — an app publishes dozens of actions, so one page is a slice of
        Pipedream's own order and a real component key on page two would be invisible to discovery.
        The walk ends on a short page (Connect carries the last item's cursor on every page,
        including the last) or at `MAX_LISTED_ACTIONS` rows, which stops it even where the listing
        offers another cursor: past that count no caller renders the rows."""
        rows: list[dict[str, object]] = []
        after = ""
        while True:
            params = {"app": app, "limit": str(ACTION_PAGE_LIMIT)}
            if query:
                params["q"] = query
            if after:
                params["after"] = after
            payload = await self._get(f"/connect/{self.project_id}/actions", params=params)
            data = payload.get("data")
            page = (
                [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
            )
            rows.extend(page)
            end_cursor = _dict(payload.get("page_info")).get("end_cursor")
            if (
                len(page) < ACTION_PAGE_LIMIT
                or len(rows) >= MAX_LISTED_ACTIONS
                or not isinstance(end_cursor, str)
                or not end_cursor
            ):
                return tuple(rows[:MAX_LISTED_ACTIONS])
            after = end_cursor

    async def action_definition(self, key: str) -> dict[str, object]:
        return await self._get(f"/connect/{self.project_id}/components/{key}")

    async def run_action(
        self,
        key: str,
        external_user_id: str,
        configured_props: dict[str, object],
    ) -> dict[str, object]:
        """Run one action server-side, always under a fresh File Stash (`stash_id`): any file the
        action writes to its own `/tmp` syncs to the stash and comes back as a presigned URL in
        `exports.$filestash_uploads` — without it the response names container-local paths nothing
        outside Pipedream can read."""
        body: dict[str, object] = {
            "id": key,
            "external_user_id": external_user_id,
            "configured_props": configured_props,
            "stash_id": STASH_NEW,
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
    account = _account(record, account_id)
    if account.external_user_id != external_user_id:
        raise PipedreamError(
            403,
            f"connected account {account_id!r} is owned by {account.external_user_id!r}, "
            f"not {external_user_id!r}",
        )
    return account


def _account(record: dict[str, object], account_id: str) -> ConnectedAccount:
    """Project a Connect account record, refusing the two states it cannot authenticate under.

    `healthy: false` is Pipedream's own word for a grant whose token it can no longer refresh — a
    revoked consent, an expired refresh token, a password change. It answers that on every read of
    the account for as long as the state lasts, and nothing but the member reconnecting changes it,
    so it raises `GrantUnusable` rather than a broker fault: a caller retrying it every minute
    would spend a request a minute forever and alert an operator who cannot fix it."""
    owner = record.get("external_id")
    if not isinstance(owner, str) or not owner:
        raise PipedreamError(502, f"connected account {account_id!r} carried no external owner")
    if record.get("healthy") is False:
        raise GrantUnusable(
            f"pipedream cannot authenticate connected account {account_id!r}: it is unhealthy, so "
            "its grant needs the member to reconnect the account",
            awaits_grant=True,
        )
    app = _dict(record.get("app"))
    app_slug = app.get("name_slug")
    return ConnectedAccount(
        account_id=account_id,
        app=app_slug if isinstance(app_slug, str) else "",
        external_user_id=owner,
    )


def workspace_user_prefix(workspace_id: UUID) -> str:
    return f"{EXTERNAL_USER_PREFIX}{workspace_id.hex}_"


def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool:
    if external_user_id == f"{EXTERNAL_USER_PREFIX}{workspace_id}":
        return True
    prefix = workspace_user_prefix(workspace_id)
    if not external_user_id.startswith(prefix):
        return False
    connection_id = external_user_id.removeprefix(prefix)
    return len(connection_id) == CONNECTION_ID_CHARS and all(
        character in LOWER_HEX_DIGITS for character in connection_id
    )


def connection_user_id(workspace_id: UUID, state: str) -> str:
    connection_id = sha256(state.encode()).hexdigest()[:CONNECTION_ID_CHARS]
    return f"{workspace_user_prefix(workspace_id)}{connection_id}"


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
