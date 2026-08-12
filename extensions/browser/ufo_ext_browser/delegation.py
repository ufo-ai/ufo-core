"""Browser delegation tools: hand a web-automation objective to the browser subagent.

`browser_task` spawns one `browser` child turn for a full multi-step session and returns its
summary; the child runs in the background while the tool awaits it under the call's
`timeout_minutes` budget, cancelling a session that outlives it, so a wedged website or a runaway
automation loop never holds the parent turn open. `wide_browse` reads an entities file (one per
line), fans a bounded pool of `browser` children out over them in parallel, and collects their
summaries into a workspace JSON file. Both reach the child through `ctx.spawn` — the same Spawn
seam `spawn_subagent` uses — so a delegated browser run is scoped to the browser profile's tools,
never a raw browser handle.

A budget is bounded at both ends: below so a wedged site gets a fair run, above because the browser
a run holds is one leased session, and a transport that reaps its own sessions would drop the live
connection mid-task rather than ending it through the graceful cancel path below.

The round budget divides the same way. `browser_task` takes the profile's own — the main agent's
ceiling, for a session that pages through a site — while `wide_browse` sends `extended_context`
off, dropping each of its up-to-128 children to the ordinary subagent budget: a fan-out entity is
one short extraction, and 128 of them at the main ceiling is a spend no single call should reach.

Both tools are `side_effecting`: `browser_task` keys its one child on the call's
`idempotency_key`, `wide_browse` spawns each child under `dedup_key =
f"{idempotency_key}/{entity}"` — deterministic across a crash-recovery re-run, so a recovered
parent reconnects to the children already spawned rather than respawning them."""

import asyncio
import json
import shlex

from pydantic import BaseModel, Field

from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_browser.subagent import BrowserResult

BROWSER_PROFILE_NAME = "browser"
BROWSER_TASK_TIMEOUT_FLOOR_MINUTES = 20
BROWSER_TASK_TIMEOUT_CEILING_MINUTES = 60
MAX_WIDE_BROWSE_ENTITIES = 128
DEFAULT_SUBAGENT_FANOUT = 8
WIDE_BROWSE_OUTPUT = "wide_browse.json"

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
        le=BROWSER_TASK_TIMEOUT_CEILING_MINUTES,
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
    if ctx.subagents is None:
        raise RuntimeError("subagent control is not available in this context")
    spawned = await ctx.spawn(
        BROWSER_PROFILE_NAME,
        {"task": args.task, "url": args.url, "task_name": args.task_name},
        background=True,
        dedup_key=ctx.idempotency_key,
    )
    timed_out = ToolResult(
        content=(
            TextContent(
                text=f"browser task {args.task_name!r} exceeded its "
                f"{args.timeout_minutes}-minute timeout and was cancelled"
            ),
        ),
        is_error=True,
    )
    try:
        async with asyncio.timeout(args.timeout_minutes * 60):
            (status,) = await ctx.subagents.wait((spawned.turn_id,))
    except TimeoutError:
        await ctx.subagents.cancel(spawned.turn_id)
        return timed_out
    if status.status == "cancelled":
        return timed_out
    if status.status != "done":
        raise RuntimeError(f"subagent {BROWSER_PROFILE_NAME!r} turn ended {status.status}")
    text = BrowserResult.model_validate_json(status.text).model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


async def _read_lines(ctx: ToolContext, path: str) -> list[str]:
    """Shell-quoted, not JSON-quoted: `sandbox.bash` execs a real shell, and JSON escaping leaves
    `$(…)` and backticks live inside the double quotes."""
    read = await ctx.sandbox.bash(f"cat {shlex.quote(path)}")
    if read.exit_code != 0:
        raise ValueError(read.stderr.strip() or f"cannot read {path}")
    seen: set[str] = set()
    entities: list[str] = []
    for line in read.stdout.splitlines():
        entity = line.strip()
        if entity and entity not in seen:
            seen.add(entity)
            entities.append(entity)
    return entities


async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult:
    entities = await _read_lines(ctx, args.entities_file)
    if len(entities) > MAX_WIDE_BROWSE_ENTITIES:
        raise ValueError(f"wide_browse supports at most {MAX_WIDE_BROWSE_ENTITIES} entities")
    schema = await ctx.sandbox.bash(f"cat {shlex.quote(args.output_schema_file)}")
    output_schema = schema.stdout if schema.exit_code == 0 else ""
    semaphore = asyncio.Semaphore(DEFAULT_SUBAGENT_FANOUT)

    async def visit(entity: str) -> dict[str, object]:
        async with semaphore:
            task = args.prompt_template.replace("{entity}", entity)
            if output_schema.strip():
                task = f"{task}\n\nReturn data matching this schema:\n{output_schema}"
            result = await ctx.spawn(
                BROWSER_PROFILE_NAME,
                {"task": task, "task_name": entity, "extended_context": False},
                dedup_key=f"{ctx.idempotency_key}/{entity}",
            )
            return {
                "entity": entity,
                "result": "" if result.output is None else result.output.model_dump_json(),
            }

    rows = list(await asyncio.gather(*(visit(entity) for entity in entities)))
    await ctx.sandbox.write_file(WIDE_BROWSE_OUTPUT, json.dumps(rows, indent=2).encode())
    return ToolResult(
        content=(TextContent(text=json.dumps({"rows": rows, "output_file": WIDE_BROWSE_OUTPUT})),)
    )


DELEGATION_TOOLS: tuple[ToolDef, ...] = (
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
