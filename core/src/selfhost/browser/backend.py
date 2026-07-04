"""The pluggable browser seam, at the tool-surface level.

A `BrowserBackend` yields a per-turn `BrowserSurface` — the eleven tool methods the browser tools
drive (navigate / read_page / get_page_text / find / form_input / computer / tabs_* / upload_file /
wait_for_download). The default is `BuaBackend`: the ported BUA engine driving Chrome over the CDP
endpoint an endpoint provider yields. Two extension backends slot in at this seam without touching
the engine — browserbase reuses `BuaBackend` with a different `BrowserCdpProvider` (only the
endpoint changes); browser-use registers a `BrowserBackend` whose surface is its own (the whole
surface changes). The surface opens the connection lazily on first use, so a turn that never browses
never dials Chrome, and closes it at turn end."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from selfhost.browser.cdp_provider import BrowserCdpProvider
from selfhost.browser.find import FindCompleter
from selfhost.browser.session import BrowserSession


class BrowserSurface(Protocol):
    """The live, per-turn browser the tools call. `find` may run a host-side LLM ranking through the
    completer the backend was handed; every method returns the JSON the tool relays to the model."""

    async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    async def aclose(self) -> None: ...


class BrowserBackend(Protocol):
    def surface(self, find: FindCompleter | None, model: str | None) -> BrowserSurface: ...


@dataclass
class BuaSurface:
    """The BUA engine as a tool-surface: lazily connects a `BrowserSession` to the endpoint the
    provider yields on first use, injects the host-side find completer, and closes on turn end."""

    provider: BrowserCdpProvider
    find_completer: FindCompleter | None
    model: str | None
    session: BrowserSession | None = None

    async def _open(self) -> BrowserSession:
        if self.session is None:
            endpoint = await self.provider()
            session = BrowserSession(cdp=endpoint, model=self.model)
            await session.open()
            self.session = session
        return self.session

    async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        url = args["url"]
        if not isinstance(url, str):
            raise ValueError("navigate requires a url string")
        return await session.navigate(url, _tab_id(args.get("tab_id")))

    async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.tabs_context()

    async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        url = args.get("url")
        return await session.tabs_create(url if isinstance(url, str) and url else "about:blank")

    async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.tabs_close(args)

    async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.upload_file(args)

    async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.read_page(args)

    async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.get_page_text(args)

    async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.find(args, self.find_completer)

    async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.form_input(args)

    async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.computer(args)

    async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session = await self._open()
        return await session.wait_for_download(args)

    async def aclose(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None


@dataclass(frozen=True)
class BuaBackend:
    provider: BrowserCdpProvider

    def surface(self, find: FindCompleter | None, model: str | None) -> BrowserSurface:
        return BuaSurface(provider=self.provider, find_completer=find, model=model)


def _tab_id(value: JsonValue) -> int | None:
    match value:
        case bool():
            return None
        case int():
            return value
        case float():
            return int(value)
        case str() if value:
            return int(value)
        case _:
            return None
