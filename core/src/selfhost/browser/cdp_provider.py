"""Where the CDP endpoint comes from: a provider chain the default (BUA) engine resolves per turn.

The engine connects whatever CDP URL the provider yields, so the provider is the seam that
decouples the engine from where Chrome runs: `BROWSER_CDP_URL` (this unit — a headless Chrome, or a
sandbox-mapped port once the carrier exposes one) reads through `env_browser_cdp_provider`; a
browserbase extension slots in its own remote provider at the same seam without touching the engine.
The chain tries a hosted provider before a local one so a deploy can prefer a managed endpoint and
fall back; an empty chain fails loud rather than silently connecting nothing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from selfhost.browser.wire import BrowserCdp, BrowserCdpProvider, ValidationError

BROWSER_CDP_URL_ENV = "BROWSER_CDP_URL"
BROWSER_CDP_HEADERS_ENV = "BROWSER_CDP_HEADERS"


@dataclass(frozen=True)
class StaticBrowserCdpProvider:
    endpoint: BrowserCdp

    async def __call__(self) -> BrowserCdp:
        return self.endpoint


@dataclass(frozen=True)
class BrowserCdpProviderChain:
    hosted: BrowserCdpProvider | None
    local: BrowserCdpProvider | None

    async def __call__(self) -> BrowserCdp:
        if self.hosted is not None:
            return await self.hosted()
        if self.local is not None:
            return await self.local()
        raise RuntimeError(
            f"browser CDP endpoint is required — set {BROWSER_CDP_URL_ENV} to a reachable "
            "Chrome DevTools endpoint, or install a browser backend extension that provides one"
        )


def env_browser_cdp_provider() -> BrowserCdpProvider | None:
    endpoint = env_browser_cdp()
    return StaticBrowserCdpProvider(endpoint) if endpoint is not None else None


def env_browser_cdp() -> BrowserCdp | None:
    url = os.environ.get(BROWSER_CDP_URL_ENV)
    return BrowserCdp(url, env_browser_cdp_headers()) if url else None


def env_browser_cdp_headers() -> dict[str, str]:
    raw = os.environ.get(BROWSER_CDP_HEADERS_ENV)
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValidationError(f"{BROWSER_CDP_HEADERS_ENV} must be a JSON object")
    headers: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValidationError(f"{BROWSER_CDP_HEADERS_ENV} values must be strings")
        headers[key] = value
    return headers
