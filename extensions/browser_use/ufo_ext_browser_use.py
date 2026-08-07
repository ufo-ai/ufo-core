"""The `browser_use` pack: web automation bought whole from Browser Use's hosted agent.

It answers `browser_task` and `wide_browse` — the same names carrying the same inputs the `browser`
pack publishes — out of the v4 REST API, so a deploy picks one web-automation stack by listing this
extension in place of `browser` and its cdp provider. `ToolRegistry` rejects duplicate tool names at
boot, so the two can never load together and no config decides between them. Keeping the names is
what lets the research subagent and the eval runners, which name `browser_task` as a string, work
unchanged across the swap.

Their agent is the whole loop here — model, browser, and recovery — so this pack ships no raw
browser tools and no browser subagent, and the key it reads host-side per run through the scoped
`CredentialAccess` never enters the sandbox. Only a run's own output files cross into the workspace,
downloaded over a keyless client so the presigned storage host never sees our credential.

A run is bounded by `maxCostUsd`, in dollars, rather than by a step count. `POST /runs` carries no
domain allowlist and no secret map, so the task text is the only instrument that scopes where the
hosted agent may browse — a workspace-level domain bound has nothing to attach to on this API."""

import asyncio
import json
import shlex
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from ufo.sdk.context import CredentialAccess, ScopedStore
from ufo.sdk.manifest import CredentialSlot, Manifest, PromptSection
from ufo.sdk.sandbox import WORKSPACE_DIR, ContainmentError, contained_relative
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "browser_use"
VERSION = "0.1.0"

API_BASE = "https://api.browser-use.com/api/v4"
API_KEY_SLOT = "browser_use_api_key"
API_KEY_HEADER = "X-Browser-Use-API-Key"
HTTPS_PREFIX = "https://"
REQUEST_TIMEOUT_SECONDS = 30
POLL_SECONDS = 3
PROXY_COUNTRY = "us"

TASK_MODEL = "claude-sonnet-5"
TASK_MAX_COST_USD = 2.0
WIDE_BROWSE_MODEL = "gemini-3.5-flash"
WIDE_BROWSE_MAX_COST_USD = 0.5

MAX_TASK_CHARS = 20_000
MAX_OUTPUT_FILES = 5
MAX_OUTPUT_BYTES = 10_000_000
MAX_WIDE_BROWSE_ENTITIES = 128
WIDE_BROWSE_FANOUT = 8
WIDE_BROWSE_OUTPUT = "wide_browse.json"
BROWSER_TASK_TIMEOUT_FLOOR_MINUTES = 20
WIDE_BROWSE_TIMEOUT_MINUTES = 20

RUN_COMPLETED = "completed"
TERMINAL_STATUSES = frozenset({RUN_COMPLETED, "failed", "stopped", "cancelled"})
RUN_TIMED_OUT = "timed_out"
RUN_ERRORED = "errored"

SECTION_NAME = "browser"
SECTION_BODY = (Path(__file__).parent / "browser_use_section.md").read_text().strip()

BROWSER_USE_TRANSPORT: httpx.AsyncBaseTransport | None = None

BROWSER_TASK_DESCRIPTION = (
    "Automates a full browser session: navigating websites, filling forms, clicking buttons, "
    "extracting information, multi-step web actions. Runs in an isolated cloud browser (no saved "
    "sessions/cookies). Each call starts a FRESH session — include ALL context in the task "
    "description since the agent has no conversation history. Cannot manipulate browser extensions."
)
WIDE_BROWSE_DESCRIPTION = (
    "Batch browser automation tool. Takes a file with URLs or site names (one per line) and "
    "visits each in PARALLEL using browser automation to extract structured data. Results "
    "collected into a JSON file."
)


class BrowserUseError(RuntimeError):
    """Browser Use answered a non-2xx status, a body the run flow cannot read, or an output path
    that would escape the workspace — surfaced with the status and body, never a silent empty
    answer."""


@dataclass(frozen=True)
class RunFile:
    path: str
    size: int


class StartedRun(BaseModel):
    """The handle a created run is reached by. It outlives the process in `ext_store`, so a keyed
    fan-out can reattach after a crash — persisted state, validated on the way back in.
    `timed_out` records that this key's own attempt cancelled the run at its deadline, so a
    reattach reports the timeout rather than reading the vendor's stop as its own conclusion."""

    id: str
    workspace_id: str
    timed_out: bool = False


@dataclass(frozen=True)
class RunOutcome:
    """What one finished run leaves behind. `output` is the run's result when it completed and its
    error text otherwise, so a caller always has something to say about it.

    `status` is the vendor's terminal status, except for `timed_out`, which is ours alone: a run
    the vendor itself cancelled reports `cancelled` and still carries whatever it collected, so the
    two must not share a name. A reattached run whose stored handle records this key's own
    deadline cancel reports `timed_out` like the attempt that cancelled it did."""

    status: str
    output: str
    saved: tuple[RunFile, ...]
    skipped: tuple[RunFile, ...]
    more_files: bool


@dataclass(frozen=True)
class HostedRun:
    """One Browser Use run, start to finish: create it, watch it to a terminal status, read its
    result, and carry its own output files into the workspace.

    `model`, `max_cost_usd` and `save_outputs` are the caller's policy, not the run's —
    `browser_task` buys one careful session and keeps its files, `wide_browse` buys many cheap ones
    and keeps only the rows it collects. `transport` is the httpx testability seam; production
    leaves it None."""

    credentials: CredentialAccess
    model: str
    max_cost_usd: float
    save_outputs: bool
    transport: httpx.AsyncBaseTransport | None = None

    async def execute(
        self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None = None
    ) -> RunOutcome:
        if len(task) > MAX_TASK_CHARS:
            raise ValueError(
                f"browser task is {len(task)} characters, over the {MAX_TASK_CHARS} sent to a run"
            )
        if ctx.ext is None:
            raise RuntimeError("browser_use tools need their extension context")
        store = ctx.ext.store
        key = await self.credentials.get(API_KEY_SLOT)
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=self.transport,
            headers={API_KEY_HEADER: key},
        ) as http:
            run = await self._start(http, store, task, dedup_key)
            try:
                async with asyncio.timeout(timeout_seconds):
                    status = await self._watch(http, run.id)
            except TimeoutError:
                status = await self._status(http, run.id)
                if status not in TERMINAL_STATUSES:
                    if dedup_key is not None:
                        await store.put(
                            dedup_key, run.model_copy(update={"timed_out": True}).model_dump()
                        )
                    await self._json(await http.post(f"/runs/{run.id}/cancel"))
                    return RunOutcome(RUN_TIMED_OUT, "", (), (), False)
            if run.timed_out and status in ("stopped", "cancelled"):
                return RunOutcome(RUN_TIMED_OUT, "", (), (), False)
            summary = await self._json(await http.get(f"/runs/{run.id}"))
            saved, skipped, more_files = await self._collect(http, ctx, run.workspace_id)
            output = summary.get("result") if status == RUN_COMPLETED else summary.get("error")
            return RunOutcome(status, str(output or ""), saved, skipped, more_files)

    async def _start(
        self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None
    ) -> StartedRun:
        """The run this call owns: the one a previous attempt already paid for when a dedup key
        names it, otherwise a fresh one recorded under that key. A crash between creating the run
        and recording it re-runs, which is the narrowest window the API allows — there is no way to
        name a run before it exists."""
        if dedup_key is not None:
            found = await store.get(dedup_key)
            if found is not None:
                return StartedRun.model_validate(found)
        created = await self._json(
            await http.post(
                "/runs",
                json={
                    "task": task,
                    "model": self.model,
                    "maxCostUsd": self.max_cost_usd,
                    "browserSettings": {"proxyCountryCode": PROXY_COUNTRY},
                },
            )
        )
        run = StartedRun(
            id=self._text(created, "id"), workspace_id=self._text(created, "workspaceId")
        )
        if dedup_key is not None:
            await store.put(dedup_key, run.model_dump())
        return run

    async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str:
        while True:
            status = await self._status(http, run_id)
            if status in TERMINAL_STATUSES:
                return status
            await asyncio.sleep(POLL_SECONDS)

    async def _status(self, http: httpx.AsyncClient, run_id: str) -> str:
        """Read once. The deadline can fall in a sleep between polls, so the timeout path reads
        again before cancelling — a run that finished in those last seconds is already paid for and
        keeps its result."""
        return self._text(await self._json(await http.get(f"/runs/{run_id}/status")), "status")

    async def _collect(
        self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str
    ) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]:
        """The run's own output files, written into the conversation workspace. A file past the
        size bound is reported rather than written, and a listing the count bound cut short says so
        through the vendor's own `hasMore`, so the caller can always say what it did not fetch; a
        path that would escape the workspace is a fault, not a file — decided by the shared
        containment guard rather than by a lexical check of this pack's own, with the carrier's
        write proving the path against the filesystem after it, so a directory the vendor names
        cannot be a link the agent left in the workspace."""
        if not self.save_outputs:
            return (), (), False
        listed = await self._json(
            await http.get(
                f"/workspaces/{workspace_id}/files",
                params={"includeUrls": "true", "limit": MAX_OUTPUT_FILES},
            )
        )
        files = listed.get("files")
        if not isinstance(files, list):
            raise BrowserUseError(f"browser-use file listing has no files array: {listed!r}")
        saved: list[RunFile] = []
        skipped: list[RunFile] = []
        for info in files:
            if not isinstance(info, dict):
                raise BrowserUseError(f"browser-use listed a non-object file: {info!r}")
            path, size = info.get("path"), info.get("size")
            if not isinstance(path, str) or not isinstance(size, int):
                raise BrowserUseError(
                    f"browser-use listed a file without a path and size: {info!r}"
                )
            entry = RunFile(path, size)
            try:
                scoped = contained_relative(entry.path, WORKSPACE_DIR)
            except ContainmentError as error:
                raise BrowserUseError(
                    f"run output path escapes the workspace: {entry.path!r}"
                ) from error
            url = info.get("url")
            if entry.size > MAX_OUTPUT_BYTES or not isinstance(url, str):
                skipped.append(entry)
                continue
            await ctx.sandbox.write_file(scoped, await self._download(url))
            saved.append(entry)
        return tuple(saved), tuple(skipped), bool(listed.get("hasMore"))

    async def _download(self, url: str) -> bytes:
        """Read one presigned output file over a client carrying no credential of ours — the
        storage host is a different origin from the API and never sees the key.

        The URL is the vendor's to choose, so it is fetched only over https: a listing that named a
        plaintext internal address would otherwise have its body written into the workspace."""
        if not url.startswith(HTTPS_PREFIX):
            raise BrowserUseError(f"run output url is not https: {url!r}")
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, transport=self.transport
        ) as raw:
            response = await raw.get(url)
        if response.status_code >= 400:
            raise BrowserUseError(
                f"browser-use output download failed ({response.status_code}): {response.text}"
            )
        return response.content

    @staticmethod
    async def _json(response: httpx.Response) -> dict[str, object]:
        if response.status_code >= 400:
            raise BrowserUseError(
                f"browser-use {response.request.url.path} failed "
                f"({response.status_code}): {response.text}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise BrowserUseError(
                f"browser-use {response.request.url.path} answered a non-JSON body: "
                f"{response.text!r}"
            ) from error
        if not isinstance(body, dict):
            raise BrowserUseError(f"browser-use answered a non-object body: {body!r}")
        return {str(key): value for key, value in body.items()}

    @staticmethod
    def _text(body: dict[str, object], key: str) -> str:
        """One required string field off a response, faulting as a BrowserUseError like every other
        unreadable body in this flow rather than as a bare KeyError."""
        value = body.get(key)
        if not isinstance(value, str):
            raise BrowserUseError(f"browser-use response has no {key}: {body!r}")
        return value


class BrowserTaskInput(BaseModel):
    url: str = Field(description="The starting URL for the browser session.")
    task: str = Field(
        description="Detailed description of what to do. Must be self-contained — include all "
        "relevant context, preferences, and step-by-step instructions. The browser agent has no "
        "conversation history."
    )
    task_name: str = Field(
        description="Short, user-friendly name for this task, e.g. 'Search flights' or 'Extract "
        "pricing'."
    )
    timeout_minutes: int = Field(
        default=BROWSER_TASK_TIMEOUT_FLOOR_MINUTES,
        ge=BROWSER_TASK_TIMEOUT_FLOOR_MINUTES,
        description="Wall-clock budget for the whole session; the task is cancelled when it "
        "expires.",
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class WideBrowseInput(BaseModel):
    entities_file: str = Field(
        description="Path to a workspace file containing URLs or site names to browse, one per "
        "line. Duplicates are ignored."
    )
    prompt_template: str = Field(
        description="Prompt template for each browser task. Use {entity} as the placeholder for "
        "each URL/site name."
    )
    output_schema_file: str = Field(
        description="Path to a workspace JSON file containing the output JSON Schema. Write the "
        "schema to a file first with the write tool, then pass the path here. The file must be a "
        "JSON object defining the output structure with snake_case property keys and 'title' on "
        "each property."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("browser_use tools need their extension context")
    outcome = await HostedRun(
        credentials=ctx.ext.credentials,
        model=TASK_MODEL,
        max_cost_usd=TASK_MAX_COST_USD,
        save_outputs=True,
        transport=BROWSER_USE_TRANSPORT,
    ).execute(
        ctx,
        f"Start at {args.url}\n\n{args.task}",
        timeout_seconds=args.timeout_minutes * 60,
        dedup_key=None if ctx.idempotency_key is None else f"run/{ctx.idempotency_key}",
    )
    if outcome.status == RUN_TIMED_OUT:
        return ToolResult(
            content=(
                TextContent(
                    text=f"browser task {args.task_name!r} exceeded its "
                    f"{args.timeout_minutes}-minute timeout and was cancelled"
                ),
            ),
            is_error=True,
        )
    payload = {
        "result": outcome.output,
        "files": [file.path for file in outcome.saved],
        "files_not_fetched": [{"path": f.path, "size": f.size} for f in outcome.skipped],
        "more_files_exist": outcome.more_files,
    }
    return ToolResult(
        content=(TextContent(text=json.dumps(payload)),),
        is_error=outcome.status != RUN_COMPLETED,
    )


async def _read_file(ctx: ToolContext, path: str) -> str:
    """`sandbox.bash` runs its command through a real shell, and the path is model-supplied, so it
    is shell-quoted rather than JSON-quoted — JSON escaping leaves `$(…)`, backticks and `$VAR`
    live inside the double quotes."""
    read = await ctx.sandbox.bash(f"cat {shlex.quote(path)}")
    if read.exit_code != 0:
        raise ValueError(read.stderr.strip() or f"cannot read {path}")
    return read.stdout


async def _read_lines(ctx: ToolContext, path: str) -> list[str]:
    seen: set[str] = set()
    entities: list[str] = []
    for line in (await _read_file(ctx, path)).splitlines():
        entity = line.strip()
        if entity and entity not in seen:
            seen.add(entity)
            entities.append(entity)
    return entities


async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("browser_use tools need their extension context")
    entities = await _read_lines(ctx, args.entities_file)
    if len(entities) > MAX_WIDE_BROWSE_ENTITIES:
        raise ValueError(f"wide_browse supports at most {MAX_WIDE_BROWSE_ENTITIES} entities")
    output_schema = await _read_file(ctx, args.output_schema_file)
    semaphore = asyncio.Semaphore(WIDE_BROWSE_FANOUT)
    runner = HostedRun(
        credentials=ctx.ext.credentials,
        model=WIDE_BROWSE_MODEL,
        max_cost_usd=WIDE_BROWSE_MAX_COST_USD,
        save_outputs=False,
        transport=BROWSER_USE_TRANSPORT,
    )

    async def visit(entity: str) -> dict[str, object]:
        """One entity's run. A fault here is that entity's row, never the batch's: the siblings are
        already paid for, so a single vendor hiccup must not throw their results away."""
        async with semaphore:
            task = args.prompt_template.replace("{entity}", entity)
            if output_schema.strip():
                task = f"{task}\n\nReturn data matching this schema:\n{output_schema}"
            outcome = await runner.execute(
                ctx,
                task,
                timeout_seconds=WIDE_BROWSE_TIMEOUT_MINUTES * 60,
                dedup_key=None
                if ctx.idempotency_key is None
                else f"run/{ctx.idempotency_key}/{entity}",
            )
            return {"entity": entity, "status": outcome.status, "result": outcome.output}

    visited = await asyncio.gather(*(visit(entity) for entity in entities), return_exceptions=True)
    rows: list[dict[str, object]] = []
    for entity, outcome in zip(entities, visited, strict=True):
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
        rows.append(
            {"entity": entity, "status": RUN_ERRORED, "result": str(outcome)}
            if isinstance(outcome, Exception)
            else outcome
        )
    await ctx.sandbox.write_file(WIDE_BROWSE_OUTPUT, json.dumps(rows, indent=2).encode())
    return ToolResult(
        content=(TextContent(text=json.dumps({"rows": rows, "output_file": WIDE_BROWSE_OUTPUT})),)
    )


BROWSER_USE_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="browser_task",
        description=BROWSER_TASK_DESCRIPTION,
        input_model=BrowserTaskInput,
        handler=_browser_task,
        untrusted=True,
        side_effecting=True,
    ),
    ToolDef(
        name="wide_browse",
        description=WIDE_BROWSE_DESCRIPTION,
        input_model=WideBrowseInput,
        handler=_wide_browse,
        untrusted=True,
        side_effecting=True,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=BROWSER_USE_TOOLS,
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        credentials=(
            CredentialSlot(
                name=API_KEY_SLOT,
                description=(
                    "BYOK Browser Use API key; the run flow reads it in-process, host-side, to "
                    "drive the hosted browser agent."
                ),
            ),
        ),
    )
