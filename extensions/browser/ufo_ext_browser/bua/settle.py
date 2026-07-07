from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from ufo_ext_browser.bua.cdp import CdpError
from ufo_ext_browser.bua.page import Cdp
from ufo_ext_browser.bua.wire import JsonDict

SETTLE_BEAT_S = 0.05
SETTLE_CHAIN_GAP_S = 0.15
SETTLE_POLL_S = 0.05
SETTLE_ACTION_CAP_S = 2.5
SETTLE_NAV_CAP_S = 8.0
SETTLE_POST_PAINT_GRACE_S = 1.0

PAINT_LIFECYCLE_EVENTS = frozenset({"firstContentfulPaint", "firstMeaningfulPaint"})

FLUSH_PAGE_TASKS_JS = "new Promise(resolve => setTimeout(resolve, 0))"

PASSIVE_RESOURCE_TYPES = frozenset(
    {"Stylesheet", "Image", "Media", "Font", "Ping", "CSPViolationReport", "Preflight"}
)
PASSIVE_PRIORITIES = frozenset({"VeryLow", "Low"})
ANALYTICS_HOST_FRAGMENTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "datadoghq.com",
    "sentry.io",
    "segment.io",
    "segment.com",
    "hotjar.com",
    "fullstory.com",
    "mixpanel.com",
    "amplitude.com",
    "analytics.",
)


def tracks_request(params: JsonDict) -> bool:
    """A request worth waiting on: foreground traffic a page issues to fulfil the action, not a
    subresource, idle prefetch, or analytics beacon. CDP event fields are optional, so an
    unrecognized shape stays tracked rather than silently skipped."""
    if params.get("type") in PASSIVE_RESOURCE_TYPES:
        return False
    request = params.get("request")
    request = request if isinstance(request, dict) else {}
    if request.get("initialPriority") in PASSIVE_PRIORITIES:
        return False
    url = request.get("url")
    host = urlparse(url).hostname or "" if isinstance(url, str) else ""
    return not any(fragment in host for fragment in ANALYTICS_HOST_FRAGMENTS)


class Settle:
    """Consequence-scoped settling: wait only for work the current action started — the requests it
    triggered (with chain gaps for follow-ups) and the DOM of any navigation it caused — never for
    pre-existing background traffic. A document that paints returns once its foreground requests
    drain within a short post-paint grace (never-idle pages paint fast but never quiet, so they
    return at the grace); quiet actions return after one renderer task flush; in-page changes that
    fire no paint fall back to network-quiet, cut off at the cap.

    Paint is the readiness signal: Chrome paints a page in well under a second even when the network
    stays busy forever (feeds, retail, ad-heavy news), so network-quiet is only the fallback for
    in-page (SPA) changes, which fire no paint event."""

    def __init__(self) -> None:
        self.pending: set[tuple[str, str]] = set()
        self.started = 0
        self.loading: set[str] = set()
        self.painted: set[str] = set()

    def reset(self) -> None:
        self.pending.clear()
        self.started = 0
        self.painted.clear()

    def on_request_started(self, params: JsonDict, session_id: str | None) -> None:
        request_id = params.get("requestId")
        if session_id is not None and isinstance(request_id, str) and tracks_request(params):
            self.pending.add((session_id, request_id))
            self.started += 1

    def on_request_finished(self, params: JsonDict, session_id: str | None) -> None:
        request_id = params.get("requestId")
        if session_id is not None and isinstance(request_id, str):
            self.pending.discard((session_id, request_id))

    def mark_loading(self, session_id: str) -> None:
        self.loading.add(session_id)

    def mark_loaded(self, session_id: str) -> None:
        self.loading.discard(session_id)

    def mark_painted(self, session_id: str) -> None:
        self.painted.add(session_id)

    async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None:
        await self._flush_page_tasks(cdp, session_id)
        deadline = time.monotonic() + cap
        if session_id in self.painted:
            return await self._drain_after_paint(session_id, deadline)
        if self.started == 0 and session_id not in self.loading:
            return
        while time.monotonic() < deadline:
            if session_id in self.painted:
                return await self._drain_after_paint(session_id, deadline)
            if session_id in self.loading or self.pending:
                await asyncio.sleep(SETTLE_POLL_S)
                continue
            await asyncio.sleep(SETTLE_CHAIN_GAP_S)
            if session_id not in self.loading and not self.pending:
                return

    async def _drain_after_paint(self, session_id: str, deadline: float) -> None:
        """First paint fired — the page is visible but a shell may still be fetching its content.
        Give foreground requests a brief, capped grace to finish: a shell whose content lands in
        time is no longer called ready early, while a never-idle page that paints and never quiets
        returns at the grace, never the full cap."""
        grace = min(time.monotonic() + SETTLE_POST_PAINT_GRACE_S, deadline)
        while time.monotonic() < grace:
            if session_id in self.loading or self.pending:
                await asyncio.sleep(SETTLE_POLL_S)
                continue
            await asyncio.sleep(SETTLE_CHAIN_GAP_S)
            if session_id not in self.loading and not self.pending:
                return

    async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None:
        """One renderer task-queue round trip: by the time it returns, input handlers (with their
        microtasks and setTimeout(0) continuations) have run, and — since CDP delivers events in
        order — any requestWillBeSent they emitted has already been counted."""
        try:
            await cdp.send(
                "Runtime.evaluate",
                {"expression": FLUSH_PAGE_TASKS_JS, "awaitPromise": True, "returnByValue": True},
                session_id=session_id,
            )
        except (CdpError, TimeoutError):
            await asyncio.sleep(SETTLE_BEAT_S)
