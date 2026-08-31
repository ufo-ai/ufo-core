"""The browser seam: where a turn's Chrome comes from, decoupled from the engine that drives it.

Core owns only the transport seam — never a browser engine, and never a default provider. A
`CdpProvider`, built once at boot and selected by `config.browser.cdp_provider`, mints a per-turn
`CdpLease`: a `CdpEndpoint` (a resolvable CDP URL plus any connection headers) held for the turn and
released at turn end. Every provider is contributed by an extension at the `cdp_providers` Manifest
seam — the `sandbox_chrome` provider resolves its endpoint against the Chrome running inside the
turn's own sandbox, the `browserbase` provider leases a remote hosted session. The engine that
connects the endpoint and drives the page is the browser extension, never core. `FindCompleter` is
the host-side element-ranking hook that engine calls back through, metered onto the turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from ufo.harness.sandbox.session import Sandbox

FindCompleter = Callable[[str, str], Awaitable[str]]
FileBytes = Callable[[], Awaitable[bytes]]


class SessionGone(Exception):
    """A `CdpProvider.reattach` found the session its token names no longer live — a hosted session
    reaped past its TTL, or a dead endpoint. The caller mints a fresh lease instead of reconnecting,
    re-grounding the task rather than resuming against a page that is gone."""


@dataclass(frozen=True)
class CdpEndpoint:
    """A resolvable CDP endpoint: the URL the engine connects to plus any headers the connection
    carries (a traffic token for a sandbox-mapped port, an auth header for a remote provider)."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


class CdpLease(Protocol):
    """A per-turn hold on a CDP endpoint: `endpoint` yields the URL to connect (raising when none is
    configured), `token` yields a durable, serializable reattach handle (a hosted session id, or the
    static URL) the browser extension persists so a recovered turn can reconnect, and `aclose`
    releases the hold at turn end — a no-op for a static endpoint, a hosted session release for a
    remote provider.

    `place_file` answers where the leased Chrome can open a workspace file, because only the
    transport knows whether that Chrome shares the turn's filesystem: a provider running Chrome in
    the turn's own sandbox answers the same path, while a remote provider ships the bytes and
    answers the location it uploaded them to. `read` pulls the bytes out of the sandbox and is
    awaited only by a transport that must ship them, so a sandbox-local Chrome copies nothing.

    `download_dir` and `fetch_download` are the same question in the other direction: where this
    Chrome may write a download, and how its bytes come back. A browser in the turn's sandbox writes
    to a path there and the bytes are read from the sandbox; a hosted browser writes into its
    provider's storage and the bytes come back over that provider's API. Chrome is told
    `download_dir` with `allowAndName`, so a completed download is stored under its CDP guid — which
    is what `fetch_download` is keyed on."""

    async def endpoint(self) -> CdpEndpoint: ...

    async def token(self) -> str: ...

    async def place_file(self, path: str, read: FileBytes) -> str: ...

    async def download_dir(self) -> str: ...

    async def fetch_download(self, guid: str) -> bytes: ...

    async def aclose(self) -> None: ...


class CdpProvider(Protocol):
    """Where a turn's Chrome comes from: `lease` mints one `CdpLease` per turn, optionally given the
    turn's `Sandbox` so a per-conversation-sandbox provider resolves its endpoint against the
    Chrome running inside that sandbox (a static or remote provider ignores it); `reattach`
    reconnects to the session a prior run's `token` names — returning a fresh lease over the live
    session, or raising `SessionGone` when it can no longer resolve so the caller mints instead.
    Process-wide (built once at boot), so a remote provider mints and releases a fresh hosted
    session each turn (reattachable within its TTL) while a sandbox provider wraps a static
    environment endpoint that outlives every turn."""

    async def lease(self, sandbox: Sandbox | None = None) -> CdpLease: ...

    async def reattach(self, token: str) -> CdpLease: ...
