from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol

from selfhost.browser.cdp import CdpError
from selfhost.browser.coordinate import Size
from selfhost.browser.downloads import FETCH_DOCUMENT_PATTERN, BrowserDownload, BrowserDownloads
from selfhost.browser.keys import KeyboardState
from selfhost.browser.page import FrameNode
from selfhost.browser.settle import PAINT_LIFECYCLE_EVENTS, SETTLE_NAV_CAP_S, Settle
from selfhost.browser.wire import Json, JsonDict, ValidationError, as_list, as_map, as_str

NAVIGATION_TIMEOUT_S = 30.0
HISTORY_TIMEOUT_S = 10.0


def normalize_url(url: str) -> str:
    if url in {"back", "forward", "about:blank"}:
        return url
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return url
    return f"https://{url}"


class Tab:
    def __init__(self, target_id: str, session_id: str) -> None:
        self.target_id = target_id
        self.session_id = session_id
        self.keyboard = KeyboardState()
        self.frame_seqs: dict[str, int] = {}
        self.ref_frames: dict[str, FrameNode] = {"": FrameNode(target_id, session_id, (0.0, 0.0))}

    def frame_seq(self, frame_id: str) -> int:
        if frame_id not in self.frame_seqs:
            self.frame_seqs[frame_id] = len(self.frame_seqs) + 1
        return self.frame_seqs[frame_id]


@dataclass
class BrowserTabEvents:
    initial_targets: set[str] = field(default_factory=set)
    created_targets: list[str] = field(default_factory=list)
    destroyed_targets: set[str] = field(default_factory=set)


class BrowserTabCdp(Protocol):
    async def send(
        self,
        method: str,
        params: JsonDict | None = None,
        session_id: str | None = None,
    ) -> JsonDict: ...

    def expect(self, *events: str, session_id: str | None = None) -> asyncio.Future[JsonDict]: ...

    async def wait(
        self,
        future: asyncio.Future[JsonDict],
        timeout: float,  # noqa: ASYNC109
    ) -> JsonDict: ...


class BrowserTabSession(Protocol):
    tabs: list[Tab]
    oop_sessions: dict[str, str]
    downloads: list[BrowserDownload]
    settle: Settle
    tab_events: BrowserTabEvents

    def connection(self) -> BrowserTabCdp: ...

    def download_reader(self) -> BrowserDownloads: ...

    async def eval_js(self, session_id: str, expression: str) -> Json: ...


@dataclass(frozen=True)
class BrowserTabs:
    browser: BrowserTabSession
    viewport: Size

    def remember_initial_targets(self, targets: JsonDict) -> None:
        self.browser.tab_events.initial_targets = {
            str(as_map(info, "targetInfos[]").get("targetId"))
            for info in as_list(targets.get("targetInfos"), "targetInfos")
        }

    def on_target_created(self, params: JsonDict, session_id: str | None) -> None:
        info = params.get("targetInfo")
        if not isinstance(info, dict):
            return
        target_id = info.get("targetId")
        events = self.browser.tab_events
        if (
            info.get("type") == "page"
            and isinstance(target_id, str)
            and target_id not in events.initial_targets
            and target_id not in events.created_targets
        ):
            events.created_targets.append(target_id)

    def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None:
        target_id = params.get("targetId")
        if isinstance(target_id, str):
            self.browser.tab_events.destroyed_targets.add(target_id)

    def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None:
        if session_id is None:
            return
        if self.is_top_level_frame(session_id, params.get("frameId")):
            self.browser.settle.mark_loading(session_id)

    def on_dom_content(self, params: JsonDict, session_id: str | None) -> None:
        if session_id is not None:
            self.browser.settle.mark_loaded(session_id)

    def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None:
        if session_id is None or params.get("name") not in PAINT_LIFECYCLE_EVENTS:
            return
        if self.is_top_level_frame(session_id, params.get("frameId")):
            self.browser.settle.mark_painted(session_id)

    def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool:
        return any(
            tab.session_id == session_id and tab.target_id == frame_id for tab in self.browser.tabs
        )

    async def attach_tab(self, target_id: str) -> Tab:
        conn = self.browser.connection()
        attached = await conn.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session_id = as_str(attached.get("sessionId"), "sessionId")
        await self.init_session(session_id)
        await conn.send(
            "Fetch.enable", {"patterns": [FETCH_DOCUMENT_PATTERN]}, session_id=session_id
        )
        await conn.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.viewport.width,
                "height": self.viewport.height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
            session_id=session_id,
        )
        return Tab(target_id, session_id)

    async def init_session(self, session_id: str) -> None:
        conn = self.browser.connection()
        await conn.send("Page.enable", session_id=session_id)
        await conn.send("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=session_id)
        await conn.send("DOM.enable", session_id=session_id)
        await conn.send("Network.enable", session_id=session_id)

    async def sync(self) -> None:
        events = self.browser.tab_events
        if events.destroyed_targets:
            gone = events.destroyed_targets
            events.destroyed_targets = set()
            self.browser.tabs = [tab for tab in self.browser.tabs if tab.target_id not in gone]
        while events.created_targets:
            target_id = events.created_targets.pop(0)
            if any(tab.target_id == target_id for tab in self.browser.tabs):
                continue
            try:
                self.browser.tabs.append(await self.attach_tab(target_id))
            except CdpError:
                continue

    async def page(self, tab_id: int | None = None) -> Tab:
        await self.sync()
        if not self.browser.tabs:
            created = await self.browser.connection().send(
                "Target.createTarget", {"url": "about:blank"}
            )
            self.browser.tabs.append(
                await self.attach_tab(as_str(created.get("targetId"), "targetId"))
            )
        if tab_id is None:
            return self.browser.tabs[-1]
        if tab_id < 0 or tab_id >= len(self.browser.tabs):
            raise ValidationError(f"tab_id {tab_id} is not open")
        return self.browser.tabs[tab_id]

    async def navigate(self, url: str, tab_id: int | None = None) -> JsonDict:
        tab = await self.page(tab_id)
        target = normalize_url(url)
        self.browser.settle.reset()
        match target:
            case "back":
                await self._history_step(tab, -1)
            case "forward":
                await self._history_step(tab, 1)
            case _:
                await self._goto(tab, target)
        await self.browser.settle.wait(self.browser.connection(), tab.session_id, SETTLE_NAV_CAP_S)
        return await self.tab_info(tab)

    async def _goto(self, tab: Tab, url: str) -> None:
        conn = self.browser.connection()
        downloads_before = len(self.browser.downloads)
        loaded = conn.expect("Page.domContentEventFired", session_id=tab.session_id)
        result = await conn.send("Page.navigate", {"url": url}, session_id=tab.session_id)
        error_text = result.get("errorText")
        if error_text:
            loaded.cancel()
            if await self.browser.download_reader().became_download(downloads_before):
                return
            raise RuntimeError(f"browser navigation to {url!r} failed: {error_text}")
        if result.get("loaderId") is None:
            loaded.cancel()
            return
        await conn.wait(loaded, NAVIGATION_TIMEOUT_S)

    async def _history_step(self, tab: Tab, step: int) -> None:
        conn = self.browser.connection()
        history = await conn.send("Page.getNavigationHistory", session_id=tab.session_id)
        entries = as_list(history.get("entries"), "entries")
        current = history.get("currentIndex")
        if not isinstance(current, int):
            raise RuntimeError("browser navigation history has no currentIndex")
        index = current + step
        if index < 0 or index >= len(entries):
            return
        entry = as_map(entries[index], "entries[]")
        navigated = conn.expect(
            "Page.frameNavigated", "Page.navigatedWithinDocument", session_id=tab.session_id
        )
        await conn.send(
            "Page.navigateToHistoryEntry", {"entryId": entry["id"]}, session_id=tab.session_id
        )
        await conn.wait(navigated, HISTORY_TIMEOUT_S)

    async def tab_info(self, tab: Tab) -> JsonDict:
        info = as_map(
            await self.browser.eval_js(
                tab.session_id, "({url: location.href, title: document.title})"
            ),
            "tab_info",
        )
        return {"url": str(info.get("url", "")), "title": str(info.get("title", ""))}

    async def tabs_context(self) -> JsonDict:
        await self.sync()
        current = len(self.browser.tabs) - 1 if self.browser.tabs else 0
        tabs: list[Json] = []
        for index, tab in enumerate(self.browser.tabs):
            info = await self.tab_info(tab)
            tabs.append({"id": index, "active": index == current, **info})
        return {"current_tab": current, "tabs": tabs}

    async def tab_titles(self) -> list[str]:
        titles = []
        for tab in self.browser.tabs:
            info = await self.tab_info(tab)
            titles.append(str(info.get("title") or ""))
        return titles

    async def create(self, url: str = "about:blank") -> JsonDict:
        created = await self.browser.connection().send(
            "Target.createTarget", {"url": "about:blank"}
        )
        tab = await self.attach_tab(as_str(created.get("targetId"), "targetId"))
        self.browser.tabs.append(tab)
        info = await self.navigate(url, self.browser.tabs.index(tab))
        return {"tab_id": self.browser.tabs.index(tab), **info}

    async def close(self, args: JsonDict) -> JsonDict:
        tab = await self.page(_tab_id(args.get("tab_id")))
        await self.browser.connection().send("Target.closeTarget", {"targetId": tab.target_id})
        self.browser.tabs = [open_tab for open_tab in self.browser.tabs if open_tab is not tab]
        self.browser.oop_sessions = {}
        return await self.tabs_context()


def _tab_id(value: Json | None) -> int | None:
    match value:
        case int():
            return value
        case float():
            return int(value)
        case str() if value:
            return int(value)
        case _:
            return None
