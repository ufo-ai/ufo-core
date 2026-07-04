"""The browser seam: where a turn's Chrome comes from, decoupled from the engine that drives it.

Core owns only the transport seam, never a browser engine. A `CdpProvider` — built once at boot,
selected by `config.browser.cdp_provider` — mints a per-turn `CdpLease`: a `CdpEndpoint` (a
resolvable CDP URL plus any connection headers) held for the turn and released at turn end. Core's
default `SandboxCdpProvider` wraps the `BROWSER_CDP_URL` endpoint in a static lease (a headless
Chrome, or a sandbox-mapped port); an extension registers another provider at the `cdp_providers`
Manifest seam, where browserbase mints and releases a fresh hosted session per turn. The engine that
connects the endpoint and drives the page is the browser extension, never core. `FindCompleter` is
the host-side element-ranking hook that engine calls back through, metered onto the turn."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

BROWSER_CDP_URL_ENV = "BROWSER_CDP_URL"
BROWSER_CDP_HEADERS_ENV = "BROWSER_CDP_HEADERS"

FindCompleter = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class CdpEndpoint:
    """A resolvable CDP endpoint: the URL the engine connects to plus any headers the connection
    carries (a traffic token for a sandbox-mapped port, an auth header for a remote provider)."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


class CdpLease(Protocol):
    """A per-turn hold on a CDP endpoint: `endpoint` yields the URL to connect (raising when none is
    configured), `aclose` releases the hold at turn end — a no-op for a static endpoint, a hosted
    session release for a remote provider."""

    async def endpoint(self) -> CdpEndpoint: ...

    async def aclose(self) -> None: ...


class CdpProvider(Protocol):
    """Where a turn's Chrome comes from: `lease` mints one `CdpLease` per turn. Process-wide (built
    once at boot), so a remote provider mints and releases a fresh hosted session each turn while a
    sandbox provider wraps a static environment endpoint."""

    async def lease(self) -> CdpLease: ...


@dataclass(frozen=True)
class StaticCdpLease:
    """A lease over a fixed endpoint: `endpoint` returns it, raising when none is configured;
    `aclose` is a no-op because the endpoint outlives the turn (a persistent sandbox Chrome)."""

    endpoint_: CdpEndpoint | None

    async def endpoint(self) -> CdpEndpoint:
        if self.endpoint_ is None:
            raise RuntimeError(
                f"browser CDP endpoint is required — set {BROWSER_CDP_URL_ENV} to a reachable "
                "Chrome DevTools endpoint, or install a cdp-provider extension that yields one"
            )
        return self.endpoint_

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class SandboxCdpProvider:
    """Core's default cdp provider: a static lease over the `BROWSER_CDP_URL` endpoint (a headless
    Chrome, or a sandbox-mapped port once the carrier exposes one), read from the environment. The
    endpoint outlives every turn, so the lease is static and its release a no-op; a missing URL
    fails loud at connect, never silently connecting nothing."""

    endpoint: CdpEndpoint | None

    @classmethod
    def from_env(cls) -> SandboxCdpProvider:
        url = os.environ.get(BROWSER_CDP_URL_ENV)
        return cls(endpoint=CdpEndpoint(url, _env_cdp_headers()) if url else None)

    async def lease(self) -> CdpLease:
        return StaticCdpLease(self.endpoint)


def _env_cdp_headers() -> dict[str, str]:
    raw = os.environ.get(BROWSER_CDP_HEADERS_ENV)
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{BROWSER_CDP_HEADERS_ENV} must be a JSON object")
    headers: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{BROWSER_CDP_HEADERS_ENV} values must be strings")
        headers[key] = value
    return headers
