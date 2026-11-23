"""The Composio `ConnectorBroker`: the seam object every Composio connector shares.

Each method resolves the deploy's broker client per call (so a test's transport override is
honoured) and speaks for one provider by its toolkit slug. `tools`/`schema` project the Composio
catalog into `BrokerTool`s; `execute` runs a tool on Composio's server-side execute API under this
workspace's broker user — a slug 404 is augmented with the toolkit's real slugs so the model's next
attempt is informed, and a failure naming an account this broker does not hold (a grant that
predates it, or a broker org rotation) tells the agent to have the member reconnect instead of
answering with tool slugs; `search` rides the Tool Router; `credential` confirms
the account is owned by this workspace's broker user (metadata, never a token — the confused-deputy
guard) and returns a `Credential` whose transport proxies provider HTTP through Composio's
proxy-execute, so a feed-sync source holds no secret. That credential also carries an executor for
the toolkit's own tools, which is how a source of a toolkit with no REST host (an MCP-only service)
reads its records at all.

Files cross as references: `file_outputs` finds the `{name, mimetype, s3url}` objects an execute
response carries (presigned URLs on Composio's file store), and `stage_upload` mints an upload slot
there for a tool's file input — `schema` rewrites each `file_uploadable` parameter to the
`workspace_file` vocabulary the dynamic connector tools stage. The sandbox moves the bytes both
ways through the declared transfer hosts."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
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
from ufo_ext_composio import client as composio
from ufo_ext_composio.proxy import ComposioProxyTransport

DISCOVERY_DESCRIPTION_CAP = 240
NOT_FOUND = 404


@dataclass(frozen=True)
class ComposioBroker:
    """Stateless — the broker client and its key are read per call, so no connection leaks and a
    test's transport override is honoured. See the module docstring for each method's contract."""

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        return _discovered_tools(await composio.composio_client().list_tools(provider, query))

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        try:
            payload = await composio.composio_client().tool_schema(slug)
        except composio.ComposioError as error:
            if error.status != NOT_FOUND:
                raise
            raise UnknownBrokerTool(slug) from error
        description = payload.get("description")
        input_schema = composio.workspace_file_schema(payload.get("input_parameters"))
        return BrokerTool(
            slug=slug,
            description=description if isinstance(description, str) else "",
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            read_only=_read_only(payload),
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
            if _stale_account(error, account_id):
                raise composio.ComposioError(
                    error.status, f"{error.body} — {stale_grant_guidance(provider)}"
                ) from error
            if error.status != NOT_FOUND:
                raise
            raise await self._slug_miss(client, provider, slug, error) from error

    def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]:
        """Every file the tool produced, wherever it sits in the response: Composio marks one as a
        `{name, mimetype, s3url}` object, `s3url` a presigned URL on its file store."""
        found: list[BrokerFile] = []
        _collect_files(response, found)
        return tuple(found)

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload:
        upload = await composio.composio_client().create_upload(
            provider, slug, filename, mimetype, md5
        )
        return StagedUpload(
            put_url=upload.put_url,
            content_type=mimetype,
            argument={"name": filename, "mimetype": mimetype, "s3key": upload.key},
        )

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return await composio.search_connector_tools(
            composio.composio_client(), workspace_id, provider, query
        )

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
        client = composio.composio_client()
        try:
            await client.connected_account(account, broker_user, provider)
        except composio.ComposioError as error:
            if error.status != NOT_FOUND:
                raise
            raise GrantUnusable(f"{error.body} — {stale_grant_guidance(provider)}") from error
        return Credential(
            transport=ComposioProxyTransport(
                api_base=composio.COMPOSIO_API_BASE,
                api_key=client.api_key,
                connected_account_id=account,
                inner=client.transport or httpx.AsyncHTTPTransport(),
            ),
            execute=_AccountExecutor(
                broker=self, workspace_id=workspace_id, provider=provider, account=account
            ),
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
        try:
            query = " ".join(dict.fromkeys(re.sub(r"[^a-z0-9]+", " ", slug.lower()).split()))
            tools = _discovered_tools(await client.list_tools(provider, query))
            if not tools and query:
                tools = _discovered_tools(await client.list_tools(provider, ""))
        except (composio.ComposioError, ValueError, KeyError):
            return error
        if not tools:
            return error
        names = ", ".join(tool.slug for tool in tools)
        return composio.ComposioError(
            error.status, f"{error.body} — tools available on {provider}: {names}"
        )


@dataclass(frozen=True)
class _AccountExecutor:
    broker: ComposioBroker
    workspace_id: UUID
    provider: str
    account: str

    async def __call__(self, slug: str, arguments: Mapping[str, object]) -> dict[str, object]:
        return await self.broker.execute(
            self.workspace_id, self.provider, slug, arguments, self.account, None
        )


def _collect_files(value: object, found: list[BrokerFile]) -> None:
    match value:
        case {"s3url": str() as url, "mimetype": str(), "name": str() as name} if url:
            found.append(BrokerFile(name=name, url=url))
        case dict():
            for item in value.values():
                _collect_files(item, found)
        case list():
            for item in value:
                _collect_files(item, found)


def _stale_account(error: composio.ComposioError, account_id: str) -> bool:
    """Composio surfaces a dead connected account as 400 or 404, so any status matches; only its
    "connected account" wording or the granted id counts."""
    body = error.body.lower()
    return ("connected account" in body and "not found" in body) or (
        account_id.lower() in body and "not found" in body
    )


def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]:
    tools: list[BrokerTool] = []
    for item in rows:
        slug = item.get("slug") or item.get("name")
        if not isinstance(slug, str) or not slug:
            continue
        description = item.get("description")
        input_schema = composio.workspace_file_schema(item.get("input_parameters"))
        tools.append(
            BrokerTool(
                slug=slug,
                description=description[:DISCOVERY_DESCRIPTION_CAP]
                if isinstance(description, str)
                else "",
                input_schema=input_schema if isinstance(input_schema, dict) else {},
                read_only=_read_only(item),
            )
        )
    return tuple(tools)


def _read_only(payload: dict[str, object]) -> bool:
    tags = payload.get("tags")
    return isinstance(tags, list) and "readOnlyHint" in tags
