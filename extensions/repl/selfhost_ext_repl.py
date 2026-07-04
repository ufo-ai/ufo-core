"""The persistent-REPL pack: js_repl runs Node, xlsx_repl runs Python with openpyxl.

Each REPL keeps its state in a workspace file the pack accumulates across calls — a reset (or the
first call) writes the code fresh, otherwise the new code appends to what ran before, so variables,
imports, and definitions persist. js_repl runs the accumulated Node file; xlsx_repl runs the
accumulated Python file with a footer that JSON-prints `result` when the code defined it. The work
runs in the sandbox through `ctx.sandbox`, so the container's mount and egress scoping hold; only
the combined stdout/stderr and exit code come back."""

import json
import shlex

from pydantic import BaseModel

from selfhost.sdk.manifest import Manifest
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "repl"
VERSION = "0.1.0"
JS_REPL_TOOL = "js_repl"
XLSX_REPL_TOOL = "xlsx_repl"

WORKSPACE_DIR = "/workspace"
REPL_STATE_DIR = f"{WORKSPACE_DIR}/.repl"
JS_REPL_PATH = f"{REPL_STATE_DIR}/js-repl.js"
XLSX_REPL_PATH = f"{REPL_STATE_DIR}/xlsx-repl.py"
XLSX_RUN_PATH = f"{REPL_STATE_DIR}/xlsx-run.py"
REPL_TIMEOUT_SECONDS = 120
XLSX_RESULT_FOOTER = (
    "\nimport json as _json\n"
    "try:\n"
    "    print(_json.dumps(result, default=str))\n"
    "except NameError:\n"
    "    pass\n"
)

JS_REPL_DESCRIPTION = (
    "Persistent Node.js REPL for Playwright browser automation and interactive website/game "
    "testing. Variables, imports, and state persist across calls. The REPL starts automatically on "
    "first use."
)
XLSX_REPL_DESCRIPTION = (
    "Persistent Python REPL for Excel spreadsheet manipulation using openpyxl. Variables persist "
    "across calls. MUST call load_skill(name='office/xlsx') before first use. Set result = ... to "
    "return data."
)


class JsReplInput(BaseModel):
    code: str
    reset: bool | None = None
    user_description: str


class XlsxReplInput(BaseModel):
    code: str
    reset: bool | None = None
    user_description: str | None = None


async def _accumulate(ctx: ToolContext, path: str, code: str, reset: bool) -> None:
    if reset or not await ctx.sandbox.file_exists(path):
        await ctx.sandbox.write_file(path, code.encode() + b"\n")
        return
    existing = await ctx.sandbox.bash(f"cat {shlex.quote(path)}")
    await ctx.sandbox.write_file(path, existing.stdout.encode() + code.encode() + b"\n")


def _repl_result(stdout: str, stderr: str, exit_code: int) -> ToolResult:
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"stdout": stdout, "stderr": stderr, "exit_code": exit_code})
            ),
        ),
        is_error=exit_code != 0,
    )


async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult:
    await _accumulate(ctx, JS_REPL_PATH, args.code, bool(args.reset))
    result = await ctx.sandbox.bash(
        f"node {shlex.quote(JS_REPL_PATH)}", timeout_s=REPL_TIMEOUT_SECONDS
    )
    return _repl_result(result.stdout, result.stderr, result.exit_code)


async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult:
    await _accumulate(ctx, XLSX_REPL_PATH, args.code, bool(args.reset))
    accumulated = await ctx.sandbox.bash(f"cat {shlex.quote(XLSX_REPL_PATH)}")
    await ctx.sandbox.write_file(
        XLSX_RUN_PATH, accumulated.stdout.encode() + XLSX_RESULT_FOOTER.encode()
    )
    result = await ctx.sandbox.bash(
        f"python3 {shlex.quote(XLSX_RUN_PATH)}", timeout_s=REPL_TIMEOUT_SECONDS
    )
    return _repl_result(result.stdout, result.stderr, result.exit_code)


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
            ),
            ToolDef(
                name=XLSX_REPL_TOOL,
                description=XLSX_REPL_DESCRIPTION,
                input_model=XlsxReplInput,
                handler=xlsx_repl,
            ),
        ),
    )
