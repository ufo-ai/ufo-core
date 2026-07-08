"""The `browserbase` cdp provider: the turn's Chrome runs in a Browserbase-hosted session reached
over a static CDP connect URL, selected with `[browser] cdp_provider = "browserbase"`.

Unlike the sandbox-derived `sandbox_chrome` provider, the endpoint is remote and fixed — a single
Browserbase connect URL (which embeds the account's API key) that outlives every turn, so the lease
is static and `reattach` reconnects to the same URL. The URL is a host-side BYOK credential slot,
read fresh each lease so a rotated URL takes effect next turn; it never enters the sandbox. Core
ships no cdp provider, so pointing a deploy at Browserbase is entirely this extension plus the slot
value — nothing in core."""

from __future__ import annotations

from dataclasses import dataclass

from ufo.sdk.browser import CdpEndpoint, CdpLease
from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CdpProviderSpec, CredentialSlot, Manifest
from ufo.sdk.sandbox import SandboxSession

NAME = "browserbase"
VERSION = "0.1.0"
CDP_BACKEND = "browserbase"
CONNECT_URL_SLOT = "browserbase_cdp_url"


@dataclass(frozen=True)
class StaticLease:
    """A lease over the fixed Browserbase endpoint: `endpoint` returns it, `token` is the URL itself
    (the reattach handle), and `aclose` is a no-op — the hosted session outlives the turn."""

    endpoint_: CdpEndpoint

    async def endpoint(self) -> CdpEndpoint:
        return self.endpoint_

    async def token(self) -> str:
        return self.endpoint_.url

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class BrowserbaseCdpProvider:
    """Leases the static Browserbase CDP endpoint, reading the connect URL host-side from the
    `browserbase_cdp_url` slot on each lease. Ignores the turn's sandbox — the browser is remote,
    not the turn's container."""

    credentials: CredentialAccess

    async def _endpoint(self) -> CdpEndpoint:
        return CdpEndpoint(url=await self.credentials.get(CONNECT_URL_SLOT))

    async def lease(self, sandbox: SandboxSession | None = None) -> CdpLease:
        return StaticLease(await self._endpoint())

    async def reattach(self, token: str) -> CdpLease:
        return StaticLease(await self._endpoint())


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=CONNECT_URL_SLOT,
                description=(
                    "BYOK Browserbase CDP connect URL (wss://…, embeds your API key); the provider "
                    "reads it host-side to reach the hosted browser, never in the sandbox."
                ),
            ),
        ),
        cdp_providers=(
            CdpProviderSpec(backend=CDP_BACKEND, build=lambda creds: BrowserbaseCdpProvider(creds)),
        ),
    )
