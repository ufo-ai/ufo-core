"""The persistent-REPL pack: js_repl runs Node, xlsx_repl runs Python with openpyxl, plus the data
skills the agent loads on demand for exploration, SQL, statistics, validation, and visualization.

Each REPL keeps its state in a workspace file the pack accumulates across calls — a reset (or the
first call) writes the code fresh, otherwise the new code appends to what ran before, so variables,
imports, and definitions persist. js_repl runs the accumulated code as an ES module (`.mjs`, so
top-level await works) behind the emitImage prelude; xlsx_repl runs the accumulated Python file
with a footer that JSON-prints `result` when the code defined it. The work
runs in the sandbox through `ctx.sandbox`, so the container's mount and egress scoping hold; the
combined stdout/stderr and exit code come back, plus any images the JS code handed to `emitImage` —
a prelude-defined global keeping a rolling base64 JSONL window the handler folds into
ImageContent."""

import json
import shlex
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.tools import ImageContent, TextContent, ToolContext, ToolDef, ToolResult

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

WORKSPACE_DIR = "/workspace"
REPL_STATE_DIR = f"{WORKSPACE_DIR}/.repl"
JS_REPL_PATH = f"{REPL_STATE_DIR}/js-repl.js"
JS_RUN_PATH = f"{REPL_STATE_DIR}/js-run.mjs"
JS_EMIT_RELATIVE = ".repl/js-emit.jsonl"
JS_EMIT_PATH = f"{WORKSPACE_DIR}/{JS_EMIT_RELATIVE}"
XLSX_REPL_PATH = f"{REPL_STATE_DIR}/xlsx-repl.py"
XLSX_RUN_PATH = f"{REPL_STATE_DIR}/xlsx-run.py"
REPL_TIMEOUT_SECONDS = 120
EMIT_IMAGE_LIMIT = 5
EMIT_IMAGE_MAX_B64_CHARS = 2_000_000
JS_EMIT_PRELUDE = (
    'import { writeFileSync as __ufoEmitWrite } from "node:fs";\n'
    'import { resolve as __ufoEmitResolve } from "node:path";\n'
    "(() => {\n"
    f"  const EMIT_PATH = __ufoEmitResolve({json.dumps(JS_EMIT_RELATIVE)});\n"
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

JS_REPL_DESCRIPTION = (
    "Persistent Node.js REPL for Playwright browser automation and interactive website/game "
    "testing. Variables, imports, and state persist across calls. The REPL starts automatically on "
    "first use. Call emitImage(value, mediaType?) with a Buffer, Uint8Array, base64 string, or "
    f"{{bytes, mimeType}} to return images inline in the tool result — up to {EMIT_IMAGE_LIMIT} "
    "per execution."
)
XLSX_REPL_DESCRIPTION = (
    "Persistent Python REPL for Excel spreadsheet manipulation using openpyxl. Variables persist "
    "across calls. MUST call load_skill(name='office-xlsx') before first use. Set result = ... to "
    "return data."
)


class JsReplInput(BaseModel):
    code: str = Field(
        description="JavaScript code to execute. Variables and imports persist across calls."
    )
    reset: bool | None = Field(
        default=None,
        description="Reset the REPL context and start fresh. Any code provided runs after the "
        "reset.",
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class XlsxReplInput(BaseModel):
    code: str = Field(
        description="Python code to execute. Use openpyxl directly for spreadsheet operations. "
        "Variables persist across calls. Set result = ... to return data."
    )
    reset: bool | None = Field(
        default=None, description="Reset REPL state — clears all variables and loaded workbooks."
    )
    user_description: str | None = Field(
        default=None, description="Brief plain-language description shown in the activity timeline."
    )


async def _accumulate(ctx: ToolContext, path: str, code: str, reset: bool) -> None:
    if reset or not await ctx.sandbox.file_exists(path):
        await ctx.sandbox.write_file(path, code.encode() + b"\n")
        return
    existing = await ctx.sandbox.bash(f"cat {shlex.quote(path)}")
    await ctx.sandbox.write_file(path, existing.stdout.encode() + code.encode() + b"\n")


def _repl_result(
    stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...] = ()
) -> ToolResult:
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"stdout": stdout, "stderr": stderr, "exit_code": exit_code})
            ),
            *images,
        ),
        is_error=exit_code != 0,
    )


class EmittedImage(BaseModel):
    media_type: str
    data: str


async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]:
    if not await ctx.sandbox.file_exists(JS_EMIT_PATH):
        return ()
    emitted = await ctx.sandbox.bash(f"cat {shlex.quote(JS_EMIT_PATH)}")
    images = []
    for line in [line for line in emitted.stdout.splitlines() if line][-EMIT_IMAGE_LIMIT:]:
        try:
            image = EmittedImage.model_validate_json(line)
        except ValidationError:
            continue
        images.append(ImageContent(media_type=image.media_type, data=image.data))
    return tuple(images)


async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult:
    await _accumulate(ctx, JS_REPL_PATH, args.code, bool(args.reset))
    accumulated = await ctx.sandbox.bash(f"cat {shlex.quote(JS_REPL_PATH)}")
    run_source = JS_EMIT_PRELUDE.encode() + accumulated.stdout.encode()
    await ctx.sandbox.write_file(JS_RUN_PATH, run_source)
    await ctx.sandbox.bash(f"rm -f {shlex.quote(JS_EMIT_PATH)}")
    result = await ctx.sandbox.bash(
        f"node {shlex.quote(JS_RUN_PATH)}", timeout_s=REPL_TIMEOUT_SECONDS
    )
    return _repl_result(result.stdout, result.stderr, result.exit_code, await _emitted_images(ctx))


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
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
