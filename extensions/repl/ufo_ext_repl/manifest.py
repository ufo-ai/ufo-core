"""The persistent-REPL pack: js_repl runs Node, xlsx_repl runs Python with openpyxl, plus the data
skills the agent loads on demand for exploration, SQL, statistics, validation, and visualization.

Each REPL keeps its state in a workspace file holding every successfully run block — new code
executes appended to that state and commits into it only on exit 0, so a failed attempt's
declarations never replay into later calls; a reset discards the state first. js_repl runs the
composed code as an ES module (`.mjs`, so top-level await works) behind the emitImage prelude,
with the global node_modules linked into the resolution path so bare imports of image-installed
packages (playwright) resolve; xlsx_repl runs the composed Python with a footer that JSON-prints
`result` when the code defined it. The work
runs in the sandbox through `ctx.sandbox`, so the container's mount and egress scoping hold; the
combined stdout/stderr and exit code come back, plus any images the JS code handed to `emitImage` —
a prelude-defined global keeping a rolling base64 JSONL window the handler folds into
ImageContent.

The interpreter runs through the same detached task journal the builtin `bash` runs on, so a cell
that outgrows the caller's budget keeps running — its interpreter and whatever it launched, a
headless browser included — and the result hands back the handles that reach it. Because state
commits only on exit 0, an expired cell leaves the REPL exactly where it was, and the result says
so: a blind retry replays the same starting point. Each call emits into a file of its own, so a
survivor still writing images cannot hand them to the call after it."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.o11y import emit_metric, turn_profile
from ufo.sdk.sandbox import ExecResult, shell_path
from ufo.sdk.tools import (
    MAX_COMMAND_TIMEOUT_MS,
    CommandDiagnostics,
    ImageContent,
    TaskRun,
    TextContent,
    ToolContext,
    ToolDef,
    ToolFailure,
    ToolResult,
    run_task,
    task_handles,
    timeout_notice,
)

NAME = "repl"
VERSION = "0.1.0"
JS_REPL_TOOL = "js_repl"
XLSX_REPL_TOOL = "xlsx_repl"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = (
    "data-exploration",
    "data-sql-queries",
    "data-statistical-analysis",
    "data-validation",
    "data-visualization",
)

REPL_STATE_DIR = "repl"
JS_REPL_PATH = f"{REPL_STATE_DIR}/js-repl.js"
JS_RUN_PATH = f"{REPL_STATE_DIR}/js-run.mjs"
XLSX_REPL_PATH = f"{REPL_STATE_DIR}/xlsx-repl.py"
XLSX_RUN_PATH = f"{REPL_STATE_DIR}/xlsx-run.py"
GLOBAL_MODULE_ROOTS = (
    '"$(npm root -g)"',
    '"$NODE_PATH"',
)
REPL_EXIT_CODES = frozenset({0, 1, 124, 126, 127, 130, 137, 139})
OTHER_EXIT_CODE = "other"


def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None:
    """One count per interpreter run, carrying the code it exited on.

    `tool_call_total` already reports a non-zero run, as `handler_error` — but the engine sets
    `error_class` only where an exception was raised, and a REPL that returns a failing result
    raises nothing. So a REPL failing every call reads there exactly like one whose code threw
    once, and the two want opposite fixes. The exit code is what separates them: 127 is an
    interpreter that is not on PATH, 1 is the agent's own code.

    Folded onto a fixed set because a process may exit on any of 256 codes, and an unlisted one
    would mint a series across every other dimension of the metric for a value nothing reads."""
    emit_metric(
        "repl_run_total",
        tool=tool,
        exit_code=str(exit_code) if exit_code in REPL_EXIT_CODES else OTHER_EXIT_CODE,
        profile=turn_profile(ctx.turn.subagent_profile),
    )


def global_modules_link(directory: str, roots: tuple[str, ...] = GLOBAL_MODULE_ROOTS) -> str:
    """The command merging every global root's packages into the run file's resolution path:
    per-package symlinks into the run's `repl/node_modules`, first root wins, a stale whole-dir
    symlink replaced. ESM ignores NODE_PATH by design, so the carrier env only names the image's
    module root — these links make bare imports resolve. An already-linked package is skipped
    by the existence guard; anything else that fails (permissions, read-only mount) exits nonzero
    under `set -e` with stderr intact."""
    modules = f"{directory}/node_modules"
    return (
        "set -e; "
        f"if [ -L {shell_path(modules)} ]; then rm {shell_path(modules)}; fi; "
        f"mkdir -p {shell_path(modules)}; "
        f"for root in {' '.join(roots)}; do "
        '[ -d "$root" ] || continue; '
        'for pkg in "$root"/*; do '
        '[ -e "$pkg" ] || continue; '
        f'dst="{modules}/$(basename "$pkg")"; '
        'if [ ! -e "$dst" ] && [ ! -L "$dst" ]; then ln -s "$pkg" "$dst"; fi; '
        "done; done"
    )


EMIT_IMAGE_LIMIT = 5
EMIT_IMAGE_MAX_B64_CHARS = 2_000_000


def js_emit_relative(call: str) -> str:
    """Where one call's images land, named by the call and workspace-relative: only argv is
    rewritten to the carrier's workspace directory, never a path inside the run file, so the
    prelude resolves this against the interpreter's own working directory.

    Per call because an expired cell keeps running: the process that outgrew its budget holds the
    path it started with, so a write it makes after the result was discarded lands in its own file
    rather than replacing what the next call emitted."""
    return f"{REPL_STATE_DIR}/js-emit-{call}.jsonl"


def js_emit_prelude(emit_relative: str) -> str:
    """The emitImage global, over the emit file this call reads back: a rolling window of the last
    images as base64 JSONL, rewritten whole on every emit."""
    return (
        'import { writeFileSync as __ufoEmitWrite } from "node:fs";\n'
        'import { resolve as __ufoEmitResolve } from "node:path";\n'
        "(() => {\n"
        f"  const EMIT_PATH = __ufoEmitResolve({json.dumps(emit_relative)});\n"
        f"  const MAX_B64 = {EMIT_IMAGE_MAX_B64_CHARS};\n"
        f"  const LIMIT = {EMIT_IMAGE_LIMIT};\n"
        "  const entries = [];\n"
        "  globalThis.emitImage = (value, mediaType) => {\n"
        "    let data = null;\n"
        "    let type = mediaType;\n"
        "    if (Buffer.isBuffer(value) || value instanceof Uint8Array) {\n"
        '      data = Buffer.from(value).toString("base64");\n'
        '    } else if (typeof value === "string") {\n'
        "      data = value;\n"
        '    } else if (value && typeof value === "object") {\n'
        '      data = typeof value.bytes === "string"\n'
        "        ? value.bytes\n"
        '        : Buffer.from(value.bytes).toString("base64");\n'
        "      type = type ?? value.mimeType;\n"
        "    } else {\n"
        '      throw new TypeError("emitImage: pass a Buffer, Uint8Array, base64 string, '
        'or {bytes, mimeType}");\n'
        "    }\n"
        "    if (data.length > MAX_B64) {\n"
        '      throw new RangeError("emitImage: image over " + MAX_B64 + " base64 chars; '
        'use JPEG or lower quality");\n'
        "    }\n"
        "    entries.push("
        'JSON.stringify({ media_type: type || "image/png", data }) + "\\n");\n'
        "    while (entries.length > LIMIT) entries.shift();\n"
        '    __ufoEmitWrite(EMIT_PATH, entries.join(""));\n'
        "  };\n"
        "})();\n"
    )


XLSX_RESULT_FOOTER = (
    "\nimport json as _json\n"
    "try:\n"
    "    print(_json.dumps(result, default=str))\n"
    "except NameError:\n"
    "    pass\n"
)

REPLAY_SEMANTICS = (
    "State advances only on a call that exits 0: a call that fails or expires is discarded whole, "
    "so the next call composes onto the state before it. A retry therefore replays the same "
    "starting point — change the code rather than repeat it."
)
STATE_UNCHANGED = (
    "REPL state did not advance: this call's code is not committed, so the next call composes onto "
    "the state before it."
)
REDECLARED = re.compile(r"Identifier '([^']+)' has already been declared")
"""The name node says was declared twice. The composed module is compiled whole, so this one
message covers two different faults: a collision with a block that ran earlier, which no edit to
this call can clear, and a name this call declares twice itself, which every edit can. Only the
first is what the notice below describes, so the name is read out and checked against the committed
half rather than the message being taken as the answer."""
REDECLARED_NOTICE = (
    "A name this code declares is already declared by state committed earlier, so no edit to this "
    "code compiles. Pass reset: true. Renaming compiles but keeps the earlier state, which runs "
    "again from the top on this and every later call."
)
TIMEOUT_DESCRIPTION = (
    "How long to wait for this call in the foreground, in milliseconds. Max "
    f"{MAX_COMMAND_TIMEOUT_MS} ({MAX_COMMAND_TIMEOUT_MS // 60_000} minutes). Code still running at "
    "the deadline is not stopped — it continues in the background and the result hands back its "
    "task id, log path, and pid, while the REPL state stays where it was."
)
JS_REPL_DESCRIPTION = (
    "Persistent Node.js ES-module REPL for Playwright browser automation and interactive "
    "website/game testing. Use `await import(...)`; CommonJS `require` is unavailable. Variables, "
    "imports, and state persist because every committed call is composed into one module that "
    "runs from the top each time, so an earlier block's navigation and browser launches happen "
    "again on every later call. Every call must exit: close each browser in "
    "`finally` and never keep browser, context, or page handles in globals. Keep browser checks in "
    "this tool; standalone Node scripts do not use its linked modules. The REPL starts "
    "automatically on first use. Call emitImage(value, mediaType?) with a Buffer, Uint8Array, "
    "base64 string, or "
    f"{{bytes, mimeType}} to return images inline in the tool result — up to {EMIT_IMAGE_LIMIT} "
    f"per execution. {REPLAY_SEMANTICS}"
)
XLSX_REPL_DESCRIPTION = (
    "Persistent Python REPL for Excel spreadsheet manipulation using openpyxl. Variables persist "
    "across calls. MUST call load_skill(name='office-xlsx') before first use. Set result = ... to "
    f"return data. {REPLAY_SEMANTICS}"
)


class JsReplInput(BaseModel):
    code: str = Field(
        description="JavaScript ES-module code to execute. Use `await import(...)`; `require` is "
        "unavailable. Variables and imports persist across calls."
    )
    timeout: int | None = Field(default=None, description=TIMEOUT_DESCRIPTION)
    reset: bool | None = Field(
        default=None,
        description="Reset the REPL context and start fresh. Any code provided runs after the "
        "reset.",
    )


class XlsxReplInput(BaseModel):
    code: str = Field(
        description="Python code to execute. Use openpyxl directly for spreadsheet operations. "
        "Variables persist across calls. Set result = ... to return data."
    )
    timeout: int | None = Field(default=None, description=TIMEOUT_DESCRIPTION)
    reset: bool | None = Field(
        default=None, description="Reset REPL state — clears all variables and loaded workbooks."
    )


class ReplStateUnreadable(Exception):
    """The persisted cells could not be read, or a reset could not clear them.

    An unchecked read is the worst outcome this tool has. Its stdout is empty on failure, so the
    candidate becomes this call's code alone — every variable, import and loaded workbook the
    session built silently gone — and the run then succeeds and writes that candidate back as the
    whole state. The loss is permanent and the result says the call worked. So the read is
    checked, nothing runs, and nothing is written."""

    def __init__(self, failure: ToolFailure) -> None:
        super().__init__(failure.summary)
        self.failure = failure


def _state_failed(verb: str, path: str, result: ExecResult) -> ReplStateUnreadable:
    return ReplStateUnreadable(
        ToolFailure(
            operation=f"{verb} the REPL state at {path}",
            summary=(
                f"cannot {verb} {path}. No code ran and no state was written — running past this "
                "would compose your code onto empty state and then commit that over the session's "
                f"own. {STATE_UNCHANGED}"
            ),
            command=CommandDiagnostics(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out_after_s=result.timed_out_after_s,
            ),
        )
    )


@dataclass(frozen=True)
class ReplSource:
    """The module a call runs, and the committed half it was composed onto. They are kept apart
    because a failure naming a redeclared identifier means one thing when that name came from the
    committed half and another when this call declared it twice: the first is cleared only by
    dropping the state, the second by editing the code, and the advice for one destroys the
    session for nothing under the other."""

    source: str
    committed: str


async def _candidate_source(
    ctx: ToolContext, relative: str, path: str, code: str, reset: bool
) -> ReplSource:
    if reset:
        removed = await ctx.sandbox.bash(f"rm -f {shell_path(path)}")
        if removed.exit_code != 0:
            raise _state_failed("clear", path, removed)
    if reset or not await ctx.sandbox.runtime_file_exists(relative):
        return ReplSource(source=code + "\n", committed="")
    existing = await ctx.sandbox.bash(f"cat {shell_path(path)}")
    if existing.exit_code != 0:
        raise _state_failed("read", path, existing)
    return ReplSource(source=existing.stdout + code + "\n", committed=existing.stdout)


DECLARED = (
    r"""(?:^|[;{{}}\n])\s*(?:const|let|var)\b(?:[^;\n=.()"'`]|=[^;\n,()"'`]*,)*\b{name}\b"""
    r"""|(?:^|[;{{}}\n])\s*(?:async\s+)?(?:function|class)\b\s*\*?\s*{name}\b"""
)
"""A declaration of one exact name in the committed half. The name has to be bounded, it has to
follow a declaring keyword, and under `const`, `let` and `var` it has to stand where a name is
bound rather than read: the walk to it crosses no `.`, call or quote, and it crosses an `=` only
to reach the next declarator of the same statement. The committed text is full of the letters of
short names everywhere else. A substring test reads `const i` as colliding with `import` and
`browser` with `browserContext`; a keyword test still reads `title` out of
`const t = await p.title();` and `page` out of `const notice = "reset the page";`. Every one of
those sends the model to reset, which drops the session and leaves the same failure standing."""


def _redeclared_from_state(committed: str, output: str) -> bool:
    """Whether a redeclaration collided with the committed state rather than with this call's own
    code. Reset is the only repair for the first and no repair at all for the second, where it
    would delete the session and leave the code failing exactly as before."""
    match = REDECLARED.search(output)
    if match is None:
        return False
    declared = DECLARED.format(name=re.escape(match.group(1)))
    return re.search(declared, committed, re.MULTILINE) is not None


def _repl_result(
    stdout: str,
    stderr: str,
    exit_code: int,
    images: tuple[ImageContent, ...] = (),
    hint: str = "",
) -> ToolResult:
    """A run the interpreter itself ended. A failing one carries the replay semantics with it: the
    code it just ran is discarded, so a retry starts from the state before it — unsaid, that is how
    one broken block becomes the same failure three times."""
    payload: dict[str, object] = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    if exit_code != 0:
        payload["notice"] = f"{STATE_UNCHANGED} {hint}".strip()
    return ToolResult(
        content=(TextContent(text=json.dumps(payload)), *images),
        is_error=exit_code != 0,
    )


def _expired_result(run: TaskRun, applied_s: int) -> ToolResult:
    """The budget expired, which no exit code can say: code running `timeout` exits 124 exactly as
    a carrier-stopped run does, and "your code is broken" and "your budget was too short" want
    opposite fixes. The interpreter and whatever it launched keep running when they survived, so the
    handles are the way to the output the wait never collected. State is unchanged either way — the
    reason a blind retry buys the same expiry again."""
    if run.pid is None:
        notice = f"{timeout_notice(applied_s, run.requested_s)} {STATE_UNCHANGED}"
        return ToolResult(content=(TextContent(text=notice),), is_error=True)
    handles = task_handles(
        run.task_id,
        run.pid,
        run.display_base,
        applied_s=applied_s,
        note=STATE_UNCHANGED,
    )
    return ToolResult(content=(TextContent(text=handles),))


class EmittedImage(BaseModel):
    media_type: str
    data: str


async def _emitted_images(
    ctx: ToolContext, emit_relative: str, emit_path: str
) -> tuple[ImageContent, ...]:
    if not await ctx.sandbox.runtime_file_exists(emit_relative):
        return ()
    emitted = await ctx.sandbox.bash(f"cat {shell_path(emit_path)}")
    await ctx.sandbox.bash(f"rm -f {shell_path(emit_path)}")
    images = []
    for line in [line for line in emitted.stdout.splitlines() if line][-EMIT_IMAGE_LIMIT:]:
        try:
            image = EmittedImage.model_validate_json(line)
        except ValidationError:
            continue
        images.append(ImageContent(media_type=image.media_type, data=image.data))
    return tuple(images)


async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult:
    state_path = await ctx.sandbox.runtime_path(JS_REPL_PATH)
    run_path = await ctx.sandbox.runtime_path(JS_RUN_PATH)
    state_dir = await ctx.sandbox.runtime_path(REPL_STATE_DIR)
    try:
        candidate = await _candidate_source(
            ctx, JS_REPL_PATH, state_path, args.code, bool(args.reset)
        )
    except ReplStateUnreadable as lost:
        return lost.failure.result()
    emit_relative = js_emit_relative(uuid4().hex[:8])
    emit_path = await ctx.sandbox.runtime_path(emit_relative)
    await ctx.sandbox.write_runtime_file(
        JS_RUN_PATH, js_emit_prelude(emit_path).encode() + candidate.source.encode()
    )
    linked = await ctx.sandbox.bash(global_modules_link(state_dir))
    if linked.exit_code != 0:
        raise OSError(linked.stderr.strip() or "linking global node_modules failed")
    run = await run_task(ctx, f"node {shell_path(run_path)}", args.timeout, model_authored=True)
    _meter_run(ctx, JS_REPL_TOOL, run.result.exit_code)
    if (applied_s := run.result.timed_out_after_s) is not None:
        return _expired_result(run, applied_s)
    if run.result.exit_code == 0:
        await ctx.sandbox.write_runtime_file(JS_REPL_PATH, candidate.source.encode())
    from_state = _redeclared_from_state(
        candidate.committed, f"{run.result.stdout}\n{run.result.stderr}"
    )
    return _repl_result(
        run.result.stdout,
        run.result.stderr,
        run.result.exit_code,
        await _emitted_images(ctx, emit_relative, emit_path),
        REDECLARED_NOTICE if from_state else "",
    )


async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult:
    state_path = await ctx.sandbox.runtime_path(XLSX_REPL_PATH)
    run_path = await ctx.sandbox.runtime_path(XLSX_RUN_PATH)
    try:
        candidate = await _candidate_source(
            ctx, XLSX_REPL_PATH, state_path, args.code, bool(args.reset)
        )
    except ReplStateUnreadable as lost:
        return lost.failure.result()
    await ctx.sandbox.write_runtime_file(
        XLSX_RUN_PATH, candidate.source.encode() + XLSX_RESULT_FOOTER.encode()
    )
    run = await run_task(ctx, f"python3 {shell_path(run_path)}", args.timeout, model_authored=True)
    _meter_run(ctx, XLSX_REPL_TOOL, run.result.exit_code)
    if (applied_s := run.result.timed_out_after_s) is not None:
        return _expired_result(run, applied_s)
    if run.result.exit_code == 0:
        await ctx.sandbox.write_runtime_file(XLSX_REPL_PATH, candidate.source.encode())
    return _repl_result(run.result.stdout, run.result.stderr, run.result.exit_code)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=JS_REPL_TOOL,
                description=JS_REPL_DESCRIPTION,
                input_model=JsReplInput,
                handler=js_repl,
                binds_member_authority=False,
            ),
            ToolDef(
                name=XLSX_REPL_TOOL,
                description=XLSX_REPL_DESCRIPTION,
                input_model=XlsxReplInput,
                handler=xlsx_repl,
                binds_member_authority=False,
            ),
        ),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        sandbox_internet=True,
    )
