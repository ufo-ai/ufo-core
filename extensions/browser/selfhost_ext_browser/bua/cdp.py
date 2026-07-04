"""The CDP transport: one WebSocket to Chrome, command/response futures, and event fan-out.

`CdpConnection.open` dials the DevTools WebSocket and runs a reader task that resolves each command
future by id, resolves the one-shot `expect` waiters an event matches, and fans events to `on`
listeners. `send` awaits a bounded response; `expect`/`wait` await an event with its own timeout. A
closed connection fails every pending future loud rather than hanging a turn."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Self

import httpx
import websockets
from websockets.asyncio.client import ClientConnection, connect

from selfhost_ext_browser.bua.wire import JsonDict, as_map, as_str

COMMAND_TIMEOUT_S = 30.0
EVENT_TIMEOUT_S = 30.0
MAX_MESSAGE_BYTES = 256 * 1024 * 1024

EventListener = Callable[[JsonDict, str | None], None]


class CdpError(RuntimeError):
    def __init__(self, method: str, code: int, message: str) -> None:
        self.method = method
        self.code = code
        super().__init__(f"CDP {method} failed: {message} (code {code})")


async def resolve_ws_url(url: str, headers: dict[str, str]) -> str:
    if url.startswith(("ws://", "wss://")):
        return url
    async with httpx.AsyncClient(headers=headers) as client:
        response = await client.get(f"{url.rstrip('/')}/json/version")
        response.raise_for_status()
        return as_str(response.json().get("webSocketDebuggerUrl"), "webSocketDebuggerUrl")


class CdpConnection:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, tuple[str, asyncio.Future[JsonDict]]] = {}
        self._listeners: dict[str, list[EventListener]] = {}
        self._waiters: list[tuple[tuple[str, ...], str | None, asyncio.Future[JsonDict]]] = []
        self._reader: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, ws_url: str, headers: dict[str, str] | None = None) -> Self:
        ws = await connect(ws_url, additional_headers=headers, max_size=MAX_MESSAGE_BYTES)
        conn = cls(ws)
        conn._reader = asyncio.create_task(conn._read_loop())
        return conn

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        await self._ws.close()

    async def send(
        self, method: str, params: JsonDict | None = None, session_id: str | None = None
    ) -> JsonDict:
        self._next_id += 1
        message_id = self._next_id
        message: JsonDict = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        future: asyncio.Future[JsonDict] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = (method, future)
        await self._ws.send(json.dumps(message))
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_S):
                return await future
        except TimeoutError:
            self._pending.pop(message_id, None)
            raise TimeoutError(f"CDP {method} timed out after {COMMAND_TIMEOUT_S}s") from None

    def on(self, event: str, listener: EventListener) -> None:
        self._listeners.setdefault(event, []).append(listener)

    def expect(self, *events: str, session_id: str | None = None) -> asyncio.Future[JsonDict]:
        future: asyncio.Future[JsonDict] = asyncio.get_running_loop().create_future()
        self._waiters.append((events, session_id, future))
        return future

    async def wait(
        self,
        future: asyncio.Future[JsonDict],
        timeout: float = EVENT_TIMEOUT_S,  # noqa: ASYNC109
    ) -> JsonDict:
        try:
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._waiters = [waiter for waiter in self._waiters if waiter[2] is not future]

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                self._dispatch(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        finally:
            for method, future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(f"CDP connection closed during {method}"))
            self._pending.clear()
            for _, _, future in self._waiters:
                if not future.done():
                    future.set_exception(RuntimeError("CDP connection closed"))
            self._waiters.clear()

    def _dispatch(self, message: JsonDict) -> None:
        message_id = message.get("id")
        if isinstance(message_id, int):
            pending = self._pending.pop(message_id, None)
            if pending is None:
                return
            method, future = pending
            if future.done():
                return
            if "error" in message:
                error = as_map(message.get("error"), "error")
                code = error.get("code")
                future.set_exception(
                    CdpError(
                        method,
                        code if isinstance(code, int) else 0,
                        str(error.get("message", "")),
                    )
                )
            else:
                future.set_result(as_map(message.get("result") or {}, "result"))
            return
        method_name = message.get("method")
        if not isinstance(method_name, str):
            return
        params = as_map(message.get("params") or {}, "params")
        session = message.get("sessionId")
        session_id = session if isinstance(session, str) else None
        remaining = []
        for waiter in self._waiters:
            events, waiter_session, future = waiter
            if future.done():
                continue
            if method_name in events and waiter_session in (None, session_id):
                future.set_result(params)
                continue
            remaining.append(waiter)
        self._waiters = remaining
        for listener in self._listeners.get(method_name, []):
            listener(params, session_id)
