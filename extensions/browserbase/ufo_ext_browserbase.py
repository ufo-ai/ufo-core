"""The `browserbase` cdp provider: the turn's Chrome is a Browserbase-hosted session, selected with
`[browser] cdp_provider = "browserbase"`.

Every browse runs inside a `browser` subagent turn, and a subagent holds its own conversation, so
the conversation the lease is minted under names exactly one browser run. `lease` mints a fresh
hosted session for that turn and `aclose` releases it, so nothing runs (or bills) past the turn.
Across the run the session carries a Browserbase Context — created once per conversation, kept in
this extension's own store, passed with `persist: true` — so a session re-minted inside the same run
(a recovery replay whose prior session was reaped) comes back with the logins and localStorage the
run had already earned. `aclose` deletes that Context with the session: a subagent's authenticated
browser state never outlives the subagent.

Chrome is remote, so files cannot travel by path in either direction. `place_file` ships a workspace
file through the session Uploads API and answers the `/tmp/.uploads/<name>` location Browserbase
puts it at, which is what the engine hands `DOM.setFileInputFiles`. `download_dir` answers the
literal `downloads`, the only target a hosted Chrome accepts — it refuses an absolute path as a
restricted directory, and a download lands in the session's storage, not on any disk this process
can read.

Sessions carry an explicit `timeout`: the plan default is minutes, while a browser run's budget
starts at twenty of them, so an unset timeout would reap the browser mid-run. It sits just above the
ceiling `browser_task` accepts, so a run always ends on its own deadline rather than losing its
browser to a reaped session.

Each session mints with Browserbase's built-in residential proxies, which is what makes the browse
arrive from a consumer address: the sites a member asks an agent to read refuse a datacenter one,
and a browse that fails a bot check costs the whole turn. `BROWSERBASE_PROXIES=false` turns them off
for a deploy that would rather pay in blocked pages than in proxied bandwidth.

The API key is a host-side credential slot read fresh on each call, so a rotated key takes effect on
the next lease and the key never enters the sandbox. Browserbase infers the project from the key.
Core ships no cdp provider, so pointing a deploy at Browserbase is this extension plus that one slot
value."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID

import httpx

from ufo.sdk.browser import CdpEndpoint, CdpLease, FileBytes, SessionGone
from ufo.sdk.context import CredentialAccess, ScopedStore
from ufo.sdk.manifest import CdpProviderSpec, CredentialSlot, Manifest
from ufo.sdk.sandbox import Sandbox

NAME = "browserbase"
VERSION = "0.1.0"
CDP_BACKEND = "browserbase"
API_KEY_SLOT = "browserbase_api_key"
PROXIES_ENV = "BROWSERBASE_PROXIES"
PROXIES_VALUES = {"true": True, "false": False}
API_BASE_URL = "https://api.browserbase.com"
API_KEY_HEADER = "X-BB-API-Key"
SESSIONS_PATH = "/v1/sessions"
CONTEXTS_PATH = "/v1/contexts"
DOWNLOADS_PATH = "/v1/downloads"
DOWNLOAD_SYNC_ATTEMPTS = 20
DOWNLOAD_SYNC_SLEEP_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 120
RELEASE_STATUS = "REQUEST_RELEASE"
LIVE_STATUSES = frozenset({"RUNNING", "PENDING"})
REMOTE_UPLOAD_DIR = "/tmp/.uploads"
REMOTE_DOWNLOAD_DIR = "downloads"
CONTEXT_KEY = "context/{conversation_id}"
TOKEN_FORMAT = "{conversation_id}/{session_id}/{context_id}"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_DIGEST_CHARS = 8
SESSION_TIMEOUT_SECONDS = 3900


def residential_proxies() -> bool:
    """Whether each hosted session routes through Browserbase's built-in residential proxies. On
    unless `BROWSERBASE_PROXIES` says `false`; any other value fails loud rather than deciding a
    deploy's browser egress by typo."""
    value = os.environ.get(PROXIES_ENV)
    if value is None:
        return True
    selected = PROXIES_VALUES.get(value.strip().lower())
    if selected is None:
        raise RuntimeError(f"{PROXIES_ENV} must be true or false, got {value!r}")
    return selected


class BrowserbaseError(RuntimeError):
    """Browserbase answered a non-2xx status or a body missing a field the seam needs — raised so
    the turn reports the transport failing, never a silently browser-less turn."""


@dataclass(frozen=True)
class BrowserbaseApi:
    """The Browserbase REST surface this provider uses, host-side over async httpx with the BYOK key
    read per call. `transport` is the httpx seam a test injects a `MockTransport` on; production
    leaves it None."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    proxies: bool = True

    async def create_session(self, context_id: str) -> tuple[str, str]:
        body = await self._json(
            "POST",
            SESSIONS_PATH,
            json={
                "browserSettings": {"context": {"id": context_id, "persist": True}},
                "timeout": SESSION_TIMEOUT_SECONDS,
                "proxies": self.proxies,
            },
        )
        return _field(body, "id"), _field(body, "connectUrl")

    async def live_session(self, session_id: str) -> str:
        body = await self._json("GET", f"{SESSIONS_PATH}/{session_id}")
        if _field(body, "status") not in LIVE_STATUSES:
            raise SessionGone(session_id)
        return _field(body, "connectUrl")

    async def release_session(self, session_id: str) -> None:
        await self._json("POST", f"{SESSIONS_PATH}/{session_id}", json={"status": RELEASE_STATUS})

    async def create_context(self) -> str:
        return _field(await self._json("POST", CONTEXTS_PATH, json={}), "id")

    async def delete_context(self, context_id: str) -> None:
        await self._send("DELETE", f"{CONTEXTS_PATH}/{context_id}", REQUEST_TIMEOUT_SECONDS)

    async def download(self, session_id: str, guid: str) -> bytes:
        """A completed download's bytes, bounded before they are pulled: the whole file lands in
        this shared process, and what a page chooses to download is not ours to trust. The listing
        reports the stored size, so an oversized one is refused without ever reading it — and a
        listing that reports no size fails rather than passing an unmeasured file through."""
        for attempt in range(DOWNLOAD_SYNC_ATTEMPTS):
            body = await self._json("GET", f"{DOWNLOADS_PATH}?sessionId={session_id}")
            listed = body.get("downloads")
            found = [
                entry
                for entry in (listed if isinstance(listed, list) else [])
                if isinstance(entry, dict) and entry.get("filename") == guid
            ]
            if found:
                entry = found[-1]
                size = _size(entry)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"download {guid} is {size} bytes; this browser returns at most "
                        f"{MAX_DOWNLOAD_BYTES}"
                    )
                response = await self._send(
                    "GET",
                    f"{DOWNLOADS_PATH}/{_field(entry, 'id')}",
                    REQUEST_TIMEOUT_SECONDS,
                    headers={"Accept": "application/octet-stream"},
                )
                return response.content
            if attempt + 1 < DOWNLOAD_SYNC_ATTEMPTS:
                await asyncio.sleep(DOWNLOAD_SYNC_SLEEP_SECONDS)
        raise BrowserbaseError(f"browserbase never stored download {guid}")

    async def upload(self, session_id: str, name: str, data: bytes) -> None:
        await self._send(
            "POST",
            f"{SESSIONS_PATH}/{session_id}/uploads",
            UPLOAD_TIMEOUT_SECONDS,
            files={"file": (name, data)},
        )

    async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        response = await self._send(method, path, REQUEST_TIMEOUT_SECONDS, **kwargs)
        body = response.json()
        if not isinstance(body, dict):
            raise BrowserbaseError(f"browserbase {path} did not answer a JSON object")
        return body

    async def _send(
        self, method: str, path: str, timeout_s: float, **kwargs: object
    ) -> httpx.Response:
        extra = kwargs.pop("headers", {})
        headers = {API_KEY_HEADER: await self.credentials.get(API_KEY_SLOT)}
        if isinstance(extra, dict):
            headers.update(extra)
        async with httpx.AsyncClient(
            base_url=API_BASE_URL, timeout=timeout_s, transport=self.transport
        ) as client:
            response = await client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]
        if response.is_error:
            raise BrowserbaseError(
                f"browserbase {method} {path} failed {response.status_code}: {response.text}"
            )
        return response


def _field(body: dict[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise BrowserbaseError(f"browserbase answered no {name}")
    return value


def _size(entry: dict[str, object]) -> int:
    """A listed download's size, required rather than optional: it is the only thing standing
    between a page's choice of file and this process's memory, so a listing without it fails the
    fetch instead of waving it through."""
    value = entry.get("size")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BrowserbaseError(f"browserbase listed a download with no size: {entry!r}")
    return int(value)


@dataclass(frozen=True)
class BrowserbaseLease:
    """One browser run's hold on its hosted session: `endpoint` is the connect URL, `token` the
    handle a recovered turn reattaches by, `place_file` uploads a workspace file the page needs,
    and `aclose` releases the session and deletes the run's Context — the session first, so a
    Context is never deleted out from under a browser still holding it."""

    api: BrowserbaseApi
    store: ScopedStore
    conversation_id: UUID
    session_id: str
    connect_url: str
    context_id: str
    staged: dict[str, str] = field(default_factory=dict)

    async def endpoint(self) -> CdpEndpoint:
        return CdpEndpoint(url=self.connect_url)

    async def token(self) -> str:
        """Every id a reattach needs to drive this run and then clean up after it. A reattach
        arrives with the token alone, so the run's conversation and Context ride in it rather than
        being searched for — two runs in one workspace hold two sessions, and neither can resolve
        to the other's Context."""
        return TOKEN_FORMAT.format(
            conversation_id=self.conversation_id,
            session_id=self.session_id,
            context_id=self.context_id,
        )

    async def place_file(self, path: str, read: FileBytes) -> str:
        """Ship the file and answer where Browserbase put it. Uploads land under one flat remote
        directory keyed by file name, so two workspace files sharing a base name would overwrite
        each other and the page would receive the same bytes twice; the second and later senders
        of a name are qualified by a digest of their path. The first keeps its plain name, which
        is what the site sees. A name is claimed only once its bytes have landed, so an upload that
        fails to read — or fails to send — leaves the name free for the retry."""
        base = PurePosixPath(path).name
        if not base:
            raise ValueError(f"cannot upload a path with no file name: {path!r}")
        data = await read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"{base} is {len(data)} bytes; browserbase uploads are capped at {MAX_UPLOAD_BYTES}"
            )
        name = base
        if self.staged.get(name, path) != path:
            name = f"{sha256(path.encode()).hexdigest()[:UPLOAD_DIGEST_CHARS]}-{name}"
        await self.api.upload(self.session_id, name, data)
        self.staged[name] = path
        return f"{REMOTE_UPLOAD_DIR}/{name}"

    async def download_dir(self) -> str:
        """The literal Browserbase requires: a hosted Chrome refuses any absolute path as a
        restricted directory, and only this one routes a download into the session's own storage."""
        return REMOTE_DOWNLOAD_DIR

    async def fetch_download(self, guid: str) -> bytes:
        """The download's bytes, out of the session's storage. `allowAndName` names the stored file
        by its CDP guid, which is the `filename` the Downloads API reports, so that is what a
        session's downloads are matched on. The sync into storage trails the browser finishing the
        file, so a listing that does not have it yet is retried rather than reported empty."""
        return await self.api.download(self.session_id, guid)

    async def aclose(self) -> None:
        """Release the session, then drop the run's Context. The store row goes in its own
        `finally`: a Context delete that fails transiently must not leave the row naming a Context
        the next lease would hand to `create_session`, which would fail every later browse in this
        conversation. Leaking a Context costs storage; keeping the row costs the conversation."""
        try:
            await self.api.release_session(self.session_id)
        finally:
            try:
                await self.api.delete_context(self.context_id)
            finally:
                await self.store.delete(CONTEXT_KEY.format(conversation_id=self.conversation_id))


@dataclass(frozen=True)
class BrowserbaseCdpProvider:
    """Core's `cdp_providers` seam backed by Browserbase. `lease` reads the browser run's identity
    off the turn's sandbox — a subagent turn holds its own conversation, so that is the run —
    gets or creates the run's Context, and mints a session against it. `reattach` yields a lease
    over the session its token names when Browserbase still holds it, else raises `SessionGone` so
    the caller mints fresh. `proxies` is the deploy's residential-proxy setting, read once at boot
    and carried onto every session this provider mints."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    proxies: bool = True

    async def lease(self, sandbox: Sandbox | None = None) -> CdpLease:
        if sandbox is None:
            raise RuntimeError(
                "the browserbase cdp provider needs the turn's sandbox to name the browser run"
            )
        conversation_id = sandbox.conversation_id
        api = BrowserbaseApi(
            credentials=self.credentials, transport=self.transport, proxies=self.proxies
        )
        store = ScopedStore(extension=NAME)
        context_id = await self._context(api, store, conversation_id)
        session_id, connect_url = await api.create_session(context_id)
        return BrowserbaseLease(
            api=api,
            store=store,
            conversation_id=conversation_id,
            session_id=session_id,
            connect_url=connect_url,
            context_id=context_id,
        )

    async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str:
        key = CONTEXT_KEY.format(conversation_id=conversation_id)
        stored = await store.get(key)
        if isinstance(stored, str) and stored:
            return stored
        context_id = await api.create_context()
        await store.put(key, context_id)
        return context_id

    async def reattach(self, token: str, sandbox: Sandbox | None = None) -> CdpLease:
        conversation_id, session_id, context_id = _parse_token(token)
        api = BrowserbaseApi(
            credentials=self.credentials, transport=self.transport, proxies=self.proxies
        )
        connect_url = await api.live_session(session_id)
        return BrowserbaseLease(
            api=api,
            store=ScopedStore(extension=NAME),
            conversation_id=conversation_id,
            session_id=session_id,
            connect_url=connect_url,
            context_id=context_id,
        )


def _parse_token(token: str) -> tuple[UUID, str, str]:
    conversation_id, _, rest = token.partition("/")
    session_id, _, context_id = rest.partition("/")
    if not session_id or not context_id:
        raise SessionGone(token)
    try:
        return UUID(conversation_id), session_id, context_id
    except ValueError:
        raise SessionGone(token) from None


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=API_KEY_SLOT,
                description=(
                    "Browserbase API key; the cdp provider reads it host-side to mint and release "
                    "each browser run's hosted session, never in the sandbox."
                ),
            ),
        ),
        cdp_providers=(
            CdpProviderSpec(
                backend=CDP_BACKEND,
                build=lambda creds: BrowserbaseCdpProvider(creds, proxies=residential_proxies()),
            ),
        ),
    )
