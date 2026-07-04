"""Browser delegation tools: hand a web-automation objective to the browser subagent.

`browser_task` spawns one `browser` child turn for a full multi-step session and returns its
summary; `wide_browse` reads an entities file (one per line), fans a bounded pool of `browser`
children out over them in parallel, and collects their summaries into a workspace JSON file. Both
reach the child through `ctx.spawn` — the same Spawn seam `spawn_subagent` uses — so a delegated
browser run is scoped to the browser profile's tools, never a raw browser handle."""

import asyncio
import json

from pydantic import BaseModel

from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

BROWSER_PROFILE_NAME = "browser"
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
    url: str
    task: str
    task_name: str
    timeout_minutes: int | None = None
    user_description: str


class WideBrowseInput(BaseModel):
    entities_file: str
    prompt_template: str
    output_schema_file: str
    user_description: str


async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult:
    result = await ctx.spawn(
        BROWSER_PROFILE_NAME, {"task": args.task, "url": args.url, "task_name": args.task_name}
    )
    text = "" if result.output is None else result.output.model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


async def _read_lines(ctx: ToolContext, path: str) -> list[str]:
    read = await ctx.sandbox.bash(f"cat {json.dumps(path)}")
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
    schema = await ctx.sandbox.bash(f"cat {json.dumps(args.output_schema_file)}")
    output_schema = schema.stdout if schema.exit_code == 0 else ""
    semaphore = asyncio.Semaphore(DEFAULT_SUBAGENT_FANOUT)

    async def visit(entity: str) -> dict[str, object]:
        async with semaphore:
            task = args.prompt_template.replace("{entity}", entity)
            if output_schema.strip():
                task = f"{task}\n\nReturn data matching this schema:\n{output_schema}"
            result = await ctx.spawn(BROWSER_PROFILE_NAME, {"task": task, "task_name": entity})
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
    ),
    ToolDef(
        name="wide_browse",
        description=WIDE_BROWSE_DESCRIPTION,
        input_model=WideBrowseInput,
        handler=_wide_browse,
    ),
)
