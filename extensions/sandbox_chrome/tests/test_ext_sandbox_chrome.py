"""The sandbox_chrome cdp provider driven against a fake carrier: one bring-up command brings Chrome
and the Host-rewrite proxy up inside the sandbox and yields the DevTools websocket path, which the
lease turns into the wss endpoint over the carrier's public per-port host. The carrier is a real
`SandboxSession` over a fake `Carrier` that records the command it is asked to run and answers with
a canned DevTools url — so the provider is exercised through the same seam the loop uses, never a
mocked provider. The commands themselves run for real, against a real Chrome, in
`tests/integration/test_sandbox_chrome_lease.py`."""

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
    """Records the shell commands the session runs and answers the bring-up with a canned DevTools
    url; `host` returns the carrier's public per-port host. A real object the session drives through
    the `Carrier` protocol, never a mock of the provider under test."""

    commands: list[str] = field(default_factory=list)
    host_ports: list[int] = field(default_factory=list)
    timeouts: list[int] = field(default_factory=list)
    host_value: str = FAKE_HOST

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        self.commands.append(argv[-1])
        self.timeouts.append(timeout_s)
        return ExecResult(stdout=f"{CANNED_WS}\n", stderr="", exit_code=0)

    async def host(self, handle: SandboxHandle, port: int) -> str:
        self.host_ports.append(port)
        return self.host_value


@dataclass
class FailingCarrier:
    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        return ExecResult(
            stdout="", stderr="the browser exited 1 without serving\nAbort trap", exit_code=1
        )

    async def host(self, handle: SandboxHandle, port: int) -> str:
        raise AssertionError("host must not be reached when the browser fails to come up")


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


async def test_lease_brings_the_browser_up_in_one_command() -> None:
    carrier = FakeCarrier()
    await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert len(carrier.commands) == 1
    command = carrier.commands[0]
    assert "--remote-debugging-port=9222" in command
    assert "--remote-allow-origins=*" in command
    assert ext.CHROME_PID_PATH in command
    assert "CHROME_PORT = 9222" in command
    assert "PROXY_PORT = 9223" in command
    assert 'f"Host: {CHROME_HOST}:{CHROME_PORT}"' in command
    assert ext.PROXY_SCRIPT_PATH in command
    assert f"http://127.0.0.1:{ext.BROWSER_CDP_PROXY_PORT}/json/version" in command


async def test_the_carrier_deadline_leaves_the_command_room_to_report_its_own_failure() -> None:
    """Every wait the command can enclose must end before the carrier kills it, or a browser that
    never answers is reported as the carrier's timeout and its log — the only thing that says why —
    is lost. This is the arithmetic the reported outage failed: a 45s wait under a 45s deadline. The
    sum is what matters, since the stages wait one after another."""
    carrier = FakeCarrier()
    await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert carrier.timeouts == [ext.BROWSER_START_TIMEOUT_SECONDS]
    assert ext.IN_SANDBOX_WAIT_SECONDS < ext.BROWSER_START_TIMEOUT_SECONDS
    assert (
        ext.CHROME_READY_BUDGET_SECONDS
        + ext.PROXY_READY_BUDGET_SECONDS
        + 2 * ext.PROCESS_END_BUDGET_SECONDS
        == ext.IN_SANDBOX_WAIT_SECONDS
    )


async def test_lease_without_a_sandbox_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="needs the turn's sandbox"):
        await ext.SandboxChromeCdpProvider().lease(None)


async def test_lease_omits_the_header_when_the_sandbox_has_no_traffic_token() -> None:
    lease = await ext.SandboxChromeCdpProvider().lease(_session(FakeCarrier(), traffic_token=None))
    assert (await lease.endpoint()).headers == {}


async def test_lease_surfaces_what_the_bring_up_reported() -> None:
    with pytest.raises(RuntimeError, match="Abort trap"):
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
