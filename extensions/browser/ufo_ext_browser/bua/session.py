"""The BUA browser session: one CDP connection to Chrome and the tool-surface it drives.

A session opens one WebSocket to the CDP endpoint the provider yielded, wires the event listeners
(downloads, dialogs, settle accounting, tab discovery), and exposes the browser tool-surface —
navigate / read_page / get_page_text / find / form_input / computer / tabs_* / upload_file /
wait_for_download — each delegating to a per-call reader dataclass beneath it. The session holds the
mutable per-turn state (tabs, downloads, out-of-process frame sessions, keyboard) the readers act
on; it is disposable, recreated per turn against the persistent browser."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Coroutine
from typing import Any, Self

from ufo.sdk.browser import CdpEndpoint, FindCompleter
from ufo_ext_browser.bua.cdp import CdpConnection, CdpError, resolve_ws_url
from ufo_ext_browser.bua.computer import BrowserComputer
from ufo_ext_browser.bua.content import BrowserContent
from ufo_ext_browser.bua.coordinate import (
    Size,
    compute_screenshot_dimensions,
    model_coordinate_space,
)
from ufo_ext_browser.bua.dialogs import BrowserDialogs
from ufo_ext_browser.bua.downloads import BrowserDownload, BrowserDownloads
from ufo_ext_browser.bua.forms import BrowserForms
from ufo_ext_browser.bua.page import BrowserPage, FrameNode
from ufo_ext_browser.bua.runtime import BrowserRuntime
from ufo_ext_browser.bua.settle import Settle
from ufo_ext_browser.bua.tabs import BrowserTabEvents, BrowserTabs, Tab
from ufo_ext_browser.bua.wire import Json, JsonDict, as_str

VIEWPORT = Size(1440, 900)
MODEL_SIZE = compute_screenshot_dimensions(VIEWPORT)
MAX_WAIT_SECONDS = 30.0
MAX_FRAME_DEPTH = 3


class BrowserUnavailable(RuntimeError):
    pass


class BrowserSession:
    def __init__(
        self,
        cdp: CdpEndpoint | None = None,
        model: str | None = None,
    ) -> None:
        self.cdp = cdp
        self.model_size = model_coordinate_space(model) or MODEL_SIZE
        self.conn: CdpConnection | None = None
        self.tabs: list[Tab] = []
        self.oop_sessions: dict[str, str] = {}
        self.downloads: list[BrowserDownload] = []
        self.download_dir: str | None = None
        self.is_mac = False
        self.last_batch_scroll_only = False
        self.settle = Settle()
        self.dialogs: list[str] = []
        self.tab_events = BrowserTabEvents()
        self._bg_tasks: set[asyncio.Task[None]] = set()

    async def open(self) -> None:
        if self.conn is not None:
            return
        try:
            await self._bootstrap()
        except BaseException:
            await self.close()
            raise

    async def _bootstrap(self) -> None:
        cdp = self.cdp
        if cdp is None:
            raise BrowserUnavailable("browser CDP endpoint is required")
        headers = cdp.headers
        ws_url = await resolve_ws_url(cdp.url, headers)
        self.conn = await CdpConnection.open(ws_url, headers)
        conn = self.conn
        version = await conn.send("Browser.getVersion")
        self.is_mac = "Mac" in str(version.get("userAgent", ""))
        self.download_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="ufo-downloads-")
        tabs = self.tab_reader()
        downloads = self.download_reader()
        dialogs = self.dialog_reader()
        conn.on("Browser.downloadWillBegin", downloads.on_download_begin)
        conn.on("Browser.downloadProgress", downloads.on_download_progress)
        await conn.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": self.download_dir, "eventsEnabled": True},
        )
        tabs.remember_initial_targets(await conn.send("Target.getTargets"))
        conn.on("Target.targetCreated", tabs.on_target_created)
        conn.on("Target.targetDestroyed", tabs.on_target_destroyed)
        conn.on("Network.requestWillBeSent", self.settle.on_request_started)
        conn.on("Network.loadingFinished", self.settle.on_request_finished)
        conn.on("Network.loadingFailed", self.settle.on_request_finished)
        conn.on("Page.frameStartedLoading", tabs.on_frame_loading)
        conn.on("Page.domContentEventFired", tabs.on_dom_content)
        conn.on("Page.lifecycleEvent", tabs.on_lifecycle)
        conn.on("Page.javascriptDialogOpening", dialogs.on_dialog)
        conn.on("Fetch.requestPaused", downloads.on_fetch_paused)
        await conn.send("Target.setDiscoverTargets", {"discover": True})
        created = await conn.send("Target.createTarget", {"url": "about:blank"})
        self.tabs = [await tabs.attach_tab(as_str(created.get("targetId"), "targetId"))]

    async def close(self) -> None:
        try:
            if self.conn is not None:
                for tab in self.tabs:
                    try:
                        await self.conn.send("Target.closeTarget", {"targetId": tab.target_id})
                    except (CdpError, TimeoutError, RuntimeError):
                        pass
                await self.conn.close()
        finally:
            if self.download_dir is not None:
                await asyncio.to_thread(shutil.rmtree, self.download_dir, ignore_errors=True)
            self.conn = None
            self.tabs = []
            self.oop_sessions = {}
            self.downloads = []
            self.download_dir = None
            self.last_batch_scroll_only = False
            self.settle = Settle()
            self.dialogs = []
            for task in self._bg_tasks:
                task.cancel()
            self._bg_tasks = set()
            self.tab_events = BrowserTabEvents()

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def connection(self) -> CdpConnection:
        if self.conn is None:
            raise BrowserUnavailable("browser is not open")
        return self.conn

    def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool:
        return self.tab_reader().is_top_level_frame(session_id, frame_id)

    async def init_session(self, session_id: str) -> None:
        await self.tab_reader().init_session(session_id)

    async def page(self, tab_id: int | None = None) -> Tab:
        return await self.tab_reader().page(tab_id)

    async def navigate(self, url: str, tab_id: int | None = None) -> JsonDict:
        return await self.tab_reader().navigate(url, tab_id)

    async def tab_info(self, tab: Tab) -> JsonDict:
        return await self.tab_reader().tab_info(tab)

    async def tabs_context(self) -> JsonDict:
        return await self.tab_reader().tabs_context()

    async def tab_titles(self) -> list[str]:
        return await self.tab_reader().tab_titles()

    def tab_reader(self) -> BrowserTabs:
        return BrowserTabs(self, VIEWPORT)

    def page_reader(self) -> BrowserPage:
        return BrowserPage(self, VIEWPORT, MAX_FRAME_DEPTH)

    def content_reader(self) -> BrowserContent:
        return BrowserContent(self)

    def download_reader(self) -> BrowserDownloads:
        return BrowserDownloads(self, MAX_WAIT_SECONDS)

    def dialog_reader(self) -> BrowserDialogs:
        return BrowserDialogs(self)

    def form_reader(self) -> BrowserForms:
        return BrowserForms(self)

    def runtime_reader(self) -> BrowserRuntime:
        return BrowserRuntime(self)

    async def tabs_create(self, url: str = "about:blank") -> JsonDict:
        return await self.tab_reader().create(url)

    async def tabs_close(self, args: JsonDict) -> JsonDict:
        return await self.tab_reader().close(args)

    async def upload_file(self, args: JsonDict) -> JsonDict:
        return await self.form_reader().upload_file(args)

    async def tree(self, args: JsonDict, filter_type: str = "all") -> str:
        return await self.content_reader().tree(args, filter_type)

    async def read_page(self, args: JsonDict) -> JsonDict:
        return await self.content_reader().read_page(args)

    async def get_page_text(self, args: JsonDict) -> JsonDict:
        return await self.content_reader().get_page_text(args)

    async def find(self, args: JsonDict, complete: FindCompleter | None = None) -> JsonDict:
        return await self.content_reader().find(args, complete)

    async def form_input(self, args: JsonDict) -> JsonDict:
        return await self.form_reader().input(args)

    async def computer(self, args: JsonDict) -> JsonDict:
        return await BrowserComputer(self, VIEWPORT, MAX_WAIT_SECONDS).run(args)

    async def wait_for_download(self, args: JsonDict) -> JsonDict:
        if self.download_dir is None:
            raise BrowserUnavailable("browser is not open")
        return await self.download_reader().wait(args, self.download_dir)

    async def eval_js(self, session_id: str, expression: str) -> Json:
        return await self.runtime_reader().eval(session_id, expression)

    async def call_on(
        self,
        session_id: str,
        object_id: str,
        function: str,
        arguments: list[Json] | None = None,
    ) -> JsonDict:
        return await self.runtime_reader().call_on(session_id, object_id, function, arguments)

    def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]:
        return self.page_reader().resolve_ref(tab, ref)

    async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]:
        return await self.page_reader().ref_point(tab, ref)
