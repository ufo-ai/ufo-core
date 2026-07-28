from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from ufo_ext_browser.bua.cdp import CdpError
from ufo_ext_browser.bua.wire import Json, JsonDict, ValidationError

logger = logging.getLogger(__name__)

DOWNLOAD_POLL_S = 0.1
FORCE_DOWNLOAD_TYPES = ("application/pdf",)
DOWNLOAD_NAV_GRACE_S = 1.0
FETCH_DOCUMENT_PATTERN: JsonDict = {
    "urlPattern": "*",
    "requestStage": "Response",
    "resourceType": "Document",
}


@dataclass
class Download:
    guid: str
    filename: str
    state: str
    drained: bool = False


class BrowserDownload(Protocol):
    guid: str
    filename: str
    state: str
    drained: bool


class BrowserDownloadCdp(Protocol):
    async def send(
        self,
        method: str,
        params: JsonDict | None = None,
        *,
        session_id: str | None = None,
    ) -> JsonDict: ...


class BrowserDownloadSession(Protocol):
    downloads: list[BrowserDownload]

    def connection(self) -> BrowserDownloadCdp: ...

    def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None: ...

    def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool: ...


@dataclass(frozen=True)
class BrowserDownloads:
    browser: BrowserDownloadSession
    max_wait_seconds: float

    def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None:
        """Every paused request must be released or the page hangs. Only a top-level navigation to a
        viewer-only type (PDF, which Chrome renders inline in a viewer the accessibility tree can't
        read) is rewritten to a download so the agent gets the bytes; request-stage pauses and
        embedded/subframe documents pass through untouched so a page that embeds a PDF still
        renders."""
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return
        response_code = params.get("responseStatusCode")
        if not isinstance(response_code, int):
            self.browser.spawn_background(self._continue_request(session_id, request_id))
            return
        headers = params.get("responseHeaders")
        headers = headers if isinstance(headers, list) else []
        force = (
            self.browser.is_top_level_frame(session_id, params.get("frameId"))
            and _content_type(headers) in FORCE_DOWNLOAD_TYPES
        )
        self.browser.spawn_background(
            self._continue_response(session_id, request_id, response_code, headers, force)
        )

    async def _continue_request(self, session_id: str | None, request_id: str) -> None:
        try:
            await self.browser.connection().send(
                "Fetch.continueRequest", {"requestId": request_id}, session_id=session_id
            )
        except (CdpError, TimeoutError, RuntimeError):
            logger.warning("browser.fetch_release_failed", extra={"request_id": request_id})

    async def _continue_response(
        self,
        session_id: str | None,
        request_id: str,
        response_code: int,
        headers: list[Json],
        force: bool,
    ) -> None:
        params: JsonDict = {"requestId": request_id}
        if force:
            kept = [
                header
                for header in headers
                if not (
                    isinstance(header, dict)
                    and str(header.get("name", "")).lower() == "content-disposition"
                )
            ]
            params["responseCode"] = response_code
            params["responseHeaders"] = [
                *kept,
                {"name": "Content-Disposition", "value": "attachment"},
            ]
        try:
            await self.browser.connection().send(
                "Fetch.continueResponse", params, session_id=session_id
            )
        except (CdpError, TimeoutError, RuntimeError):
            logger.warning("browser.fetch_release_failed", extra={"request_id": request_id})

    def on_download_begin(self, params: JsonDict, session_id: str | None) -> None:
        self.browser.downloads.append(
            Download(
                guid=str(params.get("guid")),
                filename=str(params.get("suggestedFilename") or "download"),
                state="inProgress",
            )
        )

    def on_download_progress(self, params: JsonDict, session_id: str | None) -> None:
        guid = str(params.get("guid"))
        state = str(params.get("state"))
        for download in self.browser.downloads:
            if download.guid == guid:
                download.state = state

    async def became_download(self, before_count: int) -> bool:
        deadline = time.monotonic() + DOWNLOAD_NAV_GRACE_S
        while time.monotonic() < deadline:
            if len(self.browser.downloads) > before_count:
                return True
            await asyncio.sleep(DOWNLOAD_POLL_S)
        return False

    async def wait(self, args: JsonDict) -> BrowserDownload:
        """Wait for a download to finish and report which one, by the guid Chrome stored it under
        and the name the site suggested. Fetching the bytes belongs to the transport that owns
        wherever the browser wrote them, so nothing is read here."""
        timeout = float_or_default(args.get("timeout"), self.max_wait_seconds)
        deadline = time.monotonic() + timeout
        while True:
            completed = [
                download for download in self.browser.downloads if download.state == "completed"
            ]
            if completed or time.monotonic() >= deadline:
                break
            await asyncio.sleep(DOWNLOAD_POLL_S)
        if not completed:
            raise TimeoutError("no browser download completed")
        return completed[-1]


def float_or_default(value: Json | None, default: float) -> float:
    match value:
        case int() | float():
            return float(value)
        case str() if value:
            return float(value)
        case None:
            return default
        case _:
            raise ValidationError("value must be numeric")


def _content_type(headers: list[Json]) -> str:
    for header in headers:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "content-type":
            return str(header.get("value", "")).split(";", 1)[0].strip().lower()
    return ""
