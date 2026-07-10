"""The Pipedream `ConnectorBroker`: the seam object every Pipedream connector shares.

Each method resolves the deploy's Connect client per call (so a test's transport override is
honoured) and speaks for one provider by its app slug. `tools`/`schema` project the app's pre-built
actions into `BrokerTool`s — an action's input schema is derived from its `configurable_props`,
with the app slot (the connected account) held back for the broker to bind itself; `execute` runs
an action on Pipedream's server-side run API with the granted account bound through that slot's
`authProvisionId` — an unknown key is augmented with the app's real keys so the model's next
attempt is informed, and an action-level error raises loud; `search` is the same catalog search
(Pipedream has no router, so plan/guidance stay empty); `credential` confirms the account is owned
by this workspace's external user and connected to this provider's app (metadata, never a token —
the confused-deputy guard) and returns a `Credential` whose transport proxies provider HTTP through
the Connect Proxy, so a feed-sync source holds no secret."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import BrokerSearch, BrokerTool, UnknownBrokerTool
from ufo_ext_pipedream import client as pipedream
from ufo_ext_pipedream.client import APP_PROP_TYPE, ConnectorSpec, PipedreamError
from ufo_ext_pipedream.proxy import PipedreamProxyTransport

NOT_FOUND = 404
_INTERNAL_PROP_TYPES = frozenset({APP_PROP_TYPE, "dir"})
_PROP_TYPE_TO_JSON = {
    "string": "string",
    "boolean": "boolean",
    "integer": "integer",
    "object": "object",
    "string[]": "array",
    "integer[]": "array",
}


@dataclass(frozen=True)
class PipedreamBroker:
    """Stateless — the Connect client and its credentials are read per call, so no connection leaks
    and a test's transport override is honoured. See the module docstring for each method's
    contract."""

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        """Pipedream's `q` is a strict term match — a multi-word query routinely answers empty — so
        an empty queried answer falls back to the app's unqueried top actions, never a dead end."""
        client = pipedream.pipedream_client()
        app = _spec(provider).app
        found = _listed_tools(await client.list_actions(app, query))
        if not found and query:
            found = _listed_tools(await client.list_actions(app, ""))
        return found

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        definition = await self._definition(slug)
        return BrokerTool(
            slug=slug,
            description=_str(definition.get("description")),
            input_schema=_input_schema(_props(definition)),
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
        client = pipedream.pipedream_client()
        try:
            definition = await self._definition(slug)
        except UnknownBrokerTool as missing:
            raise await self._key_miss(client, provider, slug) from missing
        configured: dict[str, object] = dict(arguments)
        configured[_app_slot(definition, slug)] = {"authProvisionId": account_id}
        response = await client.run_action(
            slug, f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}", configured
        )
        error = response.get("error")
        if error:
            raise PipedreamError(502, json.dumps(error))
        return response

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return BrokerSearch(tools=await self.tools(workspace_id, provider, query))

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        spec = _spec(provider)
        external_user = f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}"
        client = pipedream.pipedream_client()
        connected = await client.connected_account(account, external_user)
        if connected.app != spec.app:
            raise PipedreamError(
                409,
                f"connected account {account!r} authenticates {connected.app!r}, not {spec.app!r}",
            )
        return Credential(
            transport=PipedreamProxyTransport(
                client=client,
                account_id=account,
                external_user_id=external_user,
                inner=client.transport or httpx.AsyncHTTPTransport(),
            )
        )

    async def _definition(self, slug: str) -> dict[str, object]:
        try:
            payload = await pipedream.pipedream_client().action_definition(slug)
        except PipedreamError as error:
            if error.status != NOT_FOUND:
                raise
            raise UnknownBrokerTool(slug) from error
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    async def _key_miss(
        self, client: pipedream.PipedreamClient, provider: str, slug: str
    ) -> PipedreamError:
        """An unknown action key on execute, augmented with the app's real keys so the model's next
        attempt is informed instead of another blind guess. Augmentation is best-effort: if the
        catalog lookup fails, a plain not-found stands."""
        app = _spec(provider).app
        try:
            tools = _listed_tools(await client.list_actions(app, ""))
        except PipedreamError:
            return PipedreamError(NOT_FOUND, f"no action {slug!r} on {app!r}")
        names = ", ".join(tool.slug for tool in tools)
        return PipedreamError(
            NOT_FOUND, f"no action {slug!r} on {app!r} — actions available: {names}"
        )


def _spec(provider: str) -> ConnectorSpec:
    spec = pipedream.CONNECTORS.get(provider)
    if spec is None:
        raise KeyError(f"pipedream registers no connector provider {provider!r}")
    return spec


def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]:
    """Project a Pipedream `list_actions` response to the app's real component keys and short
    descriptions."""
    items = listed.get("data")
    tools: list[BrokerTool] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key:
            continue
        tools.append(BrokerTool(slug=key, description=_str(item.get("description"))))
    return tuple(tools)


def _props(definition: dict[str, object]) -> list[dict[str, object]]:
    props = definition.get("configurable_props")
    return [prop for prop in props if isinstance(prop, dict)] if isinstance(props, list) else []


def _app_slot(definition: dict[str, object], slug: str) -> str:
    """The component's app prop — the slot the connected account binds into. An action without one
    cannot execute against a granted account, so it fails loud."""
    for prop in _props(definition):
        name = prop.get("name")
        if prop.get("type") == APP_PROP_TYPE and isinstance(name, str) and name:
            return name
    raise PipedreamError(502, f"action {slug!r} declares no app slot to bind the account into")


def _input_schema(props: list[dict[str, object]]) -> dict[str, object]:
    """A JSON schema over the action's configurable props the agent may set: the app slot is the
    broker's to bind, and service props (`$.…`, `dir` — gmail-send-email carries a `syncDir`) are
    Pipedream-internal, so none of them is offered."""
    properties: dict[str, object] = {}
    required: list[str] = []
    for prop in props:
        name = prop.get("name")
        prop_type = _str(prop.get("type"))
        if not isinstance(name, str) or not name:
            continue
        if prop_type in _INTERNAL_PROP_TYPES or prop_type.startswith("$."):
            continue
        field: dict[str, object] = {"type": _PROP_TYPE_TO_JSON.get(prop_type, "string")}
        description = prop.get("description") or prop.get("label")
        if isinstance(description, str) and description:
            field["description"] = description
        properties[name] = field
        if not prop.get("optional"):
            required.append(name)
    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""
