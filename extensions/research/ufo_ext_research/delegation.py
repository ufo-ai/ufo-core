"""Research delegation: fan a research objective over a list of entities in parallel.

`wide_research` reads an entities file (one per line), dedupes, fans a bounded pool of `research`
children over them through `ctx.spawn` — the same Spawn seam the `spawn` tool uses, so each run is
scoped to the research profile's tools — and collects their JSON results into a workspace file.
It mirrors the browser pack's `wide_browse`, reusing the `research` profile rather than inventing a
third one.

The tool is `side_effecting`, so it carries a stable per-call `idempotency_key`; each child is
spawned under `dedup_key = f"{idempotency_key}/{entity}"`, deterministic across a crash-recovery
re-run of the fan-out. A recovered parent reconnects to the children it already spawned — completed
entities return their memoized result, only the remainder is fresh work."""

import asyncio
import json
import shlex
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from ufo.sdk.tools import (
    CommandDiagnostics,
    TextContent,
    ToolContext,
    ToolDef,
    ToolFailure,
    ToolResult,
)
from ufo_ext_research.subagent import RESEARCH_PROFILE_NAME, ResearchOutput

WIDE_RESEARCH_TOOL_NAME = "wide_research"
MAX_WIDE_RESEARCH_ENTITIES = 128
DEFAULT_SUBAGENT_FANOUT = 8
WIDE_RESEARCH_OUTPUT = "wide_research.json"
WIDE_RESEARCH_RECOVERY_PREFIX = "/workspace/.wide-research-aggregate-"
WIDE_RESEARCH_ERROR_MAX_CHARS = 500

WIDE_RESEARCH_DESCRIPTION = (
    "Batch web-research tool. Takes a file with entities, companies, or topics (one per line) and "
    "researches each in PARALLEL using the research subagent to gather structured data. Results "
    "collected into a JSON file."
)


class WideResearchInput(BaseModel):
    entities_file: str
    prompt_template: str
    output_schema_file: str


class WideResearchRow(BaseModel):
    entity: str
    result: JsonValue | None = None
    error: str = Field(default="", max_length=WIDE_RESEARCH_ERROR_MAX_CHARS)


class WideResearchFile(BaseModel):
    untrusted: Literal[True] = True
    source: Literal["wide_research"] = "wide_research"
    call_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: tuple[WideResearchRow, ...]


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


@dataclass(frozen=True)
class _WideResearch:
    ctx: ToolContext
    args: WideResearchInput
    entities: list[str]
    call_id: str
    recovery_path: str
    recovery_staging_path: str
    output_schema: str
    result_paths: dict[str, str]
    recovered_rows: dict[str, WideResearchRow]
    persisted_result_paths: set[str]
    completed_rows: dict[str, WideResearchRow]
    semaphore: asyncio.Semaphore
    aggregate_lock: asyncio.Lock

    async def run(self) -> ToolResult:
        """One entity's fault is that entity's row, never the batch's.

        Every sibling has already been paid for — a spawned child burned model tokens whether or
        not the entity beside it could start one — so a raise out of the fan-out throws away work
        that is finished and hands back a single message about the one that failed. The failure is
        recorded as that entity's `error` and the rest of the file stands, which is also what the
        recovery aggregate already holds row by row. A row's `error` is never empty either: an
        exception raised bare leaves `str()` empty, and an empty error beside an empty result
        is a row that reads as though the entity was never attempted."""
        self.ctx.cleanup.register(self._remove_result_files)
        visited = await asyncio.gather(
            *(self._visit(entity) for entity in self.entities), return_exceptions=True
        )
        collected: list[WideResearchRow] = []
        for entity, outcome in zip(self.entities, visited, strict=True):
            if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
                raise outcome
            collected.append(
                WideResearchRow(
                    entity=entity,
                    error=(str(outcome).strip() or type(outcome).__name__)[
                        :WIDE_RESEARCH_ERROR_MAX_CHARS
                    ],
                )
                if isinstance(outcome, Exception)
                else outcome
            )
        rows = tuple(collected)
        output = WideResearchFile(call_id=self.call_id, rows=rows).model_dump(mode="json")
        await self._install_recovery(rows)
        await self.ctx.sandbox.write_file(
            WIDE_RESEARCH_OUTPUT, json.dumps(output, indent=2).encode()
        )
        return ToolResult(
            content=(TextContent(text=json.dumps({**output, "output_file": WIDE_RESEARCH_OUTPUT})),)
        )

    async def _remove_result_files(self) -> None:
        if not self.persisted_result_paths:
            return
        removed = await self.ctx.sandbox.bash(
            "rm -f -- " + " ".join(shlex.quote(path) for path in self.persisted_result_paths)
        )
        if removed.exit_code != 0:
            raise OSError(removed.stderr.strip() or "cannot remove wide research result files")

    async def _install_recovery(self, rows: tuple[WideResearchRow, ...]) -> None:
        aggregate = WideResearchFile(call_id=self.call_id, rows=rows).model_dump(mode="json")
        await self.ctx.sandbox.write_file(
            self.recovery_staging_path,
            json.dumps(aggregate, indent=2).encode(),
        )
        installed = await self.ctx.sandbox.bash(
            f"mv -f -- {shlex.quote(self.recovery_staging_path)} {shlex.quote(self.recovery_path)}"
        )
        if installed.exit_code != 0:
            raise OSError(installed.stderr.strip() or "cannot install wide research recovery file")

    async def _save_row(self, row: WideResearchRow) -> None:
        async with self.aggregate_lock:
            self.completed_rows[row.entity] = row
            await self._install_recovery(
                tuple(
                    self.completed_rows[entity]
                    for entity in self.entities
                    if entity in self.completed_rows
                )
            )
            self.persisted_result_paths.add(self.result_paths[row.entity])

    async def _visit(self, entity: str) -> WideResearchRow:
        async with self.semaphore:
            if entity in self.recovered_rows:
                return self.recovered_rows[entity]
            idempotency_key = self.ctx.idempotency_key
            if idempotency_key is None:
                raise RuntimeError("wide_research requires a call idempotency key")
            child_key = f"{idempotency_key}/{entity}"
            result_path = self.result_paths[entity]
            objective = self.args.prompt_template.replace("{entity}", entity)
            if self.output_schema.strip():
                objective = (
                    f"{objective}\n\nWrite the complete result as JSON to {result_path}. It must "
                    f"match this schema:\n{self.output_schema}\n\nReturn only the result path."
                )
            else:
                objective = (
                    f"{objective}\n\nWrite the complete result as JSON to {result_path}. Return "
                    "only the result path."
                )
            spawned = await self.ctx.spawn(
                f"profile:{RESEARCH_PROFILE_NAME}",
                {"objective": objective},
                dedup_key=child_key,
            )
            read = await self.ctx.sandbox.bash(f"cat {shlex.quote(result_path)}")
            if read.exit_code != 0:
                error = (read.stderr.strip() or f"cannot read {result_path}")[
                    :WIDE_RESEARCH_ERROR_MAX_CHARS
                ]
                if spawned.output is not None:
                    child_result = ResearchOutput.model_validate(spawned.output.model_dump()).result
                    error = f"{error}; child: {child_result}"[:WIDE_RESEARCH_ERROR_MAX_CHARS]
                row = WideResearchRow(entity=entity, error=error)
                await self._save_row(row)
                return row
            try:
                result = json.loads(read.stdout)
            except json.JSONDecodeError as error:
                row = WideResearchRow(
                    entity=entity,
                    error=f"{result_path} is not JSON: {error}"[:WIDE_RESEARCH_ERROR_MAX_CHARS],
                )
                await self._save_row(row)
                return row
            row = WideResearchRow(entity=entity, result=result)
            await self._save_row(row)
            return row


async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult:
    if ctx.idempotency_key is None:
        raise RuntimeError("wide_research requires a call idempotency key")
    entities = await _read_lines(ctx, args.entities_file)
    if len(entities) > MAX_WIDE_RESEARCH_ENTITIES:
        raise ValueError(f"wide_research supports at most {MAX_WIDE_RESEARCH_ENTITIES} entities")
    call_id = sha256(ctx.idempotency_key.encode()).hexdigest()
    recovery_path = f"{WIDE_RESEARCH_RECOVERY_PREFIX}{ctx.turn.id}-{call_id}.json"
    recovery_staging_path = f"{recovery_path}.tmp"
    current_turn_pattern = f".wide-research-aggregate-{ctx.turn.id}-*"
    pruned = await ctx.sandbox.bash(
        "find /workspace -maxdepth 1 -type f "
        f"-name {shlex.quote('.wide-research-aggregate-*')} "
        f"! -name {shlex.quote(current_turn_pattern)} -delete"
    )
    if pruned.exit_code != 0:
        raise OSError(pruned.stderr.strip() or "cannot prune wide research recovery files")
    recovered_rows: dict[str, WideResearchRow] = {}
    previous = await ctx.sandbox.bash(f"cat {shlex.quote(recovery_path)}")
    recovered: WideResearchFile | None = None
    if previous.exit_code == 0:
        try:
            candidate = json.loads(previous.stdout)
            recovered = WideResearchFile.model_validate(candidate)
        except (json.JSONDecodeError, ValidationError):
            recovered = None
        if recovered is not None and recovered.call_id == call_id:
            recovered_rows = {row.entity: row for row in recovered.rows}
    schema = await ctx.sandbox.bash(f"cat {shlex.quote(args.output_schema_file)}")
    if schema.exit_code != 0:
        return ToolFailure(
            operation=f"read the output schema at {args.output_schema_file}",
            summary=(
                f"cannot read {args.output_schema_file}, and the schema is the contract every "
                "child writes its result against. No child was started — read without it they "
                "would each answer in a shape of their own, and the file they wrote would report "
                "success. Fix the path and call again."
            ),
            command=CommandDiagnostics(
                exit_code=schema.exit_code,
                stdout=schema.stdout,
                stderr=schema.stderr,
                timed_out_after_s=schema.timed_out_after_s,
            ),
        ).result()
    output_schema = schema.stdout
    semaphore = asyncio.Semaphore(DEFAULT_SUBAGENT_FANOUT)
    result_paths = {
        entity: (
            "/workspace/.wide-research-"
            f"{sha256(f'{ctx.idempotency_key}/{entity}'.encode()).hexdigest()}.json"
        )
        for entity in entities
    }
    persisted_result_paths = {
        result_paths[entity] for entity in recovered_rows if entity in result_paths
    }
    return await _WideResearch(
        ctx,
        args,
        entities,
        call_id,
        recovery_path,
        recovery_staging_path,
        output_schema,
        result_paths,
        recovered_rows,
        persisted_result_paths,
        dict(recovered_rows),
        semaphore,
        asyncio.Lock(),
    ).run()


WIDE_RESEARCH_TOOL = ToolDef(
    name=WIDE_RESEARCH_TOOL_NAME,
    description=WIDE_RESEARCH_DESCRIPTION,
    input_model=WideResearchInput,
    handler=_wide_research,
    side_effecting=True,
    untrusted=True,
)
