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
footer it renders, and a connector send reaches no renderer of ours.

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
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ufo.sdk.connectors import (
    WORKSPACE_FILE_KEY,
    BrokerFile,
    BrokerTool,
    ConnectorEntry,
    ConnectorRegistry,
    UnknownBrokerTool,
)
from ufo.sdk.context import JsonValue
from ufo.sdk.sandbox import WORKSPACE_DIR, workspace_path
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

CONNECTOR_FILES_DIR = "connector_files"
FALLBACK_FILENAME = "download"
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

UFO_ATTRIBUTION = "Sent using ufo"
SLACK_PROVIDER = "slack"
SLACK_MESSAGE_NOUN = "message"
SLACK_SEND_VERBS = ("send", "post", "reply", "schedule")
SLACK_MESSAGE_TEXT_ARGUMENTS = ("markdown_text", "text")

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
import hashlib, sys
h = hashlib.md5(usedforsecurity=False)
size = 0
with open(sys.argv[1], "rb") as f:
    while chunk := f.read(1048576):
        h.update(chunk)
        size += len(chunk)
print(h.hexdigest())
print(size)
"""


class ListExternalToolsInput(BaseModel):
    queries: tuple[str, ...] = Field(
        max_length=MAX_LIST_QUERIES,
        description="Search keywords. Use single-word queries — split multi-word searches into "
        "separate keywords, e.g. ['Microsoft', 'email'] not ['Microsoft email']. Multiple queries "
        "searched in parallel. Use 'select:<source_id>' to fetch a specific connector by exact ID.",
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
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
    user_description: str = Field(
        description="Which connected account you are checking what you can do with, in plain "
        "language for the activity timeline."
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
    user_description: str = Field(
        description="What you are doing in the connected account, in plain language for the "
        "activity timeline. Name the account, never the tool slug."
    )


class SearchConnectorToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID to search within.")
    query: str = Field(description="Search query to find matching tools in the connector.")
    user_description: str = Field(
        description="What you are hoping the connected account can do, in plain language for the "
        "activity timeline."
    )


async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult:
    registry = _registry(ctx)
    matches: list[dict[str, str]] = []
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
            matches.append({"source_id": provider, "label": entry.label})
    catalogs = await asyncio.gather(
        *(registry.search_catalog(target, CATALOG_SEARCH_LIMIT) for target in targets)
    )
    for rows in catalogs:
        for row in rows:
            if row.provider in seen:
                continue
            seen.add(row.provider)
            matches.append({"source_id": row.provider, "label": row.label})
    return _json_result({"connectors": matches})


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


def slack_attributed(
    provider: str, slug: str, arguments: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """`arguments` with the ufo attribution appended to the message text of a Slack send — the one
    connector call that publishes a message this deploy wrote, and the only place it can be marked:
    the Slack surface's own reply carries its footer, a message posted through a connector passes
    through no renderer of ours. A read, an edit, and a listing are untouched, as is a text already
    carrying the line, so a resend or an edit of a marked message never stacks it."""
    if provider != SLACK_PROVIDER:
        return arguments
    name = slug.lower()
    if SLACK_MESSAGE_NOUN not in name or not any(verb in name for verb in SLACK_SEND_VERBS):
        return arguments
    attributed = dict(arguments)
    for key in SLACK_MESSAGE_TEXT_ARGUMENTS:
        text = arguments.get(key)
        if isinstance(text, str) and text.strip() and UFO_ATTRIBUTION not in text:
            attributed[key] = f"{text}\n\n{UFO_ATTRIBUTION}"
    return attributed


async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    account_id = await ctx.connector_account(args.source_id, args.account_id)
    arguments = slack_attributed(entry.provider, args.tool_name, args.arguments)
    call = _ConnectorCall(ctx=ctx, entry=entry, slug=args.tool_name)
    return ToolResult(content=(TextContent(text=await call.run(arguments, account_id)),))


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

    async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str:
        staged = {key: await self._staged_value(item) for key, item in arguments.items()}
        response = await self.entry.broker.execute(
            self.ctx.turn.workspace_id,
            self.entry.provider,
            self.slug,
            staged,
            account_id,
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
        preflight = await self.ctx.sandbox.bash(
            f"python3 -c {shlex.quote(MD5_PREFLIGHT_PROG)} {shlex.quote(scoped)}",
            timeout_s=TRANSFER_TIMEOUT_SECONDS,
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
        sandbox — under a fresh `connector_files/<uuid>/` so no fetch clobbers another file."""
        saved: list[dict[str, str]] = []
        for file in files:
            basename = PurePosixPath(file.name.replace("\\", "/")).name
            safe = basename if basename not in ("", ".", "..") else FALLBACK_FILENAME
            target = f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/{uuid4()}/{safe}"
            fetched = await self.ctx.sandbox.bash(
                f"curl -fsSL --create-dirs --max-filesize {TRANSFER_MAX_BYTES} "
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
        basename = PurePosixPath(name.replace("\\", "/")).name
        safe = basename if basename not in ("", ".", "..") else FALLBACK_FILENAME
        target = f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/{hashlib.sha256(data).hexdigest()}/{safe}"
        staged = f"{target}.{uuid4()}.part"
        await self.ctx.sandbox.write_file(staged, data)
        placed = await self.ctx.sandbox.bash(
            f"mv -f {shlex.quote(staged)} {shlex.quote(target)}", timeout_s=TRANSFER_TIMEOUT_SECONDS
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
            "label. Call this before claiming you can't access something — there is very likely a "
            "connector available. Use 'select:<source_id>' syntax to fetch a specific connector by "
            "exact source ID. To find a connector's real tools, call "
            "describe_external_tools(source_id, query=...)."
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
    ),
)
