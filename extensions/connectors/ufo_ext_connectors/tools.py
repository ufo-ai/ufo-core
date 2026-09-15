"""The dynamic connector tool surface: discover connectors, describe a connector's real tools, and
execute one server-side — generic over every installed broker.

A broker fronts hundreds of services and thousands of tools, so the agent never holds a fixed
per-provider tool — it searches. Every call reads the turn's `ConnectorRegistry` and dispatches to
the broker that registered the provider: `list_external_tools` filters the registry locally;
`describe_external_tools` and `search_connector_tools` read the broker's catalog — a discovery
listing whose query matched nothing falls back to the connector's unqueried top tools and marks the
answer, so discovery is never a dead end and a fallback head is never read as a relevance ranking;
`call_external_tool` executes on the broker's server-side API, authenticated by the turn-agent's
connected account (bound through `/connect`). The broker holds the account's token and injects it
itself, so an execute reaches only the broker's own API. A Slack message sent through a connector
is the one call that publishes text this deploy wrote into someone else's surface, so its body
carries the ufo attribution (`slack_attributed`) — the Slack surface marks its own replies with the
footer it renders, and a connector send reaches no renderer of ours. That footer reaches an internal
audience only: a send whose own connector account did not prove the destination internal goes out
unmarked, so an externally-shared Slack channel and a Teams chat across the tenant carry nothing.

Files cross through the workspace, moved by the sandbox itself: an argument carrying the
`workspace_file` vocabulary is hashed in the container, staged to where the broker mints
(`stage_upload`, a presigned PUT), and replaced by the broker's own argument value; every file a
tool produces (`file_outputs`, presigned URLs on the broker's file store) is fetched into
`/workspace/connector_files/` and listed in the result. Both transfers ride the egress proxy under
the grant's declared transfer hosts — the bytes never cross the serve process. Base64 a provider
inlines in its own JSON result already has, so it is translated in place before the result enters
context: decoded text inline, anything binary or large written to `/workspace/connector_files/`
through the sandbox's write seam and replaced by a reference. An identical object a provider repeats
per list item crosses once the same way: the first occurrence in full, every later copy the
`same_as` pointer naming it."""

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import shlex
import string
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from ufo.sdk.connectors import (
    WORKSPACE_FILE_KEY,
    BrokerFile,
    BrokerTool,
    ConnectorEntry,
    ConnectorRegistry,
    UnknownBrokerTool,
)
from ufo.sdk.context import JsonValue
from ufo.sdk.o11y import log
from ufo.sdk.sandbox import WORKSPACE_DIR, workspace_path
from ufo.sdk.tools import (
    AuthorizationBinding,
    AuthorizationScope,
    ConnectorConnection,
    StandingAuthorization,
    TextContent,
    ToolContext,
    ToolDef,
    ToolFailure,
    ToolResult,
)

CONNECTOR_FILES_DIR = "connector_files"
FALLBACK_FILENAME = "download"
UNUSABLE_FILENAMES = frozenset({"", ".", ".."})
FALLBACK_MIMETYPE = "application/octet-stream"
TRANSFER_TIMEOUT_SECONDS = 600
TRANSFER_MAX_BYTES = 100 * 1024 * 1024
WORKSPACE_FILES_RESULT_KEY = "workspace_files"
CATALOG_SEARCH_LIMIT = 10
MAX_LIST_QUERIES = 8
AVAILABLE_TOOLS_KEY = "availableTools"
AVAILABLE_TOOLS_NOTE_KEY = "availableTools_note"
SEARCH_TOOLS_NOTE_KEY = "tools_note"
AVAILABLE_TOOLS_BUDGET_CHARS = 20_000
AVAILABLE_TOOLS_FALLBACK_NOTE = (
    "No tool matched the query. These are the connector's top tools in its own catalog order, "
    "not matches ranked by relevance."
)
AVAILABLE_TOOLS_OMITTED_NOTE = (
    "{omitted} of the {total} tools listed are omitted here; narrow the query to reach them."
)

UFO_ATTRIBUTION_LEAD = "Sent using"
UFO_ATTRIBUTION_SUBJECT = "ufo"
UFO_ATTRIBUTION_MENTION_SUBJECT = "<@{bot_user_id}>"
ATTRIBUTION_MRKDWN = f"*{UFO_ATTRIBUTION_LEAD}* {{subject}}"
"""The footer's one shape: the mrkdwn line of a Block Kit context element, which Slack renders in
its small muted contextual type. The lead is bold and the subject left unstyled, so a bot mention
renders as its chip."""
_MENTION_SUBJECT_ARM = UFO_ATTRIBUTION_MENTION_SUBJECT.format(bot_user_id=r"[^\s>]+")
_ATTRIBUTION_ARM = (
    rf"(\*?){re.escape(UFO_ATTRIBUTION_LEAD)}\1[ \t]+"
    rf"(?:{re.escape(UFO_ATTRIBUTION_SUBJECT)}|{_MENTION_SUBJECT_ARM})"
)
"""The footer itself, under either subject and with the lead bold or plain — Slack's own renderers
keep the mrkdwn asterisks on the text a message stores and drop them from the element it delivers
back. The backreference is what pairs the asterisks rather than accepting a body that opens with a
stray one. The two patterns over it differ only in what they demand around it, because the outbound
and inbound reads want opposite tolerances."""
ATTRIBUTION_LINE = re.compile(rf"(?:\A|\n)[ \t]*{_ATTRIBUTION_ARM}[ \t]*(?=\n|\Z)")
"""A footer holding a whole line of its own — the never-stack guard, and the only form an outbound
send is read for. Matching a whole line and never a prefix is what keeps a body that merely opens
the same way ("Sent using an iPhone") a body, still owed a footer of its own."""
ATTRIBUTION_ANYWHERE = re.compile(rf"[ \t]*{_ATTRIBUTION_ARM}")
"""The same footer wherever a published message carries it, whole line or not. Only the inbound read
tolerates this much, because there a footer this deploy wrote must not be readable as a member
addressing the agent in any shape a message can come back in — flattened onto the body's own line,
or quoted with a member's own text on both sides of it."""
CONTEXT_BLOCK_TYPE = "context"
MARKDOWN_BLOCK_TYPE = "markdown"
SECTION_BLOCK_TYPE = "section"
MRKDWN_ELEMENT_TYPE = "mrkdwn"
SLACK_PROVIDER = "slack"
SLACK_MESSAGE_NOUN = "message"
SLACK_SEND_VERBS = ("send", "post", "reply", "schedule")
SLACK_MARKDOWN_ARGUMENT = "markdown_text"
SLACK_BLOCKS_ARGUMENT = "blocks"
SLACK_TEXT_ARGUMENT = "text"
SLACK_CHANNEL_ARGUMENT = "channel"
SLACK_CONVERSATIONS_INFO_URL = "https://slack.com/api/conversations.info"
SLACK_DESTINATION_READ_SECONDS = 3.0
SLACK_EXTERNAL_FLAGS = (
    "is_ext_shared",
    "is_pending_ext_shared",
    "is_org_shared",
    "is_shared",
)
SLACK_MARKDOWN_TEXT_LIMIT = 12_000
"""Slack's cap on the `markdown` blocks of one payload, counted across all of them together — the
bound every writer of that block holds, here and on the surface's own reply, since a payload past it
is a send Slack refuses as `invalid_blocks`. It is also the cap Slack documents on the
`markdown_text` argument, so a body over it is refused whichever of the two carries it and splitting
it across blocks of the same type buys nothing."""
SLACK_SECTION_TEXT_LIMIT = 3_000
"""Slack's cap on one mrkdwn text object, counted per object rather than per payload — the bound a
`text` body is chunked at, since that argument carries far more than one object holds."""

BASE64_MARKER = "base64"
INLINED_MARKER = "utf-8"
OFFLOADED_MARKER = "offloaded"
BASE64_MARKER_KEYS = frozenset({"encoding", "content_encoding", "contentencoding"})
BASE64_CONTENT_KEYS = ("content", "data", "body")
BASE64_NAME_KEYS = ("name", "filename", "file_name", "path")
BASE64_WHITESPACE = {ord(char): None for char in string.whitespace}
MAX_INLINE_DECODED_CHARS = 64 * 1024
MAX_DECODE_CHARS = 2 * 1024 * 1024
MAX_TRANSLATE_DEPTH = 100
DEDUPE_REFERENCE_KEY = "same_as"
MIN_DEDUPE_BYTES = 512
MAX_DEDUPE_CHARS = 1024 * 1024
MAX_DEDUPE_TOKENS = 24_000
DATA_URL_PREFIX = "data:"
DATA_URL_RE = re.compile(
    r"\Adata:(?P<mime>[\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[\w.+-]+)*;base64,(?P<payload>.*)\Z",
    re.DOTALL,
)

MD5_PREFLIGHT_PROG = """
import hashlib
import sys

digest = hashlib.md5(usedforsecurity=False)
size = 0
try:
    with open(sys.argv[1], "rb") as handle:
        while chunk := handle.read(1048576):
            digest.update(chunk)
            size += len(chunk)
except OSError as error:
    raise SystemExit(str(error))
print(digest.hexdigest())
print(size)
"""

PLACE_PROG = """
import os
import sys

try:
    os.replace(sys.argv[1], sys.argv[2])
except OSError as error:
    raise SystemExit(str(error))
"""


class ListExternalToolsInput(BaseModel):
    queries: tuple[str, ...] = Field(
        max_length=MAX_LIST_QUERIES,
        description="Search keywords. Use single-word queries — split multi-word searches into "
        "separate keywords, e.g. ['Microsoft', 'email'] not ['Microsoft email']. Multiple queries "
        "searched in parallel. Use 'select:<source_id>' to fetch a specific connector by exact ID.",
    )


class DescribeExternalToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'slack', 'gmail'.")
    tool_names: tuple[str, ...] = Field(
        default=(),
        description="Exact tool names to get schemas for, from list_external_tools results. Omit "
        "to discover the connector's tools via `query`.",
    )
    query: str = Field(
        default="", description="Discovery query to find matching tools when tool_names is omitted."
    )


class CallExternalToolInput(BaseModel):
    tool_name: str = Field(description="Exact tool name from describe_external_tools results.")
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'gmail'.")
    account_id: str | None = Field(
        default=None,
        description="Connected-account ID. Required when this agent has multiple accounts.",
    )
    arguments: dict[str, JsonValue] = Field(
        description="Arguments for the connector tool as a dict. Pass {} for tools that take no "
        "parameters."
    )
    attribution_bot_user_id: SkipJsonSchema[str | None] = None
    """The bot user the optional Slack surface proved. The connector owns the audience check and
    uses this only to replace the generic footer's subject with a mention."""

    @field_validator("attribution_bot_user_id", mode="before")
    @classmethod
    def _no_model_supplied_attribution_identity(cls, value: object) -> None:
        """A call the model wrote cannot choose the identity named in the footer."""
        return None


class SearchConnectorToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID to search within.")
    query: str = Field(description="Search query to find matching tools in the connector.")


async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult:
    registry = _registry(ctx)
    accounts = await _connected_accounts(ctx)
    matches: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    targets = list(dict.fromkeys(q.removeprefix("select:").strip().lower() for q in args.queries))
    for target in targets:
        for provider, entry in sorted(registry.entries.items()):
            if provider in seen:
                continue
            haystack = f"{provider} {entry.label}".lower()
            if target and target not in haystack:
                continue
            seen.add(provider)
            matches.append(
                {
                    "source_id": provider,
                    "label": entry.label,
                    "connected_accounts": accounts.get(provider, []),
                }
            )
    catalogs = await asyncio.gather(
        *(registry.search_catalog(target, CATALOG_SEARCH_LIMIT) for target in targets)
    )
    for rows in catalogs:
        for row in rows:
            if row.provider in seen:
                continue
            seen.add(row.provider)
            matches.append(
                {
                    "source_id": row.provider,
                    "label": row.label,
                    "connected_accounts": accounts.get(row.provider, []),
                }
            )
    return _json_result({"connectors": matches})


async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]:
    """Each provider's accounts this agent can already use — owner and sharing included, so a
    listing answers what is connected and whose it is without further lookups."""
    accounts: dict[str, list[JsonValue]] = {}
    for account in await ctx.usable_connector_accounts():
        accounts.setdefault(account.provider, []).append(
            {
                "account_id": account.account_id,
                "owner": account.owner_email,
                "shared": account.shared,
            }
        )
    return accounts


async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    workspace_id = ctx.turn.workspace_id
    schemas: dict[str, object] = {}
    unresolved: list[str] = []
    for name in args.tool_names:
        try:
            described = await entry.broker.schema(workspace_id, entry.provider, name)
        except UnknownBrokerTool:
            unresolved.append(name)
            continue
        schemas[name] = _tool_json(described)
    result: dict[str, object] = {"source_id": args.source_id, "schemas": schemas}
    if args.query or unresolved or not args.tool_names:
        query = _discovery_query(args.query, unresolved)
        listed = await entry.broker.tools(workspace_id, entry.provider, query)
        rows, note = await _discovered_rows(entry, workspace_id, query, listed)
        result[AVAILABLE_TOOLS_KEY] = rows
        if note:
            result[AVAILABLE_TOOLS_NOTE_KEY] = note
    if unresolved:
        result["unresolved"] = unresolved
    return _json_result(result)


def attribution_stripped(text: str) -> str:
    """`text` with every attribution this deploy could have written removed, wherever the message it
    came back from carries it — what an inbound read decides over, so a mention left in it is a
    mention the author wrote."""
    return ATTRIBUTION_ANYWHERE.sub("", text)


def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]:
    """`arguments` with the attribution appended as the send's last block — the footer as a section
    of the message like any other, built as the Block Kit context block the Slack surface's own
    reply closes on, which is the one shape Slack renders it in. A body the send authored as
    `blocks` is appended to; a `markdown_text` body moves into the `markdown` block that argument is
    the top-level spelling of, which is also what keeps the two from being sent together, and a
    `text` body stays where it is as the notification fallback while blocks of its own carry it.
    Each body reaches the block that renders the markup it is already written in, so a footer costs
    a send nothing in how it reads. A send already carrying a footer anywhere is left as it is, and
    a call that names no body — a listing, a delete — comes back exactly as it went in."""
    if _carries_attribution(arguments):
        return arguments
    footer: dict[str, JsonValue] = {
        "type": CONTEXT_BLOCK_TYPE,
        "elements": [
            {"type": MRKDWN_ELEMENT_TYPE, "text": ATTRIBUTION_MRKDWN.format(subject=subject)}
        ],
    }
    if SLACK_BLOCKS_ARGUMENT in arguments:
        appended = _appended_blocks(arguments[SLACK_BLOCKS_ARGUMENT], footer)
        if appended is None:
            return arguments
        return {**arguments, SLACK_BLOCKS_ARGUMENT: appended}
    body = _body_blocks(arguments)
    if body is None:
        return arguments
    kept = {name: value for name, value in arguments.items() if name != SLACK_MARKDOWN_ARGUMENT}
    return {**kept, SLACK_BLOCKS_ARGUMENT: [*body, footer]}


def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None:
    """The body a send authored as text, as the blocks the footer's own block follows, or None when
    it authored none. The two arguments are written in different markup languages and each reaches
    the block that renders its own: `markdown_text` is the standard markdown of a `markdown` block,
    `text` is the mrkdwn of a section's text object, and typing either into the other's block is
    what would publish `<https://x|label>` as its own characters and read `*x*` as emphasis instead
    of bold.

    A `markdown_text` body stays whole in its one block: Slack caps that argument and those blocks
    at the same 12,000 characters, counted across the payload, so a longer body is a send it refuses
    either way. A `text` body runs to the 40,000 characters that argument carries, past what one
    text object holds, so it is chunked at the object's own cap the way the Slack surface chunks its
    own reply."""
    markdown = arguments.get(SLACK_MARKDOWN_ARGUMENT)
    if isinstance(markdown, str) and markdown.strip():
        return [{"type": MARKDOWN_BLOCK_TYPE, "text": markdown}]
    text = arguments.get(SLACK_TEXT_ARGUMENT)
    if isinstance(text, str) and text.strip():
        return [
            {
                "type": SECTION_BLOCK_TYPE,
                "text": {
                    "type": MRKDWN_ELEMENT_TYPE,
                    "text": text[start : start + SLACK_SECTION_TEXT_LIMIT],
                },
            }
            for start in range(0, len(text), SLACK_SECTION_TEXT_LIMIT)
        ]
    return None


def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None:
    """The `blocks` value with the footer's block after it, or None to leave the argument as it is —
    an empty list is no body, and a value this cannot read is one no footer can be appended to. The
    broker's schema takes the list itself and a serialized form of it, so a string is parsed and
    re-emitted the way it arrived — and read for a footer of its own only once parsed, since inside
    a serialized body no element holds a line."""
    match value:
        case list() if value:
            return [*value, footer]
        case str():
            for url_encoded in (False, True):
                try:
                    parsed = json.loads(unquote(value) if url_encoded else value)
                except ValueError:
                    continue
                if not isinstance(parsed, list) or not parsed or _carries_attribution(parsed):
                    return None
                rendered = json.dumps([*parsed, footer])
                return quote(rendered) if url_encoded else rendered
            return None
        case _:
            return None


def _carries_attribution(value: JsonValue) -> bool:
    match value:
        case str():
            return bool(ATTRIBUTION_LINE.search(value))
        case list():
            return any(_carries_attribution(item) for item in value)
        case dict():
            return any(_carries_attribution(item) for item in value.values())
        case _:
            return False


def slack_attributed(
    provider: str,
    slug: str,
    arguments: dict[str, JsonValue],
    *,
    destination_internal: bool,
    bot_user_id: str | None = None,
) -> dict[str, JsonValue]:
    """`arguments` with the ufo attribution attached to the body of a Slack send — the one connector
    call that publishes a message this deploy wrote, and the only place it can be marked: the Slack
    surface's own reply carries its footer, a message posted through a connector passes through no
    renderer of ours. A read, an edit, and a listing are untouched, as is a send already carrying a
    footer of its own, so a resend or an edit of a marked message never stacks it. The optional bot
    id supplied by the Slack extension selects the mentioning form; without it the product name is
    the subject.

    A destination not proved internal carries no footer at all, the way the Slack surface withholds
    its own accounting footer off an externally-shared channel: a Slack Connect or org-shared
    channel, a Microsoft Teams chat or shared channel that crosses the tenant, and any destination
    the prover could not read. `destination_internal` is that proof and nothing else settles it, so
    a send whose audience cannot be shown internal fails closed and goes out unmarked."""
    if not destination_internal or not is_slack_send(provider, slug):
        return arguments
    subject = (
        UFO_ATTRIBUTION_SUBJECT
        if bot_user_id is None
        else UFO_ATTRIBUTION_MENTION_SUBJECT.format(bot_user_id=bot_user_id)
    )
    return attributed_arguments(arguments, subject)


def is_slack_send(provider: str, slug: str) -> bool:
    """Whether this connector call publishes a Slack message."""
    name = slug.lower()
    return (
        provider == SLACK_PROVIDER
        and SLACK_MESSAGE_NOUN in name
        and any(verb in name for verb in SLACK_SEND_VERBS)
    )


async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    if ctx.connector_read_only:
        described = await entry.broker.schema(ctx.turn.workspace_id, entry.provider, args.tool_name)
        if not described.read_only:
            return ToolFailure(
                operation=f"{entry.provider}.{args.tool_name}",
                summary=(
                    f"{args.tool_name} writes through {entry.provider}, and this agent reads "
                    "connectors without writing to them. Nothing was sent, and re-issuing the "
                    "call refuses again. Gather what you need with a read-only tool of this "
                    "provider, and leave the write to the conversation that can make it."
                ),
            ).result()
    connection = await ctx.connector_connection(args.source_id, args.account_id)
    candidate = slack_attributed(
        entry.provider,
        args.tool_name,
        args.arguments,
        destination_internal=True,
        bot_user_id=args.attribution_bot_user_id,
    )
    arguments = (
        candidate
        if candidate is not args.arguments
        and await _destination_is_internal(ctx, entry, connection, args.arguments)
        else args.arguments
    )
    call = _ConnectorCall(ctx=ctx, entry=entry, slug=args.tool_name)
    return ToolResult(content=(TextContent(text=await call.run(arguments, connection)),))


async def call_external_tool_standing_authorization(
    ctx: ToolContext, args: CallExternalToolInput
) -> StandingAuthorization[CallExternalToolInput]:
    entry = _registry(ctx).entry(args.source_id)
    described, connection = await asyncio.gather(
        entry.broker.schema(ctx.turn.workspace_id, entry.provider, args.tool_name),
        ctx.connector_connection(entry.provider, args.account_id),
    )
    scope = AuthorizationScope(
        provider=entry.provider,
        account_id=connection.account_id,
        operation=described.slug,
        access="read" if described.read_only else "write",
    )
    binding = AuthorizationBinding(
        connection_id=connection.id,
        grant_id=connection.grant_id,
        **scope.model_dump(),
    )
    return StandingAuthorization(
        context=replace(
            ctx,
            connector_selection=connection,
            connector_binding=binding,
        ),
        input=args.model_copy(
            update={
                "source_id": entry.provider,
                "tool_name": described.slug,
                "account_id": connection.account_id,
            }
        ),
        scope=scope,
        binding=binding,
    )


async def _destination_is_internal(
    ctx: ToolContext,
    entry: ConnectorEntry,
    connection: ConnectorConnection,
    arguments: dict[str, JsonValue],
) -> bool:
    channel = arguments.get(SLACK_CHANNEL_ARGUMENT)
    if not isinstance(channel, str) or not channel:
        return False
    try:
        async with asyncio.timeout(SLACK_DESTINATION_READ_SECONDS):
            credential = await entry.broker.credential(
                ctx.turn.workspace_id, entry.provider, connection.account_id
            )
            headers = dict(credential.headers)
            if credential.bearer is not None:
                headers["Authorization"] = f"Bearer {credential.bearer}"
            if credential.transport is None and not headers:
                return False
            async with httpx.AsyncClient(
                transport=credential.transport,
                headers=headers,
                timeout=SLACK_DESTINATION_READ_SECONDS,
            ) as client:
                response = await client.get(
                    SLACK_CONVERSATIONS_INFO_URL,
                    params={"channel": channel},
                )
                response.raise_for_status()
                payload = response.json()
    except Exception as error:
        log("connector.slack.destination_unread", error_class=type(error).__name__)
        return False
    info = payload.get("channel") if isinstance(payload, Mapping) else None
    return (
        payload.get("ok") is True
        and isinstance(info, Mapping)
        and info.get("id") == channel
        and not any(info.get(flag) is True for flag in SLACK_EXTERNAL_FLAGS)
    )


@dataclass(frozen=True)
class _ConnectorCall:
    """One connector tool execution, top to bottom: stage every `workspace_file` argument to the
    broker's file store, execute server-side with the granted account, fetch the produced files
    back into the workspace, translate the base64 the provider inlined in its own result, and
    replace every object that result repeats identically with a pointer to its first occurrence —
    the private steps below in execution order. Both transfers run inside the sandbox, so the bytes
    never cross the serve process.

    The flow hands back the result already serialized, because condensing it has to serialize the
    response to bound its own work: a result nothing was condensed in crosses on that one string,
    and a condensed one is serialized again from the smaller form it became.

    Condensing runs in a worker thread. It is bounded (`MAX_DEDUPE_TOKENS`), but the bound counts
    nodes and the cost of a node is hardware: shapes measuring 8 to 9.5 ms on one box measured 3 to
    5 times that on another, past the budget for holding the one loop, which is when doctrine says
    the work goes to a pool deliberately. A pool is worth taking here, unlike for the base64 decode
    below: this is interpreted bytecode, so the interpreter hands the GIL back every switch interval
    and the loop keeps serving — on a 38 ms walk the loop's worst stall drops from 38.7 ms to
    6.3 ms — where a single `b64decode` holds the GIL start to finish and can only be refused."""

    ctx: ToolContext
    entry: ConnectorEntry
    slug: str

    async def run(self, arguments: dict[str, JsonValue], connection: ConnectorConnection) -> str:
        staged = {key: await self._staged_value(item) for key, item in arguments.items()}
        authorization = self.ctx.connector_binding
        if authorization is not None:
            described = await self.entry.broker.schema(
                self.ctx.turn.workspace_id,
                self.entry.provider,
                self.slug,
            )
            if (
                authorization.provider != self.entry.provider
                or authorization.account_id != connection.account_id
                or authorization.connection_id != connection.id
                or authorization.grant_id != connection.grant_id
                or authorization.operation != self.slug
                or described.slug != authorization.operation
                or described.read_only != (authorization.access == "read")
            ):
                raise ValueError("the connector operation changed after member authorization")
        await self.ctx.require_connector_connection(connection)
        response = await self.entry.broker.execute(
            self.ctx.turn.workspace_id,
            self.entry.provider,
            self.slug,
            staged,
            connection.account_id,
            self.ctx.idempotency_key,
        )
        files = await self._fetched_files(self.entry.broker.file_outputs(response))
        translated = await self._translated_node(response)
        payload = {**translated, WORKSPACE_FILES_RESULT_KEY: files} if files else translated
        return await asyncio.to_thread(self._deduped, payload)

    async def _staged_value(self, value: object) -> object:
        """An argument value with every `{"workspace_file": path}` staged to the broker's file
        store and replaced by the broker's own argument naming the staged object — a file crosses
        as that reference, its bytes PUT by the sandbox."""
        match value:
            case dict() if set(value) == {WORKSPACE_FILE_KEY}:
                path = value[WORKSPACE_FILE_KEY]
                if not isinstance(path, str) or not path:
                    raise ValueError(f"{WORKSPACE_FILE_KEY} must be a workspace path string")
                return await self._stage_file(path)
            case dict():
                return {key: await self._staged_value(item) for key, item in value.items()}
            case list():
                return [await self._staged_value(item) for item in value]
            case _:
                return value

    async def _stage_file(self, path: str) -> dict[str, object]:
        """Stage one workspace file: hash it in the container, ask the broker where it goes, PUT
        the bytes there from inside the sandbox, and return the argument value that names it. A
        broker answering a dedup hit (no put_url) already holds the bytes, so the PUT is skipped."""
        scoped = workspace_path(path)
        preflight = await self.ctx.sandbox.python(
            MD5_PREFLIGHT_PROG, scoped, timeout_s=TRANSFER_TIMEOUT_SECONDS
        )
        if preflight.exit_code != 0:
            raise ValueError(preflight.stderr.strip() or f"cannot read workspace file {path!r}")
        digest, _, size = preflight.stdout.strip().partition("\n")
        if int(size) > TRANSFER_MAX_BYTES:
            raise ValueError(
                f"workspace file {path!r} is {size} bytes, over the {TRANSFER_MAX_BYTES}-byte limit"
            )
        filename = PurePosixPath(scoped).name
        mimetype = mimetypes.guess_type(filename)[0] or FALLBACK_MIMETYPE
        staged = await self.entry.broker.stage_upload(
            self.ctx.turn.workspace_id, self.entry.provider, self.slug, filename, mimetype, digest
        )
        if staged.put_url is None:
            return staged.argument
        content_type = shlex.quote(f"Content-Type: {staged.content_type}")
        put = await self.ctx.sandbox.bash(
            f"curl -fsS -T {shlex.quote(scoped)} -H {content_type} "
            f"--url {shlex.quote(staged.put_url)}",
            timeout_s=TRANSFER_TIMEOUT_SECONDS,
        )
        if put.exit_code != 0:
            raise RuntimeError(put.stderr.strip() or f"staging workspace file {path!r} failed")
        return staged.argument

    async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]:
        """Fetch each produced file from its presigned URL into the workspace, from inside the
        sandbox — under a fresh `connector_files/<uuid>/` so no fetch clobbers another file. The
        provider names the file, so the name is reduced to one leaf."""
        saved: list[dict[str, str]] = []
        for file in files:
            leaf = PurePosixPath(file.name.replace("\\", "/")).name
            safe = FALLBACK_FILENAME if leaf in UNUSABLE_FILENAMES else leaf
            target = f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/{uuid4()}/{safe}"
            fetched = await self.ctx.sandbox.bash(
                f"curl -fsSL --max-filesize {TRANSFER_MAX_BYTES} --create-dirs "
                f"-o {shlex.quote(target)} --url {shlex.quote(file.url)}",
                timeout_s=TRANSFER_TIMEOUT_SECONDS,
            )
            if fetched.exit_code != 0:
                raise RuntimeError(
                    fetched.stderr.strip()
                    or f"fetching produced file {safe!r} into the workspace failed"
                )
            saved.append({"name": safe, "workspace_path": target})
        return saved

    async def _translated_node(
        self, node: Mapping[str, object], depth: int = 0
    ) -> dict[str, object]:
        """One result object with the base64 its provider inlined translated into what the model
        can actually read, and its children walked. GitHub's contents API answers `{"content":
        "<b64>", "encoding": "base64"}`; left alone that base64 lands in context verbatim, where it
        is enormous, useless as text, and re-read on every later round. The provider's own marker
        is what triggers the translation — never a base64-looking string, so an id or a digest is
        never mangled. A marker is a claim, not a guarantee, so each marked field stands on its own:
        one that is not valid base64 is left exactly as the provider sent it while its siblings are
        still translated, since holding a decodable field back would leave real base64 in context
        for no gain.

        A provider carries one marker for the node, not one per field, so the rewritten marker
        summarizes it: `utf-8` only when every marked field inlined, `offloaded` when any became a
        reference, and left alone when any field is still raw base64 — it never certifies a field it
        does not describe. What describes an individual field is its own value: a string is the
        decoded text, an object is the file to read it from."""
        markers = tuple(
            key
            for key, item in node.items()
            if key.lower() in BASE64_MARKER_KEYS
            and isinstance(item, str)
            and item.strip().lower() == BASE64_MARKER
        )
        marked = (
            tuple(key for key in BASE64_CONTENT_KEYS if isinstance(node.get(key), str))
            if markers
            else ()
        )
        walked = {key: await self._translated(item, depth + 1) for key, item in node.items()}
        if not marked:
            return walked
        decoded: dict[str, tuple[bytes, str | None]] = {}
        for key in marked:
            content = _decoded_base64(node[key])
            if content is not None:
                decoded[key] = content
        if not decoded:
            return walked
        name = next(
            (
                item.strip()
                for item in (node.get(key) for key in BASE64_NAME_KEYS)
                if isinstance(item, str) and item.strip()
            ),
            FALLBACK_FILENAME,
        )
        mimetype = mimetypes.guess_type(name)[0] or FALLBACK_MIMETYPE
        translated = {
            key: await self._translated_bytes(raw, text, name, mimetype)
            for key, (raw, text) in decoded.items()
        }
        if len(translated) < len(marked):
            return {**walked, **translated}
        marker = (
            INLINED_MARKER
            if all(isinstance(item, str) for item in translated.values())
            else OFFLOADED_MARKER
        )
        return {**walked, **translated, **dict.fromkeys(markers, marker)}

    async def _translated(self, value: object, depth: int) -> object:
        """Anything else in the result, translated the same way: objects recurse, and a bare string
        that IS a `data:<mime>;base64,<...>` URL carries the same claim about itself as a marked
        field does — bounded by the same cap the decode is, since matching the pattern scans the
        whole string and a payload past the cap could not be decoded anyway. Broker output is
        untrusted and this walk is the only recursive pass over it on
        a broker whose `file_outputs` reads fixed keys, so past `MAX_TRANSLATE_DEPTH` the subtree is
        returned as it came: nesting deeper than the interpreter's recursion budget degrades to
        untranslated rather than failing a call that used to work."""
        if depth >= MAX_TRANSLATE_DEPTH:
            return value
        match value:
            case dict():
                return await self._translated_node(value, depth)
            case list():
                return [await self._translated(item, depth + 1) for item in value]
            case str() if value.startswith(DATA_URL_PREFIX) and len(value) <= MAX_DECODE_CHARS:
                return await self._translated_data_url(value)
            case _:
                return value

    async def _translated_data_url(self, value: str) -> object:
        """A `data:<mime>;base64,<payload>` string, translated under the mimetype it declares. A
        string that merely starts `data:` without being one is returned untouched."""
        match = DATA_URL_RE.match(value)
        if match is None:
            return value
        content = _decoded_base64(match.group("payload"))
        if content is None:
            return value
        decoded, text = content
        mimetype = match.group("mime") or FALLBACK_MIMETYPE
        name = f"{FALLBACK_FILENAME}{mimetypes.guess_extension(mimetype) or ''}"
        return await self._translated_bytes(decoded, text, name, mimetype)

    async def _translated_bytes(
        self, decoded: bytes, text: str | None, name: str, mimetype: str
    ) -> object:
        """Decoded bytes as the model reads them: the text itself when it is UTF-8 within the inline
        cap — a source file comes back readable, a fraction of the tokens — and otherwise a
        workspace file holding the bytes plus the reference naming it, so neither the base64 nor the
        payload it hides ever enters context."""
        if text is not None and len(text) <= MAX_INLINE_DECODED_CHARS:
            return text
        return await self._offloaded(name, mimetype, decoded)

    async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]:
        """Write decoded bytes into the workspace and return the reference the model reads them by.
        Unlike a produced file, pulled from a presigned URL by the sandbox, these bytes were decoded
        from the execute payload the serve process already holds, so they go straight through the
        sandbox's write seam instead of back out over the network.

        The bytes name their own directory: the same payload decoded again — the next round of a
        conversation re-reading a file, or a retried step — resolves to the path it already wrote
        instead of accumulating a copy per call. A produced file cannot be addressed this way
        because the sandbox streams it from a presigned URL and the serve process never holds it;
        here the bytes are in hand, so the digest is free. The digest is sha256 in full, not the
        broker's dedup md5 and not a prefix of either: a provider chooses this content, and any
        digest it can collide lets one payload overwrite another and be read under its name.

        Addressing by content means two turns decoding the same payload write one path, and
        `Carrier.write` promises no atomicity — docker truncates through `cat >`. So the bytes land
        beside the target and are renamed onto it, which is atomic within a directory: a reader
        either sees the previous complete file or the new one, never a truncated window. A produced
        file needs none of this because its `uuid4` path is unique to one fetch and no second writer
        can reach it."""
        leaf = PurePosixPath(name.replace("\\", "/")).name
        safe = FALLBACK_FILENAME if leaf in UNUSABLE_FILENAMES else leaf
        target = f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/{hashlib.sha256(data).hexdigest()}/{safe}"
        staged = f"{target}.{uuid4()}.part"
        await self.ctx.sandbox.write_file(staged, data)
        placed = await self.ctx.sandbox.python(
            PLACE_PROG, staged, target, timeout_s=TRANSFER_TIMEOUT_SECONDS
        )
        if placed.exit_code != 0:
            raise RuntimeError(
                placed.stderr.strip() or f"placing decoded file {safe!r} in the workspace failed"
            )
        return {"name": safe, "workspace_path": target, "mimetype": mimetype, "bytes": len(data)}

    def _deduped(self, payload: dict[str, object]) -> str:
        """The result as its text, with every object it repeats identically replaced by a pointer to
        the first occurrence. A denormalized list response embeds the parent record in every element
        so each element stands alone — a code search scoped to one repository answers 30 items
        carrying 30 byte-identical copies of that repository, 85% of the payload. The first copy
        stays whole and each later one becomes `{"same_as": "<JSON Pointer>"}` naming it, so nothing
        is projected away and the whole record is one hop from the element that needs it.

        What that buys is a result the model still holds. Dispatch offloads any result over
        `MAX_TOOL_RESULT_CHARS` to a workspace file and keeps a preview, so boilerplate does not
        only cost tokens — it pushes the answer out of context and into a file the model has to
        filter to read. That search is 141K chars and offloads; condensed it is 21K and stays
        inline, the same facts without the round trip. A cross-repo search carries a different
        record per hit, so nothing collapses and it offloads either way, which is the correct
        outcome and not a shortfall: only a real repeat is ever replaced.

        The text is what this returns because deciding whether to run costs one serialization and
        the result needed one anyway: a payload left alone is handed back as the string already
        built, a condensed one is serialized from its condensed form, the smaller of the two.
        Serializing is itself per-node work — 12 ms for a 1 MiB response of 27.5K three-field
        objects — so paying for it twice is the same defect as walking unbounded.

        Structural identity is what a repeat is, so the pass keys on the bytes a node serializes to
        and never on a field name: any provider's denormalization collapses and none is
        special-cased. The pointer only reads as ours while the provider's own JSON does not use the
        key, so a payload already carrying it anywhere crosses exactly as it came rather than
        rewritten into something its reader cannot tell from provider data.

        Two bounds decide whether the pass runs at all, and each caps a different kind of work.
        `MAX_DEDUPE_CHARS` caps the byte-proportional part — the serialization and the hashing. What
        the ceiling gives up is condensing above it: rarely a result that would have come under the
        inline budget, more often one that would still have offloaded but as a smaller file for the
        model to filter. Both are real, and both are traded for not spending the byte work on
        megabytes that mostly cannot be brought back into context.
        `MAX_DEDUPE_TOKENS` caps the node-proportional part, which is what the walk is: it visits
        every node, so 1 MiB is 38 ms as 95K short strings and 5.4 ms as a 470 KB 100-hit search
        page. Structural tokens (`,` and `:` in the serialization) are that count, read in two C
        scans of a string already built — an over-count wherever a string carries one, so the bound
        errs toward leaving a payload alone.

        At the bound a walk measures 8 to 9.5 ms across the densest shapes (numeric ids, metric
        rows, short strings); a real 30-hit search is 6K tokens and 1.6 ms, a full 100-hit page 20K
        and 5.4 ms. What the bound excludes is a result made of tens of thousands of small leaves —
        which is what a result with no repeated object worth replacing looks like, so the refusal
        costs nothing that could have been saved."""
        serialized = json.dumps(payload)
        if (
            len(serialized) > MAX_DEDUPE_CHARS
            or serialized.count(",") + serialized.count(":") > MAX_DEDUPE_TOKENS
            or DEDUPE_REFERENCE_KEY in serialized
        ):
            return serialized
        first: dict[bytes, str] = {}
        return json.dumps(
            {
                key: self._condensed(item, f"/{_escaped(key)}", 1, first)[0]
                for key, item in payload.items()
            }
        )

    def _condensed(
        self, value: object, pointer: str, depth: int, first: dict[bytes, str]
    ) -> tuple[object, bytes, int]:
        """One node condensed, with the digest that identifies it and the bytes it occupies as the
        provider sent it. The digest is built from its children's digests rather than from its own
        serialization, so the pass costs one hash per node instead of one per node per level and a
        deeply nested payload cannot turn into quadratic work on the loop. Each kind of node tags
        its own stream and every key is length-prefixed, so no two nodes that differ can hash the
        same bytes; the digest is sha256 in full for the reason the decoded-bytes path uses it — a
        provider chooses this content, and any digest it can collide lets one record be read as
        another.

        The size a node reports is the one it came in at, never the one it was rewritten to. Both
        numbers describe the node as the provider sent it, so two nodes with equal digests always
        clear the floor alike: a record whose own child was pointed away earlier still collapses
        whole rather than surviving as a partially-pointered copy of a record already in the result.

        A node under `MIN_DEDUPE_BYTES` is neither replaced nor recorded. A pointer costs ~35 bytes,
        so the floor is not break-even: it is where a result stays readable. Measured on two real
        payloads, any floor from 256 to 768 bytes yields the identical full structural saving (85%
        off a code search, 15% off an issue list); dropping it to zero buys a further 1.0% while
        tripling the pointers a reader must follow, and raising it to 1024 loses the issue list's
        saving entirely.

        A leaf renders itself rather than going through the JSON encoder, whose per-value call setup
        made a result of numeric leaves twice the cost of the same count of string leaves (18 ms
        against 9 ms at the bound). `str` tells every JSON scalar apart — `1`, `1.0`, `True` and
        `None` all render differently, and a string that renders the same carries a different tag —
        and it is JSON's own width for every scalar a provider realistically sends, `true`/`false`/
        `null` included. Only a non-finite float renders shorter than JSON writes it, which can
        shade a size against the floor by a few bytes and can never affect an identity.

        Only an object is replaced. A pointer in an array's place would change that field's type,
        and an array is not what a provider repeats — its elements are. Past `MAX_TRANSLATE_DEPTH` a
        node takes a digest of its own position, unique by construction, so nothing at or above it
        is ever judged identical to anything else: the pass never calls two subtrees the same when
        it stopped short of comparing them."""
        if depth >= MAX_TRANSLATE_DEPTH:
            return value, hashlib.sha256(b"@" + pointer.encode()).digest(), 0
        match value:
            case dict():
                walked: dict[str, object] = {}
                stream = [b"{"]
                size = 2 + max(len(value) - 1, 0)
                for key, item in value.items():
                    child, digest, contributed = self._condensed(
                        item, f"{pointer}/{_escaped(key)}", depth + 1, first
                    )
                    walked[key] = child
                    name = key.encode()
                    stream += (b"%d:" % len(name), name, digest)
                    size += len(name) + 3 + contributed
                node = hashlib.sha256(b"".join(stream)).digest()
                if size < MIN_DEDUPE_BYTES:
                    return walked, node, size
                repeated = first.get(node)
                if repeated is None:
                    first[node] = pointer
                    return walked, node, size
                return {DEDUPE_REFERENCE_KEY: repeated}, node, size
            case list():
                items: list[object] = []
                stream = [b"["]
                size = 2 + max(len(value) - 1, 0)
                for index, item in enumerate(value):
                    child, digest, contributed = self._condensed(
                        item, f"{pointer}/{index}", depth + 1, first
                    )
                    items.append(child)
                    stream.append(digest)
                    size += contributed
                return items, hashlib.sha256(b"".join(stream)).digest(), size
            case str():
                return value, hashlib.sha256(b"s" + value.encode()).digest(), len(value) + 2
            case _:
                rendered = str(value)
                return value, hashlib.sha256(b"n" + rendered.encode()).digest(), len(rendered)


def _escaped(token: str) -> str:
    """One JSON Pointer reference token (RFC 6901): a key holding `/` or `~` still names exactly the
    node it came from."""
    return token.replace("~", "~0").replace("/", "~1")


def _decoded_base64(value: object) -> tuple[bytes, str | None] | None:
    """The bytes a marked field holds and their text, or None when the value is not valid base64 —
    a provider's marker is a claim, not a guarantee. Whitespace goes first (GitHub and PEM wrap
    their payloads) and validation is strict, so a mislabelled plain string is left alone rather
    than silently mangled into garbage bytes the way the default lenient decode would. Non-ASCII
    raises a bare ValueError rather than the binascii subclass, so both are caught: an i18n
    placeholder a provider marks base64 leaves its node untouched like any other mislabelled field.
    Text is None when the bytes are not UTF-8, which is what routes them to a file.

    A field longer than MAX_DECODE_CHARS is refused rather than decoded, which is what keeps this
    off the one serve loop's critical path. Each step is a single C call that holds the GIL for its
    whole duration — none of `str.translate`, `base64.b64decode`, or `bytes.decode` releases it — so
    a worker thread would starve the loop just the same; only the bound holds. At the cap the worst
    case measures ~7 ms, inside the budget for GIL-bound work, and a payload past it is left for the
    engine's tool-result offload, which keeps it out of context without decoding anything."""
    if not isinstance(value, str) or len(value) > MAX_DECODE_CHARS:
        return None
    try:
        decoded = base64.b64decode(value.translate(BASE64_WHITESPACE), validate=True)
    except ValueError:
        return None
    try:
        return decoded, decoded.decode("utf-8")
    except UnicodeDecodeError:
        return decoded, None


async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    workspace_id = ctx.turn.workspace_id
    found = await entry.broker.search(workspace_id, entry.provider, args.query)
    rows, note = await _discovered_rows(entry, workspace_id, args.query, found.tools)
    result: dict[str, object] = {
        "connector": args.source_id,
        "tools": rows,
        "plan": list(found.plan),
        "guidance": list(found.guidance),
        "pitfalls": list(found.pitfalls),
    }
    if note:
        result[SEARCH_TOOLS_NOTE_KEY] = note
    return _json_result(result)


def _registry(ctx: ToolContext) -> ConnectorRegistry:
    if ctx.connectors is None:
        raise RuntimeError("connector tools dispatched without the turn's connector registry")
    return ctx.connectors


def _tool_json(tool: BrokerTool) -> dict[str, object]:
    return {"slug": tool.slug, "description": tool.description, "input_schema": tool.input_schema}


async def _discovered_rows(
    entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]
) -> tuple[list[dict[str, object]], str]:
    """The tool rows one discovery answer carries and the note describing how they were reached —
    the seam both discovery tools go through, so the "never a dead end" rule holds however the
    broker matched. A query that found nothing is answered with the connector's unqueried top tools
    instead of empty, marked as catalog order rather than relevance so a fallback head is never read
    as a ranking: a broker whose search is a term match (or a semantic router that recalls nothing)
    would otherwise tell the model the connector cannot do the thing at all."""
    degraded = bool(query) and not found
    listed = await entry.broker.tools(workspace_id, entry.provider, "") if degraded else found
    rows = _available_tools(listed)
    notes = [AVAILABLE_TOOLS_FALLBACK_NOTE] if degraded and rows else []
    if len(rows) < len(listed):
        notes.append(
            AVAILABLE_TOOLS_OMITTED_NOTE.format(omitted=len(listed) - len(rows), total=len(listed))
        )
    return rows, " ".join(notes)


def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]:
    """The tool rows that fit the model's inline result budget, for discovery and search alike: a
    connector can catalog hundreds of tools and every row carries the input schema its listing
    already held, so the projection stops before the answer grows past what the engine keeps in
    context rather than being offloaded to a file the model must then filter."""
    rows: list[dict[str, object]] = []
    spent = 0
    for tool in listed:
        row = _tool_json(tool)
        spent += len(json.dumps(row))
        if spent > AVAILABLE_TOOLS_BUDGET_CHARS and rows:
            break
        rows.append(row)
    return rows


def _discovery_query(explicit: str, unresolved: list[str]) -> str:
    """The catalog search query: the caller's explicit keywords, else the deduped words of the slugs
    that missed (so a guessed `GITHUB_LIST_PULL_REQUEST_REVIEWS` searches 'github list pull request
    reviews' and surfaces the real slug)."""
    if explicit:
        return explicit
    words = re.sub(r"[^a-z0-9]+", " ", " ".join(unresolved).lower()).split()
    return " ".join(dict.fromkeys(words))


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


CONNECTOR_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="list_external_tools",
        description=(
            "Search available external connectors (github, slack, notion, ...), not their tools. "
            "The broker brokers hundreds of services, so always search by keyword rather than "
            "assuming — queries match the live catalog. Returns connector catalog rows: source_id, "
            "label, and connected_accounts — each account already usable here with its owner and "
            "whether it is shared, so one call answers what is connected and whose it is. Call "
            "this before claiming you can't access something — there is very likely a connector "
            "available. Use 'select:<source_id>' syntax to fetch a specific connector by exact "
            "source ID. To find a connector's real tools, call describe_external_tools(source_id, "
            "query=...)."
        ),
        input_model=ListExternalToolsInput,
        handler=list_external_tools,
    ),
    ToolDef(
        name="describe_external_tools",
        description=(
            "Discover and describe a connector's real tools. Never guess slugs. Pass source_id "
            "plus a natural-language query (e.g. 'list pull request reviews') to get the "
            "connector's matching real slugs in 'availableTools'; pass source_id with no query to "
            "list its top tools. Pass exact tool_names to fetch their full input schemas — MUST be "
            "done before call_external_tool. Any name that is not a real slug is returned under "
            "'unresolved', with the connector's real slugs in 'availableTools' to use instead."
        ),
        input_model=DescribeExternalToolsInput,
        handler=describe_external_tools,
        binds_member_authority=False,
    ),
    ToolDef(
        name="search_connector_tools",
        description=(
            "Semantic tool discovery for one connector. Pass source_id plus a natural-language "
            "use case (e.g. 'comment on a pull request') to get matching real tool slugs and "
            "input schemas in 'tools', plus any 'plan' (recommended steps), 'guidance', and "
            "'pitfalls' the connector's broker surfaces for executing them. Richer than "
            "describe_external_tools when you know the goal but not the tool; still call "
            "call_external_tool to run a returned slug."
        ),
        input_model=SearchConnectorToolsInput,
        handler=search_connector_tools,
        binds_member_authority=False,
    ),
    ToolDef(
        name="call_external_tool",
        description=(
            "Execute an external connector tool. PREREQUISITE: Must call describe_external_tools "
            "first to get the input schema. The tool's own parameters go nested under 'arguments', "
            "never at the top level — e.g. {tool_name: 'GITHUB_LIST_PULL_REQUESTS', source_id: "
            "'github', arguments: {owner: 'acme', repo: 'widgets', state: 'open'}}. Pass "
            "account_id when this agent has more than one connected account for the source. A "
            "parameter whose schema asks for 'workspace_file' takes a file from the workspace — "
            'pass {"workspace_file": "/workspace/<path>"} and the file is staged to the '
            "connector automatically. Files a tool returns are saved into the workspace and "
            "listed under 'workspace_files' in the result with their paths. A result field the "
            "provider returned as base64 (e.g. a file's 'content') arrives decoded: small text as "
            "the decoded string in place, anything binary or large as a "
            "{name, workspace_path, mimetype, bytes} reference — read those bytes from "
            "'workspace_path' rather than treating the object as an error. An object the result "
            'repeats identically appears once: later copies are {"same_as": "<JSON Pointer>"}, '
            "identical in every field to the object at that pointer in this same result."
        ),
        input_model=CallExternalToolInput,
        handler=call_external_tool,
        untrusted=True,
        side_effecting=True,
        standing_authorization=call_external_tool_standing_authorization,
    ),
)
