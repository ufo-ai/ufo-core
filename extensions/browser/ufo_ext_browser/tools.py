"""The browser tools: one per action of the agent's browser/computer-use surface.

Each tool validates its arguments and drives them through the turn's one `BuaSurface` — the BUA
engine built lazily on first browser-tool call from `ctx.cdp_provider` (the deploy's selected CDP
transport) and `ctx.find` (the host-side ranking hook). The surface is cached for the turn and its
`aclose` registered on `ctx.cleanup`, so the CDP connection and any hosted-session lease open once
and release at turn end regardless of how the turn ends. `computer` (when asked) and
`wait_for_download` persist bytes into the shared workspace through the sandbox, so the parent agent
and sibling subagents reach them by path."""

import base64
import json
from dataclasses import replace
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import BaseModel, Field, JsonValue

from ufo.sdk.tools import ImageContent, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_browser.bua.backend import BuaSurface

DEFAULT_SCREENSHOT_PATH = "browser-screenshot.jpg"
DEFAULT_DOWNLOAD_DIR = "downloads"
SCREENSHOT_MEDIA_TYPE = "image/jpeg"

_TURN_SURFACES: "WeakKeyDictionary[object, BuaSurface]" = WeakKeyDictionary()


class NavigateInput(BaseModel):
    url: str
    user_description: str
    tab_id: int | None = None


class TabsContextInput(BaseModel):
    user_description: str = Field(
        description="What you are checking about the pages you have open, in plain language for "
        "the activity timeline."
    )


class TabsCreateInput(BaseModel):
    user_description: str
    url: str | None = None


class TabsCloseInput(BaseModel):
    user_description: str = Field(
        description="Which page you are done with, in plain language for the activity timeline."
    )
    tab_id: int | None = None


class UploadFileInput(BaseModel):
    user_description: str = Field(
        description="What you are uploading and where, in plain language for the activity "
        "timeline. Name the document, never the path."
    )
    ref: str
    files: tuple[str, ...]
    tab_id: int | None = None


class ReadPageInput(BaseModel):
    user_description: str
    depth: int | None = None
    filter: Literal["all", "interactive", "viewport"] | None = None
    ref_id: str | None = None
    tab_id: int | None = None


class GetPageTextInput(BaseModel):
    user_description: str
    tab_id: int | None = None


class FindInput(BaseModel):
    user_description: str
    query: str
    tab_id: int | None = None


class FormInputInput(BaseModel):
    user_description: str
    ref: str
    value: JsonValue
    tab_id: int | None = None


class ComputerInput(BaseModel):
    actions: tuple[dict[str, JsonValue], ...]
    user_description: str
    tab_id: int | None = None
    save_to_workspace: bool | None = None
    path: str | None = None


class WaitForDownloadInput(BaseModel):
    user_description: str
    guid: str | None = None
    path: str | None = None
    timeout: int | None = None


def _browser(ctx: ToolContext) -> BuaSurface:
    """The turn's one browser surface: built lazily on first browser-tool call from the selected cdp
    provider, cached for the turn (keyed by its per-turn cleanup registry), and registered on that
    registry so its CDP connection and any hosted-session lease release at turn end. Later calls in
    the turn reuse it. The surface carries the turn's sandbox (so a per-conversation-sandbox
    provider resolves Chrome inside it), the extension's scoped store, and the turn's
    conversation id so it can persist the lease's reattach token and reconnect the same session on
    a recovered turn; a call without an extension context (a bare test harness) simply mints fresh
    each turn."""
    surface = _TURN_SURFACES.get(ctx.cleanup)
    if surface is None:
        if ctx.cdp_provider is None:
            raise RuntimeError("no cdp provider is configured for this turn")
        surface = BuaSurface(
            cdp_provider=ctx.cdp_provider,
            find_completer=ctx.find,
            model=ctx.agent.model,
            sandbox=ctx.sandbox,
            store=ctx.ext.store if ctx.ext is not None else None,
            conversation_id=ctx.turn.conversation_id,
        )
        _TURN_SURFACES[ctx.cleanup] = surface
        ctx.cleanup.register(surface.aclose)
    return surface


def _json_result(reply: dict[str, JsonValue]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(reply)),))


def _required_str(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"browser reply is missing {field}")
    return value


async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult:
    reply = await _browser(ctx).navigate(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult:
    return _json_result(await _browser(ctx).tabs_context({}))


async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult:
    reply = await _browser(ctx).tabs_create({"url": args.url or "about:blank"})
    return _json_result(reply)


async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult:
    reply = await _browser(ctx).tabs_close(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult:
    reply = await _browser(ctx).upload_file(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult:
    reply = await _browser(ctx).read_page(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult:
    reply = await _browser(ctx).get_page_text(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _find(ctx: ToolContext, args: FindInput) -> ToolResult:
    reply = await _browser(ctx).find(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult:
    reply = await _browser(ctx).form_input(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    return _json_result(reply)


async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult:
    reply = await _browser(ctx).computer(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    if args.save_to_workspace is True:
        path = args.path or DEFAULT_SCREENSHOT_PATH
        screenshot = _required_str(reply.get("screenshot_base64"), "screenshot_base64")
        await ctx.sandbox.write_file(path, base64.b64decode(screenshot))
        reply["screenshot_path"] = path
    screenshot_base64 = reply.get("screenshot_base64")
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        return _json_result(reply)
    rest = {key: value for key, value in reply.items() if key != "screenshot_base64"}
    return ToolResult(
        content=(
            TextContent(text=json.dumps(rest)),
            ImageContent(media_type=SCREENSHOT_MEDIA_TYPE, data=screenshot_base64),
        )
    )


async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult:
    download = await _browser(ctx).wait_for_download(
        args.model_dump(mode="json", exclude_none=True, exclude={"user_description"})
    )
    filename = _required_str(download.get("filename"), "filename")
    content = _required_str(download.get("content_base64"), "content_base64")
    target = f"{(args.path or DEFAULT_DOWNLOAD_DIR).rstrip('/')}/{filename}"
    await ctx.sandbox.write_file(target, base64.b64decode(content))
    return _json_result({"file_path": target, "filename": filename, "size": download.get("size")})


BROWSER_TOOLS: tuple[ToolDef, ...] = tuple(
    replace(tool, profile_only=True)
    for tool in (
        ToolDef(
            name="navigate",
            description="Navigate to a URL, or go forward/back in browser history.",
            input_model=NavigateInput,
            handler=_navigate,
        ),
        ToolDef(
            name="tabs_context",
            description="Get context for all browser tabs.",
            input_model=TabsContextInput,
            handler=_tabs_context,
            untrusted=True,
        ),
        ToolDef(
            name="tabs_create",
            description="Create a browser tab.",
            input_model=TabsCreateInput,
            handler=_tabs_create,
        ),
        ToolDef(
            name="tabs_close",
            description="Close a browser tab.",
            input_model=TabsCloseInput,
            handler=_tabs_close,
        ),
        ToolDef(
            name="upload_file",
            description="Set a file input from workspace paths.",
            input_model=UploadFileInput,
            handler=_upload_file,
        ),
        ToolDef(
            name="read_page",
            description="Read the browser page accessibility tree.",
            input_model=ReadPageInput,
            handler=_read_page,
            untrusted=True,
        ),
        ToolDef(
            name="get_page_text",
            description="Extract raw text from the browser page.",
            input_model=GetPageTextInput,
            handler=_get_page_text,
            untrusted=True,
        ),
        ToolDef(
            name="find",
            description="Find browser page elements by role, text, name, or URL.",
            input_model=FindInput,
            handler=_find,
            untrusted=True,
        ),
        ToolDef(
            name="form_input",
            description="Set a form value by browser ref.",
            input_model=FormInputInput,
            handler=_form_input,
        ),
        ToolDef(
            name="computer",
            description=(
                "Interact with the browser using mouse, keyboard, wait, scroll, and screenshot "
                "actions."
            ),
            input_model=ComputerInput,
            handler=_computer,
        ),
        ToolDef(
            name="wait_for_download",
            description="Wait for a browser download and write it to the workspace.",
            input_model=WaitForDownloadInput,
            handler=_wait_for_download,
        ),
    )
)

BROWSER_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in BROWSER_TOOLS)
