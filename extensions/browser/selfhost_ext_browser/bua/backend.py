"""The BUA engine as a per-turn tool-surface, built from the turn's cdp provider.

`BuaSurface` is the eleven tool methods the browser tools drive (navigate / read_page /
get_page_text / find / form_input / computer / tabs_* / upload_file / wait_for_download). It mints a
`CdpLease` from the turn's provider on first use, connects a `BrowserSession` to the endpoint the
lease yields, injects the host-side find completer, and on `aclose` closes the session and releases
the lease — so a turn that never browses never dials Chrome, and a turn that does releases both the
CDP connection and (for a remote provider) the hosted session at turn end. The browser tools build
one `BuaSurface` per turn from `ctx.cdp_provider` and register its `aclose` on the turn cleanup."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from selfhost.sdk.browser import CdpLease, CdpProvider, FindCompleter
from selfhost_ext_browser.bua.session import BrowserSession


@dataclass
class BuaSurface:
    cdp_provider: CdpProvider
    find_completer: FindCompleter | None
    model: str | None
    lease: CdpLease | None = None
    session: BrowserSession | None = None

    async def _open(self) -> BrowserSession:
        if self.session is not None:
            return self.session
        if self.lease is None:
            self.lease = await self.cdp_provider.lease()
        endpoint = await self.lease.endpoint()
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
        """Release the CDP session and the transport lease. The lease releases even if the session
        close raises (a broken CDP socket on an errored turn) — so a paid hosted-browser lease is
        never orphaned by a failed session teardown."""
        try:
            if self.session is not None:
                await self.session.close()
                self.session = None
        finally:
            if self.lease is not None:
                await self.lease.aclose()
                self.lease = None


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
