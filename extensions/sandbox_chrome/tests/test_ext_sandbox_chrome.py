"""The sandbox_chrome cdp provider driven against a fake carrier: the lease launches Chrome and the
Host-rewrite proxy inside the sandbox, resolves the DevTools websocket path, and builds the wss
endpoint over the carrier's public per-port host with the sandbox's traffic token as a header. The
carrier is a real `SandboxSession` over a fake `Carrier` that records the commands it is asked to
run and answers the websocket probe — so the provider is exercised through the same seam the loop
uses, never a mocked provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import ufo_ext_sandbox_chrome as ext

from ufo.browser import SessionGone
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession

CANNED_WS = "ws://127.0.0.1:9222/devtools/browser/9f1c-abc-123"
FAKE_HOST = "9223-sbx123.e2b.app"


@dataclass
class FakeCarrier:
    """Records the shell commands the session runs and answers the websocket probe with a canned
    DevTools url; `host` returns the carrier's public per-port host. A real object the session
    drives through the `Carrier` protocol, never a mock of the provider under test."""

    commands: list[str] = field(default_factory=list)
    host_ports: list[int] = field(default_factory=list)
    host_value: str = FAKE_HOST

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        command = argv[-1]
        self.commands.append(command)
        if "webSocketDebuggerUrl" in command:
            return ExecResult(stdout=f"{CANNED_WS}\n", stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def host(self, handle: SandboxHandle, port: int) -> str:
        self.host_ports.append(port)
        return self.host_value


@dataclass
class FailingCarrier:
    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="Chromium is required", exit_code=127)

    async def host(self, handle: SandboxHandle, port: int) -> str:
        raise AssertionError("host must not be reached when chrome fails to start")


def _session(carrier: object, *, traffic_token: str | None = "tok-xyz") -> SandboxSession:
    handle = SandboxHandle(
        conversation_id=uuid4(), container_id="sbx123", traffic_token=traffic_token
    )
    return SandboxSession(carrier=carrier, handle=handle)  # type: ignore[arg-type]


async def test_lease_builds_the_wss_endpoint_with_the_traffic_header() -> None:
    carrier = FakeCarrier()
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    endpoint = await lease.endpoint()
    assert endpoint.url == "wss://9223-sbx123.e2b.app/devtools/browser/9f1c-abc-123"
    assert endpoint.headers == {"e2b-traffic-access-token": "tok-xyz"}
    assert carrier.host_ports == [9223]
    assert await lease.token() == endpoint.url
    assert await lease.aclose() is None


async def test_lease_execs_the_chrome_launch_then_the_host_rewrite_proxy() -> None:
    carrier = FakeCarrier()
    await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert len(carrier.commands) == 3
    chrome, proxy, ws = carrier.commands
    assert "--remote-debugging-port=9222" in chrome
    assert "--remote-allow-origins='*'" in chrome
    assert "/tmp/ufo-browser.pid" in chrome
    assert "CHROME_PORT = 9222" in proxy
    assert "PROXY_PORT = 9223" in proxy
    assert 'f"Host: {CHROME_HOST}:{CHROME_PORT}"' in proxy
    assert "/tmp/ufo-browser-proxy.py" in proxy
    assert "webSocketDebuggerUrl" in ws


async def test_lease_without_a_sandbox_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="needs the turn's sandbox"):
        await ext.SandboxChromeCdpProvider().lease(None)


async def test_lease_omits_the_header_when_the_sandbox_has_no_traffic_token() -> None:
    lease = await ext.SandboxChromeCdpProvider().lease(_session(FakeCarrier(), traffic_token=None))
    assert (await lease.endpoint()).headers == {}


async def test_lease_raises_when_chrome_fails_to_start() -> None:
    with pytest.raises(RuntimeError, match="start browser"):
        await ext.SandboxChromeCdpProvider().lease(_session(FailingCarrier()))


async def test_reattach_reports_session_gone_so_the_caller_re_leases() -> None:
    with pytest.raises(SessionGone):
        await ext.SandboxChromeCdpProvider().reattach("wss://stale")


def test_manifest_registers_the_sandbox_chrome_cdp_provider() -> None:
    manifest = ext.manifest()
    assert manifest.name == "sandbox_chrome"
    specs = {spec.backend: spec for spec in manifest.cdp_providers}
    assert set(specs) == {"sandbox_chrome"}
    provider = specs["sandbox_chrome"].build(None)  # the factory ignores the credential reader
    assert isinstance(provider, ext.SandboxChromeCdpProvider)
