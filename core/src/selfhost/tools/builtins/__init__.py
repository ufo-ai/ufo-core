"""The builtin tool set: bash, read, write, edit.

Each handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping and egress
rules apply whether a byte arrives via a shell command or a file op. `read` records every path it
returns so `edit` can refuse to touch a file the turn has not read — the guard that keeps a blind
string-replace from clobbering content the model never saw."""

from pydantic import BaseModel

from selfhost.tools.context import TextContent, ToolContext, ToolResult
from selfhost.tools.registry import ToolDef

DEFAULT_READ_LIMIT = 2000


class BashInput(BaseModel):
    command: str


class ReadInput(BaseModel):
    path: str
    offset: int = 0
    limit: int = DEFAULT_READ_LIMIT


class WriteInput(BaseModel):
    path: str
    content: str


class EditInput(BaseModel):
    path: str
    old_string: str
    new_string: str


async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult:
    result = await ctx.sandbox.bash(args.command)
    return ToolResult(
        content=(TextContent(text=result.stdout + result.stderr),),
        is_error=result.exit_code != 0,
    )


async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult:
    lines = (await ctx.sandbox.read_file(args.path)).decode().splitlines()
    ctx.read_paths.add(args.path)
    window = lines[args.offset : args.offset + args.limit]
    if not window:
        text = f"(no lines at offset {args.offset}; file has {len(lines)} lines)"
    else:
        text = "\n".join(window)
    return ToolResult(content=(TextContent(text=text),))


async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult:
    await ctx.sandbox.write_file(args.path, args.content.encode())
    return ToolResult(content=(TextContent(text=f"wrote {args.path}"),))


async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult:
    if args.path not in ctx.read_paths:
        raise ValueError(f"file {args.path} must be read before it is edited")
    current = (await ctx.sandbox.read_file(args.path)).decode()
    occurrences = current.count(args.old_string)
    if occurrences == 0:
        raise ValueError(f"old_string not found in {args.path}")
    if occurrences > 1:
        raise ValueError(f"old_string is not unique in {args.path}: {occurrences} occurrences")
    updated = current.replace(args.old_string, args.new_string)
    await ctx.sandbox.write_file(args.path, updated.encode())
    return ToolResult(content=(TextContent(text=f"edited {args.path}"),))


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="bash",
        description="Run a shell command in the workspace and return its combined output.",
        input_model=BashInput,
        handler=bash_handler,
    ),
    ToolDef(
        name="read",
        description="Read a workspace file, returning a line window from offset up to limit lines.",
        input_model=ReadInput,
        handler=read_handler,
    ),
    ToolDef(
        name="write",
        description="Write content to a workspace file, creating or overwriting it.",
        input_model=WriteInput,
        handler=write_handler,
    ),
    ToolDef(
        name="edit",
        description="Replace a unique string in a workspace file that has already been read.",
        input_model=EditInput,
        handler=edit_handler,
    ),
)
