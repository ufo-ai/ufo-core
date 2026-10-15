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
import contextlib
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
import ufo_ext_sandbox_chrome as ext

from ufo.harness.sandbox.session import (
    PLAYWRIGHT_VERSION,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSession,
)

CANNED_WS = f"ws://127.0.0.1:{ext.STACK_PORT_START}/devtools/browser/9f1c-abc-123"
FAKE_HOST = "9223-sbx123.e2b.app"
BROWSER_DIR = "/var/tmp/ufo-browser"
ALLOCATED = json.dumps({"slot": 0, "dir": BROWSER_DIR})


@dataclass
class FakeCarrier:
    """Records the shell commands the session runs and answers the bring-up with a canned
    DevTools url; `dial` returns the carrier's public per-port target."""

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
    task_detaches: list[bool] = field(default_factory=list)
    allocation_result_lost: bool = False
    found: str = ALLOCATED
    allocation_failures_remaining: int = 0
    find_failures_remaining: int = 0
    cleanup_fails: bool = False
    bring_up_fails: bool = False

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        command = argv[-1]
        self.commands.append(command)
        self.timeouts.append(timeout_s)
        if "--task" in argv:
            self.task_detaches.append("--detach" in argv)
        if command.startswith("wc -c"):
            return ExecResult(stdout=f"{self.download_size}\n", stderr="", exit_code=0)
        if command.startswith("base64"):
            return ExecResult(
                stdout=base64.b64encode(self.download_bytes).decode(), stderr="", exit_code=0
            )
        if "# sandbox_chrome allocate" in command:
            if self.allocation_failures_remaining:
                self.allocation_failures_remaining -= 1
                return ExecResult(stdout="", stderr="root already exists", exit_code=1)
            if self.allocation_result_lost:
                return ExecResult(
                    stdout="", stderr="lost allocation result", exit_code=124, timed_out_after_s=15
                )
            return ExecResult(stdout=f"{ALLOCATED}\n", stderr="", exit_code=0)
        if "# sandbox_chrome find allocation" in command:
            if self.find_failures_remaining:
                self.find_failures_remaining -= 1
                return ExecResult(stdout="", stderr="allocation is not ready", exit_code=1)
            return ExecResult(stdout=f"{self.found}\n", stderr="", exit_code=0)
        if "# sandbox_chrome bring up" in command and self.bring_up_fails:
            return ExecResult(stdout="", stderr="the browser exited 1", exit_code=1)
        if "# sandbox_chrome bring up" in command or "# sandbox_chrome resolve" in command:
            return ExecResult(stdout=f"{CANNED_WS}\n", stderr="", exit_code=0)
        if "# sandbox_chrome clean up" in command and self.cleanup_fails:
            return ExecResult(stdout="", stderr="cleanup unavailable", exit_code=1)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        self.dial_ports.append(port)
        return self.dial_target


@dataclass
class ExpiringCarrier:
    """A carrier whose deadline kills every command that does real work: the command reports
    nothing of its own, and the logs it left inside the sandbox are all that says why."""

    log_tail: str = "chromium: error while loading shared libraries"
    sized: int = 8
    timeouts: list[int] = field(default_factory=list)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        command = argv[-1]
        if "# sandbox_chrome allocate" in command or "# sandbox_chrome find allocation" in command:
            return ExecResult(stdout=f"{ALLOCATED}\n", stderr="", exit_code=0)
        if "--task" in argv or "# sandbox_chrome clean up" in command:
            return ExecResult(stdout="", stderr="", exit_code=0)
        if command.startswith("tail"):
            return ExecResult(stdout=f"{self.log_tail}\n", stderr="", exit_code=0)
        if argv[-1].startswith("wc -c"):
            return ExecResult(stdout=f"{self.sized}\n", stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="timed out", exit_code=124, timed_out_after_s=timeout_s)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("dial must not be reached when the bring-up expires")


@dataclass
class FailingCarrier:
    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        return ExecResult(
            stdout="", stderr="the browser exited 1 without serving\nAbort trap", exit_code=1
        )

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("dial must not be reached when the browser fails to come up")


def _session(carrier: object) -> SandboxSession:
    handle = SandboxHandle(conversation_id=uuid4(), container_id="sbx123", turn_id=uuid4())
    return SandboxSession(carrier=carrier, handle=handle)  # type: ignore[arg-type]


def _test_stack() -> ext.BrowserStack:
    return ext._stack("0" * 32, 0, BROWSER_DIR)


async def test_lease_builds_the_wss_endpoint_with_the_traffic_header() -> None:
    carrier = FakeCarrier()
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    endpoint = await lease.endpoint()
    assert endpoint.url == "wss://9223-sbx123.e2b.app/devtools/browser/9f1c-abc-123"
    assert endpoint.headers == {"e2b-traffic-access-token": "tok-xyz"}
    assert carrier.dial_ports == [ext.STACK_PORT_START + ext.CDP_PROXY_PORT_OFFSET]
    assert ext._stack_from_token(await lease.token()) == lease.stack
    await lease.aclose()
    assert carrier.task_detaches == [True]
    bridge_argument = f"--proxy-server=http://127.0.0.1:{_test_stack().egress_bridge_port}"
    assert bridge_argument in carrier.commands[3]
    assert ext.EGRESS_BRIDGE_PROGRAM in carrier.commands[2]
    assert "# sandbox_chrome allocate" in carrier.commands[0]
    assert "# sandbox_chrome find allocation" in carrier.commands[1]
    assert "# sandbox_chrome clean up" in carrier.commands[4]


async def test_a_lost_allocation_result_is_recovered_by_its_durable_owner() -> None:
    carrier = FakeCarrier(allocation_result_lost=True)
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))

    assert isinstance(lease, ext.SandboxChromeCdpLease)
    assert lease.stack.slot == 0
    assert "# sandbox_chrome find allocation" in carrier.commands[1]


async def test_a_malformed_allocation_is_abandoned_by_lease_identity() -> None:
    carrier = FakeCarrier(found="not-a-slot")
    with pytest.raises(RuntimeError, match="recoverable browser stack"):
        await ext.SandboxChromeCdpProvider().lease(_session(carrier))

    assert "# sandbox_chrome abandon allocation" in carrier.commands[2]


async def test_a_half_built_deterministic_allocation_is_abandoned_and_retried_once() -> None:
    carrier = FakeCarrier(allocation_failures_remaining=1, find_failures_remaining=1)

    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))

    assert isinstance(lease, ext.SandboxChromeCdpLease)
    assert sum("# sandbox_chrome allocate" in command for command in carrier.commands) == 2
    assert sum("# sandbox_chrome find allocation" in command for command in carrier.commands) == 2
    assert (
        sum("# sandbox_chrome abandon allocation" in command for command in carrier.commands) == 1
    )


async def test_a_half_built_allocation_retry_is_bounded() -> None:
    carrier = FakeCarrier(allocation_failures_remaining=2, find_failures_remaining=2)

    with pytest.raises(RuntimeError, match="recoverable browser stack"):
        await ext.SandboxChromeCdpProvider().lease(_session(carrier))

    assert sum("# sandbox_chrome allocate" in command for command in carrier.commands) == 2
    assert (
        sum("# sandbox_chrome abandon allocation" in command for command in carrier.commands) == 2
    )


async def test_a_durable_token_reattaches_and_refreshes_only_its_bridge() -> None:
    carrier = FakeCarrier()
    session = _session(carrier)
    provider = ext.SandboxChromeCdpProvider()
    original = await provider.lease(session)
    assert isinstance(original, ext.SandboxChromeCdpLease)

    recovered = await provider.reattach(await original.token(), session)

    assert isinstance(recovered, ext.SandboxChromeCdpLease)
    assert recovered.stack == original.stack
    assert carrier.dial_ports == [original.stack.cdp_proxy_port] * 2
    assert any("# sandbox_chrome stop bridge" in command for command in carrier.commands)
    assert any("# sandbox_chrome resolve" in command for command in carrier.commands)


async def test_a_stale_token_cannot_release_a_slot_a_new_lease_owns() -> None:
    carrier = FakeCarrier()
    session = _session(carrier)
    provider = ext.SandboxChromeCdpProvider()
    original = await provider.lease(session)
    assert isinstance(original, ext.SandboxChromeCdpLease)
    carrier.found = json.dumps({"slot": 1, "dir": BROWSER_DIR})

    with pytest.raises(ext.SessionGone):
        await provider.reattach(await original.token(), session)

    cleanup = next(
        command for command in reversed(carrier.commands) if "# sandbox_chrome clean up" in command
    )
    assert "owns_slot = lock.is_symlink() and os.readlink(lock) == LEASE_ID" in cleanup
    assert "if owns_slot:\n    end_recorded" in cleanup
    assert "if marker not in process_command(pid):" in cleanup


async def test_a_failed_reattach_reports_a_stack_it_could_not_release() -> None:
    carrier = FakeCarrier()
    session = _session(carrier)
    provider = ext.SandboxChromeCdpProvider()
    original = await provider.lease(session)
    carrier.found = json.dumps({"slot": 1, "dir": BROWSER_DIR})
    carrier.cleanup_fails = True

    with pytest.raises(ext.SessionGone) as failure:
        await provider.reattach(await original.token(), session)

    assert failure.value.__notes__ == [
        "the browser stack was not released: sandbox_chrome failed to stop the browser stack: "
        "cleanup unavailable"
    ]


async def test_a_token_cannot_reattach_under_another_turns_authority() -> None:
    carrier = FakeCarrier()
    provider = ext.SandboxChromeCdpProvider()
    original = await provider.lease(_session(carrier))
    command_count = len(carrier.commands)

    with pytest.raises(ext.SessionGone):
        await provider.reattach(await original.token(), _session(carrier))

    assert len(carrier.commands) == command_count + 1
    assert "# sandbox_chrome clean up" in carrier.commands[-1]


async def test_a_token_naming_no_dir_reattaches_its_stack_at_the_fixed_root() -> None:
    carrier = FakeCarrier(found=json.dumps({"slot": 0, "dir": "/tmp/ufo-browser"}))
    session = _session(carrier)
    assert session.turn_id is not None
    lease_id = session.turn_id.hex
    token = json.dumps({"version": ext.TOKEN_VERSION, "lease_id": lease_id, "slot": 0})

    recovered = await ext.SandboxChromeCdpProvider().reattach(token, session)

    assert isinstance(recovered, ext.SandboxChromeCdpLease)
    assert recovered.stack.root == f"/tmp/ufo-browser/leases/{lease_id}"


async def test_a_token_naming_no_dir_releases_its_stack_when_the_root_moved() -> None:
    carrier = FakeCarrier()
    session = _session(carrier)
    assert session.turn_id is not None
    lease_id = session.turn_id.hex
    token = json.dumps({"version": ext.TOKEN_VERSION, "lease_id": lease_id, "slot": 0})

    with pytest.raises(ext.SessionGone):
        await ext.SandboxChromeCdpProvider().reattach(token, session)

    assert "# sandbox_chrome clean up" in carrier.commands[-1]
    assert f"ROOT = '/tmp/ufo-browser/leases/{lease_id}'" in carrier.commands[-1]


async def test_a_prior_turn_token_survives_until_its_cleanup_succeeds() -> None:
    carrier = FakeCarrier()
    provider = ext.SandboxChromeCdpProvider()
    original = await provider.lease(_session(carrier))
    carrier.cleanup_fails = True

    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        await provider.reattach(await original.token(), _session(carrier))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_egress_bridge_authenticates_and_tunnels_plain_http(tmp_path: Path) -> None:
    connect_heads: list[bytes] = []
    origin_heads: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connect_heads.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        origin_heads.append(await reader.readuntil(b"\r\n\r\n"))
        body = b"Example Domain"
        writer.write(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    bridge_port = _free_port()
    source = f'LISTEN_HOST = "127.0.0.1"\nLISTEN_PORT = {bridge_port}\n' + ext.EGRESS_BRIDGE_PROGRAM
    script = tmp_path / "bridge.py"
    script.write_text(source)

    for token in ("first-token", "rotated-token"):
        env = dict(os.environ)
        env["HTTPS_PROXY"] = f"http://{token}:ufo@127.0.0.1:{upstream_port}"
        process = await asyncio.create_subprocess_exec(sys.executable, script, env=env)
        try:
            for _attempt in range(100):
                try:
                    _probe_reader, probe_writer = await asyncio.open_connection(
                        "127.0.0.1", bridge_port
                    )
                except OSError:
                    await asyncio.sleep(0.01)
                    continue
                probe_writer.close()
                await probe_writer.wait_closed()
                break
            else:
                raise AssertionError("egress bridge did not start")

            reader, writer = await asyncio.open_connection("127.0.0.1", bridge_port)
            writer.write(
                b"GET http://example.com/title HTTP/1.1\r\n"
                b"Host: example.com\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            await writer.wait_closed()
            assert response.endswith(b"Example Domain")
        finally:
            process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
    server.close()
    await server.wait_closed()

    expected_auth = [
        base64.b64encode(f"{token}:ufo".encode()) for token in ("first-token", "rotated-token")
    ]
    assert [head.split(b"\r\n", 1)[0] for head in connect_heads] == [
        b"CONNECT example.com:80 HTTP/1.1",
        b"CONNECT example.com:80 HTTP/1.1",
    ]
    assert [
        next(
            line.split(b": ", 1)[1]
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"proxy-authorization:")
        )
        for head in connect_heads
    ] == [b"Basic " + item for item in expected_auth]
    assert [head.split(b"\r\n", 1)[0] for head in origin_heads] == [
        b"GET /title HTTP/1.1",
        b"GET /title HTTP/1.1",
    ]
    assert all(b"Connection: close\r\n" in head for head in origin_heads)


async def test_plain_http_cannot_reuse_one_authoritys_tunnel_for_another(
    tmp_path: Path,
) -> None:
    """A browser may keep its proxy socket open, but the authenticated upstream CONNECT is scoped
    to one authority."""
    connect_heads: list[bytes] = []
    origin_heads: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connect = await reader.readuntil(b"\r\n\r\n")
        connect_heads.append(connect)
        authority = connect.split()[1]
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        origin_heads.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
            + str(len(authority)).encode()
            + b"\r\n\r\n"
            + authority
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    bridge_port = _free_port()
    source = f'LISTEN_HOST = "127.0.0.1"\nLISTEN_PORT = {bridge_port}\n' + ext.EGRESS_BRIDGE_PROGRAM
    script = tmp_path / "bridge-authorities.py"
    script.write_text(source)
    env = dict(os.environ)
    env["HTTPS_PROXY"] = f"http://run-token:ufo@127.0.0.1:{upstream_port}"
    process = await asyncio.create_subprocess_exec(sys.executable, script, env=env)
    try:
        for _attempt in range(100):
            try:
                _probe_reader, probe_writer = await asyncio.open_connection(
                    "127.0.0.1", bridge_port
                )
            except OSError:
                await asyncio.sleep(0.01)
                continue
            probe_writer.close()
            await probe_writer.wait_closed()
            break
        else:
            raise AssertionError("egress bridge did not start")

        reader, writer = await asyncio.open_connection("127.0.0.1", bridge_port)
        writer.write(
            b"GET http://host-a.test/a HTTP/1.1\r\nHost: host-a.test\r\n"
            b"Connection: keep-alive\r\nProxy-Connection: keep-alive\r\n\r\n"
        )
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(), 5)).endswith(b"host-a.test:80")
        assert reader.at_eof()
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", bridge_port)
        writer.write(
            b"GET http://host-b.test/b HTTP/1.1\r\nHost: host-b.test\r\n"
            b"Connection: keep-alive\r\n\r\n"
        )
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(), 5)).endswith(b"host-b.test:80")
        writer.close()
        await writer.wait_closed()
    finally:
        process.terminate()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        server.close()
        await server.wait_closed()

    assert [head.split()[1] for head in connect_heads] == [b"host-a.test:80", b"host-b.test:80"]
    assert [head.split(b"\r\n", 1)[0] for head in origin_heads] == [
        b"GET /a HTTP/1.1",
        b"GET /b HTTP/1.1",
    ]
    assert all(b"Proxy-Connection:" not in head for head in origin_heads)


async def test_the_carrier_deadline_leaves_the_command_room_to_report_its_own_failure() -> None:
    carrier = FakeCarrier()
    await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert carrier.timeouts == [
        ext.STACK_ALLOCATE_TIMEOUT_SECONDS,
        ext.STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS + ext.COMMAND_REPORT_MARGIN_SECONDS,
        ext.STACK_TASK_START_TIMEOUT_SECONDS,
        ext.BROWSER_START_TIMEOUT_SECONDS,
    ]
    assert ext.IN_SANDBOX_WAIT_SECONDS < ext.BROWSER_START_TIMEOUT_SECONDS
    assert (
        ext.CHROME_READY_BUDGET_SECONDS + 2 * ext.PROXY_READY_BUDGET_SECONDS
        == ext.IN_SANDBOX_WAIT_SECONDS
    )


async def test_lease_without_a_sandbox_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="needs the turn's sandbox"):
        await ext.SandboxChromeCdpProvider().lease(None)


async def test_lease_omits_the_header_when_the_dial_target_carries_none() -> None:
    carrier = FakeCarrier(dial_target=DialTarget(host=FAKE_HOST, tls=True))
    lease = await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert (await lease.endpoint()).headers == {}


async def test_a_bring_up_the_carrier_kills_reports_the_browser_log_not_its_own_timeout() -> None:
    carrier = ExpiringCarrier()
    with pytest.raises(RuntimeError) as failure:
        await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert "error while loading shared libraries" in str(failure.value)
    assert f"after {ext.BROWSER_START_TIMEOUT_SECONDS}s" in str(failure.value)
    assert carrier.timeouts == [
        ext.STACK_ALLOCATE_TIMEOUT_SECONDS,
        ext.STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS + ext.COMMAND_REPORT_MARGIN_SECONDS,
        ext.STACK_TASK_START_TIMEOUT_SECONDS,
        ext.BROWSER_START_TIMEOUT_SECONDS,
        ext.LOG_TAIL_BUDGET_SECONDS,
        ext.STACK_CLEANUP_TIMEOUT_SECONDS,
    ]


async def test_place_file_answers_the_workspace_path_without_reading_it() -> None:
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
        ext.CdpEndpoint(url="wss://sandbox.test/devtools"),
        _session(FailingCarrier()),
        _test_stack(),
    )
    with pytest.raises(RuntimeError, match="could not read download"):
        await lease.fetch_download("guid-9")


async def test_a_download_read_that_expires_reports_the_budget_not_an_unreadable_file() -> None:
    """A killed read hands back an empty stdout and the carrier's own message, which is indexed
    here as a download the sandbox could not read."""
    lease = ext.SandboxChromeCdpLease(
        ext.CdpEndpoint(url="wss://sandbox.test/devtools"),
        _session(ExpiringCarrier()),
        _test_stack(),
    )
    with pytest.raises(RuntimeError, match=f"after {ext.DOWNLOAD_READ_BUDGET_SECONDS}s"):
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


async def test_a_failed_bring_up_reports_a_stack_it_could_not_release() -> None:
    carrier = FakeCarrier(bring_up_fails=True, cleanup_fails=True)
    with pytest.raises(RuntimeError, match="the browser exited 1") as failure:
        await ext.SandboxChromeCdpProvider().lease(_session(carrier))
    assert failure.value.__notes__ == [
        "the browser stack was not released: sandbox_chrome failed to stop the browser stack: "
        "cleanup unavailable"
    ]


EVERY_BROWSER = ext.BROWSER_COMMANDS["linux"]


async def _browser_lookup(
    tmp_path: Path, platform: str, installed: tuple[str, ...]
) -> tuple[int | None, str, str]:
    for name in installed:
        shim = tmp_path / name
        shim.write_text("#!/bin/sh\n")
        shim.chmod(0o755)
    source = "\n".join(
        (
            "import sys",
            f"sys.platform = {platform!r}",
            ext._assignments(
                {"BROWSER_COMMANDS": ext.BROWSER_COMMANDS, "MISSING_BROWSER": ext.MISSING_BROWSER}
            ),
            ext.BROWSER_LOOKUP_PROGRAM,
            "print(browser)",
        )
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        source,
        env={"PATH": str(tmp_path)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode().strip(), stderr.decode().strip()


@pytest.mark.parametrize(
    ("platform", "installed", "resolved"),
    [
        pytest.param("darwin", EVERY_BROWSER, ext.HEADLESS_SHELL_COMMAND, id="mac"),
        pytest.param("linux", EVERY_BROWSER, ext.HEADLESS_SHELL_COMMAND, id="linux"),
        pytest.param("linux", ("google-chrome", "chromium"), "chromium", id="linux-image"),
    ],
)
async def test_the_browser_lookup_prefers_the_headless_shell(
    tmp_path: Path, platform: str, installed: tuple[str, ...], resolved: str
) -> None:
    assert await _browser_lookup(tmp_path, platform, installed) == (0, str(tmp_path / resolved), "")


async def test_macos_without_the_headless_shell_fails_with_the_pinned_install(
    tmp_path: Path,
) -> None:
    installed = EVERY_BROWSER[1:]
    assert await _browser_lookup(tmp_path, "darwin", installed) == (1, "", ext.MISSING_BROWSER)
    assert (
        f"`npx playwright@{PLAYWRIGHT_VERSION} install chromium-headless-shell`"
        in ext.MISSING_BROWSER
    )
