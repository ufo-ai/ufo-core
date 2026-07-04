from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from selfhost_ext_browser.bua.wire import Json, JsonDict, as_map


class BrowserRuntimeCdp(Protocol):
    async def send(
        self,
        method: str,
        params: JsonDict | None = None,
        session_id: str | None = None,
    ) -> JsonDict: ...


class BrowserRuntimeSession(Protocol):
    def connection(self) -> BrowserRuntimeCdp: ...


@dataclass(frozen=True)
class BrowserRuntime:
    """Runtime.evaluate / callFunctionOn against a target, raising on an uncaught page exception."""

    browser: BrowserRuntimeSession

    async def eval(self, session_id: str, expression: str) -> Json:
        result = await self.browser.connection().send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session_id=session_id,
        )
        self.raise_on_exception(result)
        return as_map(result.get("result"), "result").get("value")

    async def call_on(
        self,
        session_id: str,
        object_id: str,
        function: str,
        arguments: list[Json] | None = None,
    ) -> JsonDict:
        params: JsonDict = {
            "objectId": object_id,
            "functionDeclaration": function,
            "returnByValue": True,
        }
        if arguments is not None:
            params["arguments"] = [{"value": argument} for argument in arguments]
        result = await self.browser.connection().send(
            "Runtime.callFunctionOn", params, session_id=session_id
        )
        self.raise_on_exception(result)
        return as_map(as_map(result.get("result"), "result").get("value") or {}, "value")

    @staticmethod
    def raise_on_exception(result: JsonDict) -> None:
        details = result.get("exceptionDetails")
        if not isinstance(details, dict):
            return
        exception = details.get("exception")
        description = (
            exception.get("description") if isinstance(exception, dict) else None
        ) or details.get("text")
        raise RuntimeError(f"browser evaluate failed: {description}")
