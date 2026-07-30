"""The sandbox_chrome cdp provider driven against a fake carrier: one bring-up command brings Chrome
and the Host-rewrite proxy up inside the sandbox and yields the DevTools websocket path, which the
lease turns into the wss endpoint over the carrier's public per-port host. The carrier is a real
`SandboxSession` over a fake `Carrier` that records the command it is asked to run and answers with
a canned DevTools url — so the provider is exercised through the same seam the loop uses, never a
mocked provider. The commands themselves run for real, against a real Chrome, in
`tests/integration/test_sandbox_chrome_lease.py`."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import ufo_ext_sandbox_chrome as ext

from ufo.browser import SessionGone
from ufo.sandbox.session import DialTarget, ExecResult, SandboxHandle, SandboxSession

CANNED_WS = "ws://127.0.0.1:9222/devtools/browser/9f1c-abc-123"
FAKE_HOST = "9223-sbx123.e2b.app"


@dataclass
class FakeCarrier:
    """Records the shell commands the session runs and answers the bring-up with a canned DevTools
    url; `dial` returns the carrier's public per-port target. A real object the session drives
    through the `Carrier` protocol, never a mock of the provider under test."""

    commands: list[str] = field(default_factory=list)
    timeouts: list[int] = field(default_factory=list)
    dial_ports: list[int] = field(default_factory=list)
    dial_target: DialTarget = field(
        default_factory=lambda: DialTarget(
            host=FAKE_HOST, tls=True, headers={"e2b-traffic-access-token": "tok-xyz"}
        )
    )
    download_bytes: bytes = b""
    download_size: int = 0

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        command = argv[-1]
        self.commands.append(command)
        self.timeouts.append(timeout_s)
        if command.startswith("wc -c"):
            return ExecResult(stdout=f"{self.download_size}\n", stderr="", exit_code=0)
        if command.startswith("base64"):
            return ExecResult(
                stdout=base64.b64encode(self.download_bytes).decode(), stderr="", exit_code=0
            )
        return ExecResult(stdout=f"{CANNED_WS}\n", stderr="", exit_code=0)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        self.dial_ports.append(port)
        return self.dial_target


@dataclass
class ShellCarrier:
    """Answers the size read with a canned byte count and runs every other command through a real
    shell. A fake cannot answer what a shell reports for the encode command's own failure, which is
    the thing under test; canning the size is what puts the read in the state that matters — a file
    the sizing saw and the encoder cannot open."""

    sized: int = 8

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        if argv[-1].startswith("wc -c"):
            return ExecResult(stdout=f"{self.sized}\n", stderr="", exit_code=0)
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return ExecResult(
            stdout=stdout.decode(),
            stderr=stderr.decode(),
            exit_code=process.returncode or 0,
        )

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("the sandbox is not dialed when a download is read")


@dataclass
class FailingCarrier:
    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        return ExecResult(
            stdout="", stderr="the browser exited 1 without serving\nAbort trap", exit_code=1
        )

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("dial must not be reached when the browser fails to come up")


def _session(carrier: object) -> SandboxSession:
    handle = SandboxHandle(conversation_id=uuid4(), container_id="sbx123")
    return SandboxSession(carrier=carrier, handle=handle)  # type: ignore[arg-type]


async def test_lease_builds_the_wss_endpoint_with_the_traffic_header() -> None:
    carrier = FakeCarrier()
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    endpoint = await lease.endpoint()
    assert endpoint.url == "wss://9223-sbx123.e2b.app/devtools/browser/9f1c-abc-123"
    assert endpoint.headers == {"e2b-traffic-access-token": "tok-xyz"}
    assert carrier.dial_ports == [9223]
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


async def test_lease_omits_the_header_when_the_dial_target_carries_none() -> None:
    carrier = FakeCarrier(dial_target=DialTarget(host=FAKE_HOST, tls=True))
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert (await lease.endpoint()).headers == {}


async def test_lease_surfaces_what_the_bring_up_reported() -> None:
    with pytest.raises(RuntimeError, match="Abort trap"):
        await ext.SandboxChromeCdpProvider().lease(_session(FailingCarrier()))


async def test_reattach_reports_session_gone_so_the_caller_re_leases() -> None:
    with pytest.raises(SessionGone):
        await ext.SandboxChromeCdpProvider().reattach("wss://stale")


async def test_downloads_land_in_the_sandbox_and_are_read_back_from_it() -> None:
    """Chrome runs in the sandbox, so a download it takes exists only there: the target is a sandbox
    path and the bytes come back through the sandbox, which is what a serve-side temp directory
    could never do once the sandbox stopped sharing serve's filesystem."""
    carrier = FakeCarrier()
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert await lease.download_dir() == f"{ext.BROWSER_DIR}/downloads"
    carrier.download_bytes = b"the downloaded file"
    carrier.download_size = len(b"the downloaded file")
    assert await lease.fetch_download("guid-9") == b"the downloaded file"
    assert any(f"{ext.DOWNLOAD_DIR}/guid-9" in command for command in carrier.commands)


async def test_place_file_answers_the_workspace_path_without_reading_it() -> None:
    """This Chrome opens the workspace itself, so the path a file input needs is the path the
    caller already holds — and the bytes-reader the seam offers must go unawaited rather than
    copying a file out of the sandbox and back into it."""
    read_calls = 0

    async def read() -> bytes:
        nonlocal read_calls
        read_calls += 1
        return b"never needed"

    lease = await ext.SandboxChromeCdpProvider().lease(_session(FakeCarrier()))
    assert await lease.place_file("/workspace/report.pdf", read) == "/workspace/report.pdf"
    assert read_calls == 0


async def test_a_download_the_sandbox_cannot_read_fails_loud() -> None:
    lease = ext.SandboxChromeCdpLease(
        ext.CdpEndpoint(url="wss://sandbox.test/devtools"), _session(FailingCarrier())
    )
    with pytest.raises(RuntimeError, match="could not read download"):
        await lease.fetch_download("guid-9")


async def test_a_download_the_encoder_cannot_open_raises_instead_of_returning_nothing() -> None:
    """The size read and the byte read are two commands, so a file the first one saw can be gone by
    the second — a Chrome that cleaned up, a sandbox that recycled. The encoder's own exit code has
    to reach the caller: piping it anywhere reports the pipe's last stage, which turns an unreadable
    download into a silent empty one."""
    lease = ext.SandboxChromeCdpLease(
        ext.CdpEndpoint(url="wss://sandbox.test/devtools"), _session(ShellCarrier())
    )
    with pytest.raises(RuntimeError, match="could not read download"):
        await lease.fetch_download("guid-9")


async def test_an_oversized_download_is_refused_before_it_is_encoded() -> None:
    """The file crosses whole into this shared process, so its size decides before any of it is
    read — the same bound the upload side already holds."""
    carrier = FakeCarrier()
    carrier.download_size = ext.MAX_DOWNLOAD_BYTES + 1
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    with pytest.raises(ValueError, match="at most"):
        await lease.fetch_download("guid-9")
    assert not any(command.startswith("base64") for command in carrier.commands)


def test_manifest_registers_the_sandbox_chrome_cdp_provider() -> None:
    manifest = ext.manifest()
    assert manifest.name == "sandbox_chrome"
    specs = {spec.backend: spec for spec in manifest.cdp_providers}
    assert set(specs) == {"sandbox_chrome"}
    provider = specs["sandbox_chrome"].build(None)  # the factory ignores the credential reader
    assert isinstance(provider, ext.SandboxChromeCdpProvider)
