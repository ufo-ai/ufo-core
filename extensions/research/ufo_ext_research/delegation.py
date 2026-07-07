"""Research delegation: fan a research objective over a list of entities in parallel.

`wide_research` reads an entities file (one per line), dedupes, fans a bounded pool of `research`
children over them through `ctx.spawn` — the same Spawn seam `spawn_subagent` uses, so each run is
scoped to the research profile's tools — and collects their summaries into a workspace JSON file.
It mirrors the browser pack's `wide_browse`, reusing the `research` profile rather than inventing a
third one.

The tool is `side_effecting`, so it carries a stable per-call `idempotency_key`; each child is
spawned under `dedup_key = f"{idempotency_key}/{entity}"`, deterministic across a crash-recovery
re-run of the fan-out. A recovered parent reconnects to the children it already spawned — completed
entities return their memoized result, only the remainder is fresh work."""

import asyncio
import json

from pydantic import BaseModel

from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_research.subagent import RESEARCH_PROFILE_NAME

WIDE_RESEARCH_TOOL_NAME = "wide_research"
MAX_WIDE_RESEARCH_ENTITIES = 128
DEFAULT_SUBAGENT_FANOUT = 8
WIDE_RESEARCH_OUTPUT = "wide_research.json"

WIDE_RESEARCH_DESCRIPTION = (
    "Batch web-research tool. Takes a file with entities, companies, or topics (one per line) and "
    "researches each in PARALLEL using the research subagent to gather structured data. Results "
    "collected into a JSON file."
)


class WideResearchInput(BaseModel):
    entities_file: str
    prompt_template: str
    output_schema_file: str
    user_description: str


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


async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult:
    entities = await _read_lines(ctx, args.entities_file)
    if len(entities) > MAX_WIDE_RESEARCH_ENTITIES:
        raise ValueError(f"wide_research supports at most {MAX_WIDE_RESEARCH_ENTITIES} entities")
    schema = await ctx.sandbox.bash(f"cat {json.dumps(args.output_schema_file)}")
    output_schema = schema.stdout if schema.exit_code == 0 else ""
    semaphore = asyncio.Semaphore(DEFAULT_SUBAGENT_FANOUT)

    async def visit(entity: str) -> dict[str, object]:
        async with semaphore:
            objective = args.prompt_template.replace("{entity}", entity)
            if output_schema.strip():
                objective = f"{objective}\n\nReturn data matching this schema:\n{output_schema}"
            result = await ctx.spawn(
                RESEARCH_PROFILE_NAME,
                {"objective": objective},
                dedup_key=f"{ctx.idempotency_key}/{entity}",
            )
            return {
                "entity": entity,
                "result": "" if result.output is None else result.output.model_dump_json(),
            }

    rows = list(await asyncio.gather(*(visit(entity) for entity in entities)))
    await ctx.sandbox.write_file(WIDE_RESEARCH_OUTPUT, json.dumps(rows, indent=2).encode())
    return ToolResult(
        content=(TextContent(text=json.dumps({"rows": rows, "output_file": WIDE_RESEARCH_OUTPUT})),)
    )


WIDE_RESEARCH_TOOL = ToolDef(
    name=WIDE_RESEARCH_TOOL_NAME,
    description=WIDE_RESEARCH_DESCRIPTION,
    input_model=WideResearchInput,
    handler=_wide_research,
    side_effecting=True,
)
