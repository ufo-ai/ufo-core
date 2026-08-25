"""The Pipedream `ConnectorBroker`: the seam object every Pipedream connector shares.

Each method resolves the deploy's Connect client per call (so a test's transport override is
honoured) and speaks for one provider by its app slug. `tools`/`schema` project the app's pre-built
actions into `BrokerTool`s — an action's input schema is derived from its `configurable_props`,
with the app slot (the connected account) held back for the broker to bind itself; `execute` runs
an action on Pipedream's server-side run API with the granted account bound through that slot's
`authProvisionId` — an unknown key is augmented with the app's real keys so the model's next
attempt is informed, an action-level error raises loud, and a failure naming an account this
broker does not hold (a grant that predates it) tells the agent to have the member reconnect;
`search` is the same catalog search (Pipedream has no router, so plan/guidance stay empty);
`credential` verifies the state-scoped account owner and app before returning a Connect Proxy
transport.

Files cross as references: every run rides a fresh File Stash, so `file_outputs` projects the
response's `$filestash_uploads` to presigned URLs the sandbox fetches itself. `stage_upload`
refuses — Pipedream actions take file inputs as URLs, so a workspace file travels as its
share_file download URL."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import PurePosixPath
from uuid import UUID

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import (
    BrokerFile,
    BrokerSearch,
    BrokerTool,
    GrantUnusable,
    StagedUpload,
    UnknownBrokerTool,
    stale_grant_guidance,
)
from ufo_ext_pipedream import client as pipedream
from ufo_ext_pipedream.client import APP_PROP_TYPE, ConnectorSpec, PipedreamError
from ufo_ext_pipedream.proxy import PipedreamProxyTransport

NOT_FOUND = 404
SUGGESTION_LIMIT = 5
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
        client = pipedream.pipedream_client()
        return _listed_tools(await client.list_actions(_spec(provider).app, query))

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        definition = await self._definition(slug)
        return BrokerTool(
            slug=slug,
            description=_str(definition.get("description")),
            input_schema=_input_schema(_props(definition)),
            read_only=_read_only(definition),
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
        try:
            account = await client.workspace_account(account_id, workspace_id)
            app = _spec(provider).app
            if account.app != app:
                raise PipedreamError(
                    403,
                    f"connected account {account_id!r} authenticates {account.app!r}, not {app!r}",
                )
            response = await client.run_action(slug, account.external_user_id, configured)
        except PipedreamError as error:
            raise (
                _reconnect_error(error, provider) if _stale_account(error, account_id) else error
            ) from error
        action_error = response.get("error")
        if action_error:
            failed = PipedreamError(502, json.dumps(action_error))
            raise (
                _reconnect_error(failed, provider) if _stale_account(failed, account_id) else failed
            )
        return response

    def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]:
        """Every file the action saved to its `/tmp`, as the File Stash synced it: each
        `exports.$filestash_uploads` entry names the container-local path and the presigned
        `get_url` the sandbox fetches the bytes from."""
        exports = response.get("exports")
        uploads = (
            exports.get(pipedream.FILESTASH_UPLOADS_EXPORT) if isinstance(exports, dict) else None
        )
        files: list[BrokerFile] = []
        for upload in uploads if isinstance(uploads, list) else []:
            if not isinstance(upload, dict):
                continue
            url = upload.get("get_url")
            if not isinstance(url, str) or not url:
                continue
            local_path = upload.get("localPath")
            name = PurePosixPath(local_path).name if isinstance(local_path, str) else ""
            files.append(BrokerFile(name=name, url=url))
        return tuple(files)

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload:
        raise ValueError(
            f"{provider!r} actions take file inputs as URLs, not staged uploads — share the "
            "workspace file with share_file and pass its download URL"
        )

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return BrokerSearch(tools=await self.tools(workspace_id, provider, query))

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        spec = _spec(provider)
        client = pipedream.pipedream_client()
        try:
            connected = await client.workspace_account(account, workspace_id)
        except PipedreamError as error:
            if error.status != NOT_FOUND:
                raise
            raise GrantUnusable(f"{error.body} — {stale_grant_guidance(provider)}") from error
        if connected.app != spec.app:
            raise PipedreamError(
                409,
                f"connected account {account!r} authenticates {connected.app!r}, not {spec.app!r}",
            )
        return Credential(
            transport=PipedreamProxyTransport(
                client=client,
                account_id=account,
                external_user_id=connected.external_user_id,
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
        """An unknown action key on execute, answered with the app's closest real keys so the
        model's next attempt is informed instead of another blind guess. Closest, never every key:
        an app's catalog runs to hundreds of actions, so the whole list would cost more context than
        the miss and is what `describe_external_tools` is for — which is what the error names when
        the catalog lookup fails or nothing is close enough to suggest."""
        app = _spec(provider).app
        try:
            tools = _listed_tools(await client.list_actions(app, ""))
        except PipedreamError:
            tools = ()
        close = get_close_matches(slug, [tool.slug for tool in tools], n=SUGGESTION_LIMIT)
        hint = (
            f"closest: {', '.join(close)}"
            if close
            else "search the app's actions with describe_external_tools"
        )
        return PipedreamError(NOT_FOUND, f"no action {slug!r} on {app!r} — {hint}")


def _stale_account(error: PipedreamError, account_id: str) -> bool:
    """Whether a run failure names an unknown external user or account — Pipedream's shapes of the
    stale-grant signature `stale_grant_guidance` answers. Matching is deliberately narrow: only
    Connect's own vocabulary ("external user") or the granted account id itself, so a
    provider-domain error that happens to say some upstream object's account was not found never
    masquerades as a stale grant."""
    body = error.body.lower()
    return "external user not found" in body or (account_id.lower() in body and "not found" in body)


def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError:
    return PipedreamError(error.status, f"{error.body} — {stale_grant_guidance(provider)}")


def _spec(provider: str) -> ConnectorSpec:
    spec = pipedream.CONNECTORS.get(provider)
    if spec is None:
        raise KeyError(f"pipedream registers no connector provider {provider!r}")
    return spec


def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]:
    """Project Pipedream's listed actions to the app's real component keys, short descriptions, and
    the input schemas the listing already carries (`configurable_props`) — so discovery answers with
    what a call needs, without a second round trip per key."""
    tools: list[BrokerTool] = []
    for item in rows:
        key = item.get("key")
        if not isinstance(key, str) or not key:
            continue
        tools.append(
            BrokerTool(
                slug=key,
                description=_str(item.get("description")),
                input_schema=_input_schema(_props(item)),
                read_only=_read_only(item),
            )
        )
    return tuple(tools)


def _read_only(definition: dict[str, object]) -> bool:
    annotations = definition.get("annotations")
    return isinstance(annotations, dict) and annotations.get("readOnlyHint") is True


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
