"""The BUA engine as a per-turn tool-surface, built from the turn's cdp provider.

`BuaSurface` is the eleven tool methods the browser tools drive (navigate / read_page /
get_page_text / find / form_input / computer / tabs_* / upload_file / wait_for_download). It leases
a `CdpLease` from the turn's provider on first use, connects a `BrowserSession` to the endpoint the
lease yields, injects the host-side find completer, and on `aclose` closes the session and releases
the lease — so a turn that never browses never dials Chrome, and a turn that does releases both the
CDP connection and (for a remote provider) the hosted session at turn end. The browser tools build
one `BuaSurface` per turn from `ctx.cdp_provider` and register its `aclose` on the turn cleanup.

Recovery reconnect: on first use the surface persists the lease's durable `token` under its
conversation's key in the extension's scoped store. A hard crash skips `aclose`, so the token
survives; when the recovered turn re-dispatches the in-flight browser tool call, the fresh surface
finds that token and `reattach`es to the live session instead of minting a new one — no re-open, no
re-navigate. A token whose session is gone (reaped, dead endpoint) raises `SessionGone`, is cleared,
and the surface mints fresh. `aclose` (which runs on every non-crash turn end) clears the token, so
a released session is never reattached by a later turn."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from ufo.sdk.browser import CdpLease, CdpProvider, FindCompleter, SessionGone
from ufo.sdk.context import ScopedStore
from ufo.sdk.sandbox import SandboxSession
from ufo_ext_browser.bua.session import BrowserSession

CDP_TOKEN_KEY = "cdp-token/{conversation_id}"


@dataclass
class BuaSurface:
    cdp_provider: CdpProvider
    find_completer: FindCompleter | None
    model: str | None
    sandbox: SandboxSession | None = None
    store: ScopedStore | None = None
    conversation_id: UUID | None = None
    lease: CdpLease | None = None
    session: BrowserSession | None = None

    async def _open(self) -> BrowserSession:
        if self.session is not None:
            return self.session
        if self.lease is None:
            self.lease = await self._acquire_lease()
        endpoint = await self.lease.endpoint()
        session = BrowserSession(cdp=endpoint, model=self.model)
        await session.open()
        self.session = session
        return self.session

    async def _acquire_lease(self) -> CdpLease:
        """Reconnect to the turn's existing CDP session when a durable token survives from a prior
        run of this turn (the crash a recovery replay is resuming), else mint a fresh lease and
        persist its token. A token whose session is gone is cleared and re-minted, so the task
        re-grounds rather than resuming against a page that no longer exists."""
        token = await self._stored_token()
        if token is not None:
            try:
                return await self.cdp_provider.reattach(token)
            except SessionGone:
                await self._store_token(None)
        lease = await self.cdp_provider.lease(self.sandbox)
        await self._store_token(await lease.token())
        return lease

    async def _stored_token(self) -> str | None:
        if self.store is None or self.conversation_id is None:
            return None
        value = await self.store.get(CDP_TOKEN_KEY.format(conversation_id=self.conversation_id))
        return value if isinstance(value, str) else None

    async def _store_token(self, token: str | None) -> None:
        if self.store is None or self.conversation_id is None:
            return
        await self.store.put(CDP_TOKEN_KEY.format(conversation_id=self.conversation_id), token)

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
        """Release the CDP session and the transport lease, and clear the durable reattach token so
        a later turn never reconnects to a released session. The lease releases even if the session
        close raises (a broken CDP socket on an errored turn) — so a paid hosted-browser lease is
        never orphaned by a failed session teardown. This runs at every non-crash turn end; a hard
        crash skips it, leaving the token for the recovery replay to reattach — as intended."""
        try:
            if self.session is not None:
                await self.session.close()
                self.session = None
        finally:
            if self.lease is not None:
                await self.lease.aclose()
                self.lease = None
            await self._store_token(None)


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
