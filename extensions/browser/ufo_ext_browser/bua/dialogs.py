from __future__ import annotations

import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from ufo_ext_browser.bua.cdp import CdpError
from ufo_ext_browser.bua.wire import JsonDict

logger = logging.getLogger(__name__)

DIALOG_AUTO_ACCEPT = frozenset({"alert", "beforeunload"})


class BrowserDialogCdp(Protocol):
    async def send(
        self,
        method: str,
        params: JsonDict | None = None,
        session_id: str | None = None,
    ) -> JsonDict: ...


class BrowserDialogSession(Protocol):
    dialogs: list[str]

    def connection(self) -> BrowserDialogCdp: ...

    def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None: ...


@dataclass(frozen=True)
class BrowserDialogs:
    browser: BrowserDialogSession

    def on_dialog(self, params: JsonDict, session_id: str | None) -> None:
        """A JS dialog freezes the target until handled, blocking every later CDP event — so it must
        be answered immediately. alert/beforeunload have no real alternative and are accepted;
        confirm/prompt are dismissed so the agent never silently satisfies a site's own "are you
        sure?" guard. Either way the model is told it happened."""
        dialog_type = str(params.get("type") or "alert")
        accept = dialog_type in DIALOG_AUTO_ACCEPT
        verb = "accepted" if accept else "dismissed"
        message = str(params.get("message") or "")
        self.browser.dialogs.append(
            f"{dialog_type} {verb}: {message}" if message else f"{dialog_type} {verb}"
        )
        self.browser.spawn_background(self._answer_dialog(session_id, accept))

    async def _answer_dialog(self, session_id: str | None, accept: bool) -> None:
        try:
            await self.browser.connection().send(
                "Page.handleJavaScriptDialog", {"accept": accept}, session_id=session_id
            )
        except (CdpError, TimeoutError, RuntimeError):
            logger.warning("browser.dialog_handle_failed", extra={"session_id": session_id})
