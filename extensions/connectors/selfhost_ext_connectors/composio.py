"""Composio as the connector OAuth broker: the async client, the provider registry, the errors.

selfhost holds a direct-provider-host grant — the egress proxy reaches the provider's own API host
and swaps a per-account sentinel for the real `Authorization: Bearer` token. Composio's job is only
the consent handoff: it runs a provider's managed OAuth and, once the member consents, exposes that
account's real access token, which selfhost then holds and injects itself. The client speaks
Composio's v3 REST API over httpx; the deploy's single broker key is read loud from the environment
(one Composio account per deploy, the analog of the model-provider key)."""

import os
from dataclasses import dataclass

import httpx

from selfhost.sdk.connectors import OAuthAccount

COMPOSIO_API_BASE = "https://backend.composio.dev/api/v3"
COMPOSIO_API_KEY_ENV = "COMPOSIO_API_KEY"
EXTERNAL_USER_PREFIX = "selfhost_"
COMPOSIO_TIMEOUT_SECONDS = 30.0
ACTIVE_STATUS = "ACTIVE"


@dataclass(frozen=True)
class ConnectorSpec:
    """One brokered connector: the member-facing label, the Composio toolkit slug whose managed
    OAuth grants the account, and the provider's own API `host` the derived grant admits and injects
    at the egress proxy — direct-provider-host, never Composio's backend."""

    label: str
    toolkit: str
    host: str


CONNECTORS: dict[str, ConnectorSpec] = {
    "github": ConnectorSpec("GitHub", "github", "api.github.com"),
    "gmail": ConnectorSpec("Gmail", "gmail", "gmail.googleapis.com"),
    "google_calendar": ConnectorSpec("Google Calendar", "googlecalendar", "www.googleapis.com"),
    "google_sheets": ConnectorSpec("Google Sheets", "googlesheets", "sheets.googleapis.com"),
    "google_drive": ConnectorSpec("Google Drive", "googledrive", "www.googleapis.com"),
    "slack": ConnectorSpec("Slack", "slack", "slack.com"),
    "notion": ConnectorSpec("Notion", "notion", "api.notion.com"),
    "linear": ConnectorSpec("Linear", "linear", "api.linear.app"),
    "asana": ConnectorSpec("Asana", "asana", "app.asana.com"),
    "stripe": ConnectorSpec("Stripe", "stripe", "api.stripe.com"),
    "hubspot": ConnectorSpec("HubSpot", "hubspot", "api.hubapi.com"),
    "calendly": ConnectorSpec("Calendly", "calendly", "api.calendly.com"),
    "intercom": ConnectorSpec("Intercom", "intercom", "api.intercom.io"),
    "airtable": ConnectorSpec("Airtable", "airtable", "api.airtable.com"),
    "monday": ConnectorSpec("Monday", "monday", "api.monday.com"),
    "pagerduty": ConnectorSpec("PagerDuty", "pagerduty", "api.pagerduty.com"),
    "sentry": ConnectorSpec("Sentry", "sentry", "sentry.io"),
    "typeform": ConnectorSpec("Typeform", "typeform", "api.typeform.com"),
    "klaviyo": ConnectorSpec("Klaviyo", "klaviyo", "a.klaviyo.com"),
    "clickup": ConnectorSpec("ClickUp", "clickup", "api.clickup.com"),
    "outlook": ConnectorSpec("Outlook", "outlook", "graph.microsoft.com"),
    "microsoft_teams": ConnectorSpec("Microsoft Teams", "microsoft_teams", "graph.microsoft.com"),
}


class ComposioError(RuntimeError):
    """A Composio API call failed or answered a shape the broker cannot use — fail loud, never a
    silent empty grant."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"composio {status}: {body}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ComposioClient:
    """Composio v3 over httpx, twinned across the consent handoff: `connect_link` mints the hosted
    OAuth link the member opens (ensuring the toolkit's managed auth config first), and
    `connected_account` reads back the account's real access token once the member has consented —
    but only after asserting the account is owned by `expected_user_id`, the workspace's brokered
    Composio user `connect_link` minted against, so a foreign account id (injected on the return
    leg) is refused before any token is read. Each call opens and closes its own client so a
    transport override (a test's MockTransport) is honoured and no connection leaks."""

    api_key: str
    transport: httpx.AsyncBaseTransport | None = None

    async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str:
        auth_config_id = await self._auth_config(toolkit)
        payload = await self._post(
            "/connected_accounts/link",
            {"auth_config_id": auth_config_id, "user_id": user_id, "callback_url": callback_url},
        )
        redirect = payload.get("redirect_url")
        if not isinstance(redirect, str) or not redirect:
            raise ComposioError(502, f"connect link carried no redirect_url: {payload!r}")
        return redirect

    async def connected_account(self, account_id: str, expected_user_id: str) -> OAuthAccount:
        payload = await self._get(f"/connected_accounts/{account_id}")
        owner = payload.get("user_id")
        if not isinstance(owner, str) or owner != expected_user_id:
            raise ComposioError(
                403,
                f"connected account {account_id!r} is owned by {owner!r}, not {expected_user_id!r}",
            )
        status = str(payload.get("status") or "").upper()
        if status != ACTIVE_STATUS:
            raise ComposioError(409, f"connected account {account_id!r} is {status or 'unknown'}")
        state = payload.get("state")
        val = state.get("val") if isinstance(state, dict) else None
        token = val.get("access_token") if isinstance(val, dict) else None
        if not isinstance(token, str) or not token:
            raise ComposioError(502, f"connected account {account_id!r} exposes no access token")
        return OAuthAccount(account_id=account_id, token=token)

    async def _auth_config(self, toolkit: str) -> str:
        existing = await self._get(
            "/auth_configs",
            params={"toolkit_slug": toolkit, "is_composio_managed": "true", "limit": "1"},
        )
        items = existing.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    return item["id"]
        created = await self._post(
            "/auth_configs",
            {"toolkit": {"slug": toolkit}, "auth_config": {"type": "use_composio_managed_auth"}},
        )
        record = created.get("auth_config")
        record = record if isinstance(record, dict) else created
        config_id = record.get("id")
        if not isinstance(config_id, str):
            raise ComposioError(502, f"auth config carried no id: {created!r}")
        return config_id

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        async with self._http() as http:
            return _body(await http.get(path, params=params))

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        async with self._http() as http:
            return _body(await http.post(path, json=body))

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=COMPOSIO_API_BASE,
            headers={"x-api-key": self.api_key},
            timeout=COMPOSIO_TIMEOUT_SECONDS,
            transport=self.transport,
        )


def _body(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        raise ComposioError(response.status_code, response.text)
    if not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise ComposioError(response.status_code, f"composio answered a non-object: {payload!r}")
    return payload


def composio_client() -> ComposioClient:
    """The deploy's Composio broker client, keyed from the environment. Raises when unset — a
    connector's consent leg cannot run without it, so the OAuth route and `exchange` fail loud
    rather than mint a link or a grant against no broker."""
    key = os.environ.get(COMPOSIO_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{COMPOSIO_API_KEY_ENV} is required to broker a connector's OAuth")
    return ComposioClient(api_key=key)
