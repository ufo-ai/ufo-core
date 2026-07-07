from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image, ImageDraw

from ufo_ext_browser.bua.actions import CLICK_ACTIONS, ComputerAction, ScrollParameters
from ufo_ext_browser.bua.cdp import CdpError
from ufo_ext_browser.bua.coordinate import Coord, Size, model_to_viewport, viewport_to_model
from ufo_ext_browser.bua.downloads import BrowserDownload
from ufo_ext_browser.bua.errors import HallucinationError
from ufo_ext_browser.bua.fixup import fixup_actions, split_at_waits
from ufo_ext_browser.bua.keys import (
    CdpCall,
    KeyboardState,
    modifiers_mask,
    press_combo,
    type_text,
)
from ufo_ext_browser.bua.page import Cdp
from ufo_ext_browser.bua.settle import SETTLE_ACTION_CAP_S, Settle
from ufo_ext_browser.bua.wire import Json, JsonDict, ValidationError, as_list, as_map, as_str

SCREENSHOT_JPEG_QUALITY = 75
CLICK_MARK_RADIUS = 6
CLICK_MARK_RGBA = (0, 100, 255, 180)
MOUSE_BUTTONS = {"left": 1, "right": 2, "middle": 4}
DRAG_STEPS = 6

SCROLL_WARNING = (
    "Avoid scrolling repeatedly. get_page_text or read_page can explore the rest of the page "
    "more efficiently."
)
SIGN_IN_WARNING = (
    "Always confirm with the user before signing in to this site. Never create an account on "
    "the user's behalf."
)
SIGN_IN_KEYWORDS = ("register", "sign up", "log in", "login", "sign into", "sign in")

JS_SELECT_INFO = """
function() {
  if (document.activeElement === this) this.blur();
  const options = Array.from(this.options).map((option) => option.text);
  return {options: options.slice(0, 10), total: options.length};
}
"""


class ComputerTab(Protocol):
    session_id: str
    keyboard: KeyboardState


class BrowserNode(Protocol):
    session_id: str


class BrowserComputerSession(Protocol):
    model_size: Size
    is_mac: bool
    last_batch_scroll_only: bool
    settle: Settle
    downloads: list[BrowserDownload]
    dialogs: list[str]

    async def page(self, tab_id: int | None = None) -> ComputerTab: ...

    def connection(self) -> Cdp: ...

    async def tab_info(self, tab: Any) -> JsonDict: ...

    async def tab_titles(self) -> list[str]: ...

    async def call_on(
        self,
        session_id: str,
        object_id: str,
        function: str,
        arguments: list[Json] | None = None,
    ) -> JsonDict: ...

    def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]: ...

    async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]: ...


@dataclass(frozen=True)
class BrowserComputer:
    browser: BrowserComputerSession
    viewport: Size
    max_wait_seconds: float

    async def run(self, args: JsonDict) -> JsonDict:
        tab = await self.browser.page(int_or_none(args.get("tab_id")))
        raw = [
            ComputerAction.model_validate(as_map(item, "actions[]"))
            for item in as_list(args.get("actions"), "actions")
        ]
        actions = fixup_actions(raw, self.viewport, self.browser.model_size)
        scroll_only = bool(actions) and all(action.action == "scroll" for action in actions)
        warn_scroll = (
            SCROLL_WARNING if self.browser.last_batch_scroll_only and scroll_only else None
        )
        self.browser.last_batch_scroll_only = scroll_only

        messages: list[str] = []
        reminder: str | None = None
        last: tuple[int, int] | None = None
        for batch in split_at_waits(actions):
            self.browser.settle.reset()
            for action in batch:
                message, point = await self.act(tab, action)
                messages.append(message)
                if point is not None:
                    last = point
                    if reminder is None and action.action in CLICK_ACTIONS:
                        reminder = await self._select_reminder(tab, point)
            await self.browser.settle.wait(
                self.browser.connection(), tab.session_id, SETTLE_ACTION_CAP_S
            )

        fresh_downloads = [download for download in self.browser.downloads if not download.drained]
        for download in fresh_downloads:
            download.drained = True
        warn_download = (
            "File download(s) started (guids: "
            f"{', '.join(download.guid for download in fresh_downloads)}). Use "
            "wait_for_download to finish and save them; ignore if this was unintentional."
            if fresh_downloads
            else None
        )
        titles = await self.browser.tab_titles()

        shot = await self.browser.connection().send(
            "Page.captureScreenshot",
            {"format": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY},
            session_id=tab.session_id,
        )
        data = as_str(shot.get("data"), "data")
        if last is not None:
            data = await asyncio.get_running_loop().run_in_executor(None, mark_click, data, last)

        warn_dialog = (
            "JavaScript dialog(s) auto-handled: " + "; ".join(self.browser.dialogs)
            if self.browser.dialogs
            else None
        )
        self.browser.dialogs = []
        output = "; ".join(messages)
        reminders = [reminder, warn_scroll, sign_in_warning(titles), warn_download, warn_dialog]
        for warning in reminders:
            if warning:
                output += f"\n\n<system-reminder>{warning}</system-reminder>"
        return {
            **await self.browser.tab_info(tab),
            "output": output,
            "last_click": list(self._to_model(last)) if last else None,
            "screenshot_base64": data,
        }

    async def act(
        self, tab: ComputerTab, action: ComputerAction
    ) -> tuple[str, tuple[int, int] | None]:
        point = await self.point(tab, action)
        match action.action:
            case "left_click" | "double_click" | "triple_click":
                x, y = require_point(point, action.action)
                count = {"left_click": 1, "double_click": 2, "triple_click": 3}[action.action]
                await self._click(tab, x, y, "left", count)
                label = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[count]
                mx, my = self._to_model((x, y))
                return f"{label} ({mx},{my})", (x, y)
            case "right_click":
                x, y = require_point(point, action.action)
                await self._click(tab, x, y, "right", 1)
                mx, my = self._to_model((x, y))
                return f"Right-clicked ({mx},{my})", (x, y)
            case "left_click_drag":
                x0, y0 = self._to_viewport(
                    require_coord(action.start_coordinate, "start_coordinate")
                )
                x1, y1 = require_point(point, action.action)
                await self._drag(tab, x0, y0, x1, y1)
                start = self._to_model((x0, y0))
                end = self._to_model((x1, y1))
                return f"Dragged {start} -> {end}", (x1, y1)
            case "type":
                if action.text:
                    await self._dispatch(
                        tab, type_text(tab.keyboard, action.text, self.browser.is_mac)
                    )
                return f"Typed {(action.text or '')[:50]!r}", None
            case "key":
                await self._dispatch(
                    tab,
                    press_combo(tab.keyboard, as_str(action.text, "text"), self.browser.is_mac),
                )
                return f"Pressed {action.text}", None
            case "wait":
                duration = min(float(action.duration or 0), self.max_wait_seconds)
                await asyncio.sleep(duration)
                return f"Waited {duration:g}s", None
            case "scroll":
                x, y = point or self._to_viewport(
                    (self.browser.model_size.width // 2, self.browser.model_size.height // 2)
                )
                params = action.scroll_parameters or ScrollParameters()
                amount = params.scroll_amount
                screens = 99.0 if amount == "max" else float(amount)
                direction = params.scroll_direction
                dx = (
                    (1 if direction == "right" else -1 if direction == "left" else 0)
                    * self.viewport.width
                    * screens
                )
                dy = (
                    (1 if direction == "down" else -1 if direction == "up" else 0)
                    * self.viewport.height
                    * screens
                )
                await self._scroll(tab, x, y, dx, dy)
                return f"Scrolled {direction}", None
            case "scroll_to":
                node, backend_id = self.browser.resolve_ref(tab, as_str(action.ref, "ref"))
                try:
                    await self.browser.connection().send(
                        "DOM.scrollIntoViewIfNeeded",
                        {"backendNodeId": backend_id},
                        session_id=node.session_id,
                    )
                except CdpError:
                    raise HallucinationError(
                        f"browser ref {action.ref!r} is not resolvable: re-read the page and "
                        "use a ref it returns"
                    ) from None
                return f"Scrolled to {action.ref}", None
            case "screenshot":
                return "Screenshot taken", None
            case _:
                raise ValidationError(f"unknown browser action {action.action!r}")

    async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None:
        if action.ref:
            return await self.browser.ref_point(tab, action.ref)
        if action.coordinate is not None:
            return self._to_viewport(action.coordinate)
        return None

    def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]:
        vp = model_to_viewport(Coord(coord[0], coord[1]), self.viewport, self.browser.model_size)
        return vp.x, vp.y

    def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]:
        point = viewport_to_model(Coord(coord[0], coord[1]), self.viewport, self.browser.model_size)
        return point.x, point.y

    async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None:
        conn = self.browser.connection()
        try:
            result = await conn.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        f"(() => {{ let el = document.elementFromPoint({point[0]}, {point[1]}); "
                        'while (el && el.tagName !== "SELECT") el = el.parentElement; '
                        "return el; })()"
                    ),
                    "returnByValue": False,
                },
                session_id=tab.session_id,
            )
            object_id = as_map(result.get("result"), "result").get("objectId")
            if not isinstance(object_id, str):
                return None
            info = await self.browser.call_on(tab.session_id, object_id, JS_SELECT_INFO)
            described = await conn.send(
                "DOM.describeNode", {"objectId": object_id}, session_id=tab.session_id
            )
        except (CdpError, RuntimeError):
            return None
        options = [str(option) for option in as_list(info.get("options") or [], "options")]
        total_raw = info.get("total")
        total = total_raw if isinstance(total_raw, int) else len(options)
        backend = as_map(described.get("node"), "node").get("backendNodeId")
        ref = f"e{backend}" if isinstance(backend, int) else None
        return select_reminder(ref, options, total)

    async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None:
        for method, params in calls:
            await self.browser.connection().send(method, params, session_id=tab.session_id)

    async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None:
        await self.browser.connection().send(
            "Input.dispatchMouseEvent", params, session_id=tab.session_id
        )

    async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None:
        modifiers = modifiers_mask(tab.keyboard.pressed_modifiers)
        await self._mouse_event(
            tab,
            {
                "type": "mouseMoved",
                "button": "none",
                "buttons": 0,
                "x": x,
                "y": y,
                "modifiers": modifiers,
                "force": 0,
            },
        )
        for count in range(1, click_count + 1):
            await self._mouse_event(
                tab,
                {
                    "type": "mousePressed",
                    "button": button,
                    "buttons": MOUSE_BUTTONS[button],
                    "x": x,
                    "y": y,
                    "modifiers": modifiers,
                    "clickCount": count,
                    "force": 0.5,
                },
            )
            await self._mouse_event(
                tab,
                {
                    "type": "mouseReleased",
                    "button": button,
                    "buttons": 0,
                    "x": x,
                    "y": y,
                    "modifiers": modifiers,
                    "clickCount": count,
                },
            )

    async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None:
        """Move in steps: a single jump skips the threshold that starts HTML5 drag-and-drop and the
        intermediate dragover a sortable/canvas target expects."""
        modifiers = modifiers_mask(tab.keyboard.pressed_modifiers)
        await self._mouse_event(
            tab,
            {
                "type": "mouseMoved",
                "button": "none",
                "buttons": 0,
                "x": x0,
                "y": y0,
                "modifiers": modifiers,
                "force": 0,
            },
        )
        await self._mouse_event(
            tab,
            {
                "type": "mousePressed",
                "button": "left",
                "buttons": 1,
                "x": x0,
                "y": y0,
                "modifiers": modifiers,
                "clickCount": 1,
                "force": 0.5,
            },
        )
        for step in range(1, DRAG_STEPS + 1):
            await self._mouse_event(
                tab,
                {
                    "type": "mouseMoved",
                    "button": "left",
                    "buttons": 1,
                    "x": int(x0 + (x1 - x0) * step / DRAG_STEPS),
                    "y": int(y0 + (y1 - y0) * step / DRAG_STEPS),
                    "modifiers": modifiers,
                    "force": 0.5,
                },
            )
        await self._mouse_event(
            tab,
            {
                "type": "mouseReleased",
                "button": "left",
                "buttons": 0,
                "x": x1,
                "y": y1,
                "modifiers": modifiers,
                "clickCount": 1,
            },
        )

    async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None:
        modifiers = modifiers_mask(tab.keyboard.pressed_modifiers)
        await self._mouse_event(
            tab,
            {
                "type": "mouseMoved",
                "button": "none",
                "buttons": 0,
                "x": x,
                "y": y,
                "modifiers": modifiers,
                "force": 0,
            },
        )
        await self._mouse_event(
            tab,
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": dx,
                "deltaY": dy,
                "modifiers": modifiers,
            },
        )


def sign_in_warning(titles: list[str]) -> str | None:
    lowered = [title.lower() for title in titles]
    if any(keyword in title for title in lowered for keyword in SIGN_IN_KEYWORDS):
        return SIGN_IN_WARNING
    return None


def select_reminder(ref: str | None, options: list[str], total: int) -> str:
    shown = ", ".join(f'"{option}"' for option in options)
    if total > len(options):
        shown += f", ... and {total - len(options)} more"
    how = (
        f"Call form_input with ref='{ref}' and the option text as the value."
        if ref
        else "Find the select's ref with read_page filter='interactive', then call form_input."
    )
    return (
        "You clicked a native <select> dropdown; clicking its options does not work in this "
        f"browser. {how} Options: [{shown}]"
    )


def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str:
    image = Image.open(BytesIO(base64.b64decode(screenshot_b64))).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    x, y = point
    ImageDraw.Draw(overlay).ellipse(
        (
            x - CLICK_MARK_RADIUS,
            y - CLICK_MARK_RADIUS,
            x + CLICK_MARK_RADIUS,
            y + CLICK_MARK_RADIUS,
        ),
        fill=CLICK_MARK_RGBA,
    )
    marked = Image.alpha_composite(image, overlay).convert("RGB")
    out = BytesIO()
    marked.save(out, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY)
    return base64.b64encode(out.getvalue()).decode()


def int_or_none(value: Json | None) -> int | None:
    match value:
        case int():
            return value
        case float():
            return int(value)
        case str() if value:
            return int(value)
        case _:
            return None


def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]:
    if point is None:
        raise ValidationError(f"{action} requires coordinate or ref")
    return point


def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]:
    if value is None:
        raise ValidationError(f"{path} is required")
    return value
