"""The browser tools: one per action of the agent's browser/computer-use surface.

Each tool validates its arguments and drives them through `ctx.browser` — the turn's live
`BrowserSurface`, yielded by whichever browser backend the deploy selected (core's default BUA
engine over a CDP endpoint, or an extension backend). A tool never holds a raw browser or CDP
handle; the backend owns the connection and closes it at turn end. `computer` (when asked) and
`wait_for_download` persist bytes into the shared workspace through the sandbox, so the parent agent
and sibling subagents reach them by path."""

import base64
import json
from typing import Literal

from pydantic import BaseModel, JsonValue

from selfhost.sdk.browser import BrowserSurface
from selfhost.sdk.tools import ImageContent, TextContent, ToolContext, ToolDef, ToolResult

DEFAULT_SCREENSHOT_PATH = "browser-screenshot.jpg"
DEFAULT_DOWNLOAD_DIR = "downloads"
SCREENSHOT_MEDIA_TYPE = "image/jpeg"


class NavigateInput(BaseModel):
    url: str
    user_description: str
    tab_id: int | None = None


class TabsContextInput(BaseModel):
    pass


class TabsCreateInput(BaseModel):
    user_description: str
    url: str | None = None


class TabsCloseInput(BaseModel):
    tab_id: int | None = None


class UploadFileInput(BaseModel):
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


def _browser(ctx: ToolContext) -> BrowserSurface:
    if ctx.browser is None:
        raise RuntimeError("no browser backend is configured for this turn")
    return ctx.browser


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
    reply = await _browser(ctx).tabs_close(args.model_dump(mode="json", exclude_none=True))
    return _json_result(reply)


async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult:
    reply = await _browser(ctx).upload_file(args.model_dump(mode="json", exclude_none=True))
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
    screenshot = reply.get("screenshot_base64")
    if not isinstance(screenshot, str) or not screenshot:
        return _json_result(reply)
    rest = {key: value for key, value in reply.items() if key != "screenshot_base64"}
    return ToolResult(
        content=(
            TextContent(text=json.dumps(rest)),
            ImageContent(media_type=SCREENSHOT_MEDIA_TYPE, data=screenshot),
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


BROWSER_TOOLS: tuple[ToolDef, ...] = (
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
            "Interact with the browser using mouse, keyboard, wait, scroll, and screenshot actions."
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

BROWSER_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in BROWSER_TOOLS)
