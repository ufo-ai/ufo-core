"""The core cdp seam: the default `SandboxCdpProvider` wraps the `BROWSER_CDP_URL` endpoint in a
static lease. The `lease` signature carries an optional sandbox (so a per-conversation-sandbox
provider can resolve Chrome inside the turn's sandbox); the static provider ignores it and yields
the same endpoint with or without one."""

from uuid import uuid4

import pytest

from ufo.browser import CdpEndpoint, SandboxCdpProvider, SessionGone, StaticCdpLease
from ufo.sandbox.session import SandboxHandle, SandboxSession


class _UnreachableCarrier:
    """A carrier the static provider must never touch — `lease` resolves from the configured
    endpoint alone, so passing the turn's sandbox changes nothing."""

    async def host(self, handle: SandboxHandle, port: int) -> str:
        raise AssertionError("the static provider must not reach the carrier")


def _sandbox() -> SandboxSession:
    return SandboxSession(
        carrier=_UnreachableCarrier(),  # type: ignore[arg-type]
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )


async def test_static_lease_ignores_the_sandbox_argument() -> None:
    endpoint = CdpEndpoint(url="ws://chrome.test:9222/devtools/browser/x")
    provider = SandboxCdpProvider(endpoint=endpoint)

    bare = await provider.lease()
    with_sandbox = await provider.lease(_sandbox())

    assert isinstance(bare, StaticCdpLease)
    assert isinstance(with_sandbox, StaticCdpLease)
    assert await bare.endpoint() == endpoint
    assert await with_sandbox.endpoint() == endpoint


async def test_unconfigured_static_lease_fails_loud_with_or_without_a_sandbox() -> None:
    provider = SandboxCdpProvider(endpoint=None)
    for lease in (await provider.lease(), await provider.lease(_sandbox())):
        with pytest.raises(RuntimeError, match="BROWSER_CDP_URL"):
            await lease.endpoint()


async def test_reattach_reports_the_session_gone_when_the_endpoint_is_dropped() -> None:
    with pytest.raises(SessionGone):
        await SandboxCdpProvider(endpoint=None).reattach("ws://old")
