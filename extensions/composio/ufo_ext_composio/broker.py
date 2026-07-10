"""The Composio `ConnectorBroker`: the seam object every Composio connector shares.

Each method resolves the deploy's broker client per call (so a test's transport override is
honoured) and speaks for one provider by its toolkit slug. `tools`/`schema` project the Composio
catalog into `BrokerTool`s; `execute` runs a tool on Composio's server-side execute API under this
workspace's broker user — a 404 is augmented with the toolkit's real slugs so the model's next
attempt is informed, not another blind guess; `search` rides the Tool Router; `credential` confirms
the account is owned by this workspace's broker user (metadata, never a token — the confused-deputy
guard) and returns a `Credential` whose transport proxies provider HTTP through Composio's
proxy-execute, so a feed-sync source holds no secret."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import BrokerSearch, BrokerTool, UnknownBrokerTool
from ufo_ext_composio import client as composio
from ufo_ext_composio.proxy import ComposioProxyTransport

DISCOVERY_DESCRIPTION_CAP = 240
NOT_FOUND = 404


@dataclass(frozen=True)
class ComposioBroker:
    """Stateless — the broker client and its key are read per call, so no connection leaks and a
    test's transport override is honoured. See the module docstring for each method's contract."""

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        listed = await composio.composio_client().list_tools(_toolkit(provider), query)
        return _discovered_tools(listed)

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        try:
            payload = await composio.composio_client().tool_schema(slug)
        except composio.ComposioError as error:
            if error.status != NOT_FOUND:
                raise
            raise UnknownBrokerTool(slug) from error
        description = payload.get("description")
        input_schema = payload.get("input_schema")
        return BrokerTool(
            slug=slug,
            description=description if isinstance(description, str) else "",
            input_schema=input_schema if isinstance(input_schema, dict) else {},
        )

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        client = composio.composio_client()
        try:
            return await client.execute_tool(
                slug,
                dict(arguments),
                f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}",
                account_id,
                idempotency_key=idempotency_key,
            )
        except composio.ComposioError as error:
            if error.status != NOT_FOUND:
                raise
            raise await self._slug_miss(client, provider, slug, error) from error

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return await composio.search_connector_tools(
            composio.composio_client(), workspace_id, provider, query
        )

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
        client = composio.composio_client()
        await client.connected_account(account, broker_user)
        return Credential(
            transport=ComposioProxyTransport(
                api_base=composio.COMPOSIO_API_BASE,
                api_key=client.api_key,
                connected_account_id=account,
                inner=client.transport or httpx.AsyncHTTPTransport(),
            )
        )

    async def _slug_miss(
        self,
        client: composio.ComposioClient,
        provider: str,
        slug: str,
        error: composio.ComposioError,
    ) -> composio.ComposioError:
        """A 404 from execute, augmented with the toolkit's real tool slugs. Augmentation is
        best-effort: if the discovery lookup fails, the original 404 stands."""
        toolkit = _toolkit(provider)
        try:
            query = " ".join(dict.fromkeys(re.sub(r"[^a-z0-9]+", " ", slug.lower()).split()))
            tools = _discovered_tools(await client.list_tools(toolkit, query))
            if not tools and query:
                tools = _discovered_tools(await client.list_tools(toolkit, ""))
        except (composio.ComposioError, ValueError, KeyError):
            return error
        if not tools:
            return error
        names = ", ".join(tool.slug for tool in tools)
        return composio.ComposioError(
            error.status, f"{error.body} — tools available on {toolkit}: {names}"
        )


def _toolkit(provider: str) -> str:
    spec = composio.CONNECTORS.get(provider)
    return spec.toolkit if spec is not None else provider


def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]:
    """Project a Composio `list_tools` response to the connector's real slugs and short
    descriptions."""
    items = listed.get("items")
    tools: list[BrokerTool] = []
    if not isinstance(items, list):
        return ()
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("name")
        if not isinstance(slug, str) or not slug:
            continue
        description = item.get("description")
        tools.append(
            BrokerTool(
                slug=slug,
                description=description[:DISCOVERY_DESCRIPTION_CAP]
                if isinstance(description, str)
                else "",
            )
        )
    return tuple(tools)
