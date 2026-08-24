"""The BUA engine as a per-turn tool-surface, built from the turn's cdp provider.

`BuaSurface` is the eleven tool methods the browser tools drive (navigate / read_page /
get_page_text / find / form_input / computer / tabs_* / upload_file / wait_for_download). It leases
a `CdpLease` from the turn's provider on first use, connects a `BrowserSession` to the endpoint the
lease yields, injects the host-side find completer, and on `aclose` closes the session and releases
the lease — so a turn that never browses never dials Chrome, and a turn that does releases both the
CDP connection and (for a remote provider) the hosted session at turn end. The browser tools build
one `BuaSurface` per turn from `ctx.cdp_provider` and register its `aclose` on the turn cleanup.

File inputs go through the lease too: a workspace path only reaches the page once the transport
has placed it where its Chrome can open it, which is the same path for a Chrome inside the turn's
sandbox and an upload for a remote one. The surface holds both the lease and the sandbox, so it is
where the bytes are read; `BrowserSession` keeps driving raw CDP against whatever path comes back.

Recovery reconnect: on first use the surface persists the lease's durable `token` under its
conversation's key in the extension's scoped store. A hard crash skips `aclose`, so the token
survives; when the recovered turn re-dispatches the in-flight browser tool call, the fresh surface
finds that token and `reattach`es to the live session instead of minting a new one — no re-open, no
re-navigate. A token whose session is gone (reaped, dead endpoint) raises `SessionGone`, is cleared,
and the surface mints fresh. `aclose` (which runs on every non-crash turn end) clears the token, so
a released session is never reattached by a later turn."""

from __future__ import annotations

import asyncio
import base64
import shlex
from dataclasses import dataclass, field
from functools import partial
from uuid import UUID

from pydantic import JsonValue

from ufo.sdk.browser import CdpLease, CdpProvider, FindCompleter, SessionGone
from ufo.sdk.context import ScopedStore
from ufo.sdk.sandbox import Sandbox, contained_leaf, workspace_path
from ufo_ext_browser.bua.session import BrowserSession

CDP_TOKEN_KEY = "cdp-token/{conversation_id}"
MAX_READ_BYTES = 20 * 1024 * 1024
SIZE_BUDGET_SECONDS = 30
ENCODE_BUDGET_SECONDS = 600
"""Each read an upload makes carries the budget its own work needs. A stat answers at once. Encoding
a file up to `MAX_READ_BYTES` hands about 27 MiB back on one command's stdout, which the sandbox's
120s default cuts off part way through a large file — and a cut command reports the carrier's own
deadline, never the read."""
DEFAULT_DOWNLOAD_NAME = "download"
UPLOAD_SETTLE_ATTEMPTS = 20
UPLOAD_SETTLE_SLEEP_SECONDS = 0.5


@dataclass
class BuaSurface:
    cdp_provider: CdpProvider
    find_completer: FindCompleter | None
    model: str | None
    sandbox: Sandbox | None = None
    store: ScopedStore | None = None
    conversation_id: UUID | None = None
    lease: CdpLease | None = None
    session: BrowserSession | None = None
    _shipped: list[int] = field(default_factory=list)

    async def _open(self) -> BrowserSession:
        if self.session is not None:
            return self.session
        if self.lease is None:
            self.lease = await self._acquire_lease()
        endpoint = await self.lease.endpoint()
        session = BrowserSession(
            cdp=endpoint,
            model=self.model,
            download_dir=await self.lease.download_dir(),
        )
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
        """Set a file input from workspace paths, each resolved under the workspace and then placed
        where the leased Chrome can open it — the same path when Chrome runs in this turn's sandbox,
        an uploaded location when the browser is remote. Only the transport knows which, so every
        path goes through the lease. `workspace_path` is what makes "workspace paths" true rather
        than merely stated: the reader below runs an unconfined shell, and a remote transport would
        carry whatever it read off the box."""
        session = await self._open()
        files = args.get("files")
        if not isinstance(files, list):
            raise ValueError("upload_file requires a files list")
        placed: list[JsonValue] = []
        self._shipped = []
        for path in files:
            if not isinstance(path, str) or not path:
                raise ValueError("upload_file requires workspace path strings")
            target = workspace_path(path)
            placed.append(await self._lease().place_file(target, partial(self._read, target)))
        attached = {**args, "files": placed}
        reply = await session.upload_file(attached)
        await self._settle_upload(session, attached)
        return reply

    async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None:
        """Wait until the input holds the bytes that were shipped to it. A remote transport writes
        the file through after its upload call returns, so an attach made too early leaves the page
        holding the right name and an empty file — the page would submit nothing and no error would
        say so. Re-attaching picks the file up once it lands. Only sizes this surface actually
        shipped are checked, so a browser reading the workspace directly waits on nothing."""
        if not self._shipped:
            return
        expected = list(self._shipped)
        attempts = UPLOAD_SETTLE_ATTEMPTS
        for attempt in range(attempts):
            if await session.attached_sizes(args) == expected:
                return
            if attempt + 1 == attempts:
                break
            await asyncio.sleep(UPLOAD_SETTLE_SLEEP_SECONDS)
            await session.upload_file(args)
        raise RuntimeError(
            "the page never received the uploaded file: the browser reported "
            f"{await session.attached_sizes(args)} bytes where {expected} were sent"
        )

    def _lease(self) -> CdpLease:
        if self.lease is None:
            raise RuntimeError("the browser has no cdp lease")
        return self.lease

    async def _read(self, path: str) -> bytes:
        """The workspace file's bytes, read out of the turn's sandbox for a transport that must ship
        them to a remote browser. Sized first: the whole file would land in this process, so an
        oversized upload fails here rather than filling serve's memory on the way out. The encoding
        reads stdin rather than passing a width flag, which is GNU-only, and stands alone in the
        command so the exit code is its own — a pipeline reports its last stage, which would mask an
        unreadable file as an empty upload. Decoding drops the line wrapping, and runs in a thread:
        a whole file's worth of it is CPU work every other turn on this loop would wait through.
        Each command states its own budget, because the encode of a whole file is minutes of work
        where the sizing is none."""
        if self.sandbox is None:
            raise RuntimeError(
                "uploading to a remote browser needs the turn's sandbox to read from"
            )
        quoted = shlex.quote(path)
        size = await self.sandbox.bash(f"stat -c %s -- {quoted}", timeout_s=SIZE_BUDGET_SECONDS)
        if size.exit_code != 0:
            raise ValueError(size.stderr.strip() or f"cannot read {path}")
        if int(size.stdout.strip()) > MAX_READ_BYTES:
            raise ValueError(f"{path} is larger than the {MAX_READ_BYTES}-byte upload limit")
        encoded = await self.sandbox.bash(f"base64 < {quoted}", timeout_s=ENCODE_BUDGET_SECONDS)
        if encoded.timed_out_after_s is not None:
            raise ValueError(
                f"reading {path} for the upload stopped at its {encoded.timed_out_after_s}s budget"
            )
        if encoded.exit_code != 0:
            raise ValueError(encoded.stderr.strip() or f"cannot read {path}")
        data = await asyncio.to_thread(base64.b64decode, encoded.stdout)
        self._shipped.append(len(data))
        return data

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
        """Wait for a download to finish, then have the transport hand its bytes back — the browser
        wrote them wherever the transport said, which is a path in the turn's sandbox or a hosted
        provider's storage, and only the transport can reach either.

        The name is cut to one path segment. The visited page chooses it, and a caller joins it into
        a workspace path, so `../` in it would land the write in a directory that caller never asked
        for. Encoding runs in a thread for the same reason the read does."""
        session = await self._open()
        download = await session.wait_for_download(args)
        data = await self._lease().fetch_download(download.guid)
        encoded = await asyncio.to_thread(base64.b64encode, data)
        return {
            "filename": contained_leaf(download.filename, DEFAULT_DOWNLOAD_NAME),
            "content_base64": encoded.decode(),
            "size": len(data),
        }

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
