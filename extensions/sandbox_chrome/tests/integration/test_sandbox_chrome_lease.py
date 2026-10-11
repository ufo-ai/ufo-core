"""The sandbox_chrome lease end to end: the real launch, proxy and resolve commands run through a
real carrier under a real session environment, against a real Chrome, and the endpoint the lease
yields answers a real CDP command.

The local carrier runs each command as a host subprocess carrying the env its spec rides — on a
proxied open, `HTTP(S)_PROXY` pointing at the proxy — which is the condition the in-sandbox
readiness probes actually run under and the one no fake carrier reproduces. The proxy refuses a
destination that is not globally routable, so a probe that reaches Chrome only by way of the proxy
cannot succeed; this is the live proof the whole chain (launch, the Host-rewriting proxy carrying
the WebSocket upgrade, the resolved DevTools path, a CDP round trip) works from inside. An
unproxied open runs the same chain with no bridge. Missing Chrome infrastructure fails the required
integration gate and skips an optional local run."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_sandbox_chrome as ext
import websockets
from ufo_testsupport.browser import MISSING_BROWSER_REASON, sandboxed_chrome
from ufo_testsupport.plugin import integration_dependency_available

from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    NO_PROXY_HOSTS,
    DialTarget,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)

CDP_ROUND_TRIP_TIMEOUT_SECONDS = 20
NAVIGATION_TIMEOUT_SECONDS = 20
POLL_SLEEP_SECONDS = 0.1
UNREACHABLE_PROXY_PORT = 9999
"""No egress proxy listens here, so any in-sandbox request that routes to the proxy instead of
straight to loopback fails outright — which is what makes this a proof and not a coincidence."""


CHROME = sandboxed_chrome()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.serial,
    pytest.mark.skipif(
        not integration_dependency_available(CHROME is not None, MISSING_BROWSER_REASON),
        reason=MISSING_BROWSER_REASON,
    ),
]


@dataclass(frozen=True)
class LoopbackHostCarrier(LocalCarrier):
    """The local carrier plus the one thing it deliberately lacks — an external per-port host —
    so the endpoint the lease builds is dialable on this machine."""

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        return DialTarget(host=f"127.0.0.1:{port}", tls=False)


async def _turn_sandbox(
    tmp_path: Path,
    browser: str | None,
    upstream_port: int | None = UNREACHABLE_PROXY_PORT,
    session_token: str = "session-token-abc",
    turn_id: UUID | None = None,
) -> SandboxSession:
    env: dict[str, str] = {}
    if upstream_port is not None:
        proxy = f"http://{session_token}:ufo@127.0.0.1:{upstream_port}"
        env = {
            **dict.fromkeys(("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"), proxy),
            **dict.fromkeys(("NO_PROXY", "no_proxy"), NO_PROXY_HOSTS),
        }
    if browser is not None:
        shim = tmp_path / "bin"
        shim.mkdir(exist_ok=True)
        wrapper = shim / ext.HEADLESS_SHELL_COMMAND
        wrapper.write_text(browser)
        wrapper.chmod(0o755)
        env["PATH"] = f"{shim}:{os.environ['PATH']}"
    carrier = LoopbackHostCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            turn_id=turn_id or uuid4(),
            env=env,
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


def _real_chrome() -> str:
    assert CHROME is not None
    return f'#!/bin/sh\nexec "{CHROME}" "$@"\n'


async def _port_closed(port: int) -> bool:
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return True
    writer.close()
    await writer.wait_closed()
    return False


async def _navigate_text(lease: ext.SandboxChromeCdpLease, url: str) -> str | None:
    endpoint = await lease.endpoint()
    async with websockets.connect(
        endpoint.url,
        additional_headers=endpoint.headers,
        open_timeout=CDP_ROUND_TRIP_TIMEOUT_SECONDS,
    ) as ws:
        command_id = 0

        async def call(
            method: str,
            params: dict[str, object] | None = None,
            session_id: str | None = None,
        ) -> dict[str, object]:
            nonlocal command_id
            command_id += 1
            command: dict[str, object] = {
                "id": command_id,
                "method": method,
                "params": params or {},
            }
            if session_id is not None:
                command["sessionId"] = session_id
            await ws.send(json.dumps(command))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), NAVIGATION_TIMEOUT_SECONDS))
                if reply.get("id") != command_id:
                    continue
                assert "error" not in reply, reply
                result = reply["result"]
                assert isinstance(result, dict)
                return result

        created = await call("Target.createTarget", {"url": "about:blank"})
        attached = await call(
            "Target.attachToTarget",
            {"targetId": created["targetId"], "flatten": True},
        )
        page_session = attached["sessionId"]
        assert isinstance(page_session, str)
        navigated = await call("Page.navigate", {"url": url}, page_session)
        assert not navigated.get("errorText"), navigated

        deadline = asyncio.get_running_loop().time() + NAVIGATION_TIMEOUT_SECONDS
        while True:
            evaluated = await call(
                "Runtime.evaluate",
                {"expression": "document.body && document.body.innerText"},
                page_session,
            )
            remote_object = evaluated["result"]
            assert isinstance(remote_object, dict)
            value = remote_object.get("value")
            if isinstance(value, str):
                return value
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(POLL_SLEEP_SECONDS)


async def test_lease_yields_an_endpoint_a_real_cdp_connection_drives(
    tmp_path: Path,
) -> None:
    session = await _turn_sandbox(tmp_path, _real_chrome())

    lease = await ext.SandboxChromeCdpProvider().lease(session)
    assert isinstance(lease, ext.SandboxChromeCdpLease)
    try:
        endpoint = await lease.endpoint()
        assert endpoint.url.startswith(
            f"ws://127.0.0.1:{lease.stack.cdp_proxy_port}/devtools/browser/"
        )
        async with websockets.connect(
            endpoint.url,
            additional_headers=endpoint.headers,
            open_timeout=CDP_ROUND_TRIP_TIMEOUT_SECONDS,
        ) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion", "params": {}}))
            reply = json.loads(await ws.recv())
        assert "Chrome" in reply["result"]["product"]
    finally:
        await lease.aclose()
    cleaned = await session.bash(
        f"test ! -e {lease.stack.root} && test ! -e {lease.stack.lock_path}"
    )
    assert cleaned.exit_code == 0, cleaned.stderr
    assert all(
        [
            await _port_closed(lease.stack.chrome_port),
            await _port_closed(lease.stack.cdp_proxy_port),
            await _port_closed(lease.stack.egress_bridge_port),
        ]
    )


@pytest.mark.skipif(
    CHROME is None or Path(CHROME).name != ext.HEADLESS_SHELL_COMMAND,
    reason=f"the pinned sandboxed browser here is not {ext.HEADLESS_SHELL_COMMAND}",
)
async def test_the_headless_shell_runs_from_its_own_directory_on_serves_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert CHROME is not None
    monkeypatch.setenv("PATH", f"{Path(CHROME).parent}{os.pathsep}{os.environ['PATH']}")
    session = await _turn_sandbox(tmp_path, None)

    lease = await ext.SandboxChromeCdpProvider().lease(session)
    assert isinstance(lease, ext.SandboxChromeCdpLease)
    try:
        assert await _navigate_text(lease, "data:text/html,<p>drawn</p>") == "drawn"
    finally:
        await lease.aclose()


async def test_a_pre_ready_crash_is_abandoned_and_relaunched(
    tmp_path: Path,
) -> None:
    """The deterministic allocator may die after claiming a slot but before publishing `ready`."""
    turn_id = uuid4()
    session = await _turn_sandbox(tmp_path, _real_chrome(), turn_id=turn_id)
    allocated = await session.bash(ext._allocate_command(turn_id.hex))
    assert allocated.exit_code == 0, allocated.stderr
    stack = ext._allocated_stack(turn_id.hex, allocated.stdout)
    interrupted = await session.bash(f"rm -f {stack.root}/ready")
    assert interrupted.exit_code == 0, interrupted.stderr

    lease = await ext.SandboxChromeCdpProvider().lease(session)
    assert isinstance(lease, ext.SandboxChromeCdpLease)
    try:
        endpoint = await lease.endpoint()
        async with websockets.connect(
            endpoint.url,
            additional_headers=endpoint.headers,
            open_timeout=CDP_ROUND_TRIP_TIMEOUT_SECONDS,
        ) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion", "params": {}}))
            reply = json.loads(await ws.recv())
        assert "Chrome" in reply["result"]["product"]
    finally:
        await lease.aclose()


async def test_cleanup_does_not_signal_reused_recorded_pids(
    tmp_path: Path,
) -> None:
    """A long-lived sandbox can reuse the PID of a Chrome or supervisor that exited first."""
    turn_id = uuid4()
    session = await _turn_sandbox(tmp_path, _real_chrome(), turn_id=turn_id)
    allocated = await session.bash(ext._allocate_command(turn_id.hex))
    assert allocated.exit_code == 0, allocated.stderr
    stack = ext._allocated_stack(turn_id.hex, allocated.stdout)
    unrelated = await asyncio.create_subprocess_exec("sleep", "300")
    try:
        recorded = await session.bash(f"printf '%s' {unrelated.pid} > {stack.task_base}.pid")
        assert recorded.exit_code == 0, recorded.stderr
        bridge_stopped = await session.bash(ext._bridge_down_command(stack))
        assert bridge_stopped.exit_code == 0, bridge_stopped.stderr
        await asyncio.sleep(POLL_SLEEP_SECONDS)
        assert unrelated.returncode is None

        recorded = await session.bash(
            f"printf '%s' {unrelated.pid} > {stack.task_base}.pid\n"
            f"printf '%s' {unrelated.pid} > {stack.chrome_pid}\n"
            f"printf '%s' {unrelated.pid} > {stack.proxy_pid}"
        )
        assert recorded.exit_code == 0, recorded.stderr
        cleaned = await session.bash(ext._stack_down_command(stack))
        assert cleaned.exit_code == 0, cleaned.stderr
        await asyncio.sleep(POLL_SLEEP_SECONDS)
        assert unrelated.returncode is None
    finally:
        with contextlib.suppress(ProcessLookupError):
            unrelated.terminate()
        await unrelated.wait()


async def test_real_chrome_navigates_through_the_authenticated_egress_bridge(
    tmp_path: Path,
) -> None:
    connect_heads: list[bytes] = []
    origin_heads: list[bytes] = []
    body = b"<html><body>nightly bridge navigation</body></html>"

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            connect_heads.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            origin_heads.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    lease = None
    try:
        session = await _turn_sandbox(tmp_path, _real_chrome(), upstream_port)
        lease = await ext.SandboxChromeCdpProvider().lease(session)
        assert isinstance(lease, ext.SandboxChromeCdpLease)
        assert (
            await _navigate_text(lease, "http://example.test/nightly-probe")
            == "nightly bridge navigation"
        )
    finally:
        if lease is not None:
            await lease.aclose()
        server.close()
        await server.wait_closed()

    matching_connects = [
        head for head in connect_heads if head.startswith(b"CONNECT example.test:80 HTTP/1.1")
    ]
    assert matching_connects
    expected_auth = base64.b64encode(b"session-token-abc:ufo")
    assert b"Proxy-Authorization: Basic " + expected_auth in matching_connects[0]
    assert any(head.startswith(b"GET /nightly-probe HTTP/1.1") for head in origin_heads)


async def test_an_unproxied_sandbox_runs_no_bridge_and_navigates_directly(
    tmp_path: Path,
) -> None:
    origin_heads: list[bytes] = []
    body = b"<html><body>direct navigation</body></html>"

    async def origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            origin_heads.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(origin, "127.0.0.1", 0)
    origin_port = server.sockets[0].getsockname()[1]
    lease = None
    try:
        session = await _turn_sandbox(tmp_path, _real_chrome(), upstream_port=None)
        lease = await ext.SandboxChromeCdpProvider().lease(session)
        assert isinstance(lease, ext.SandboxChromeCdpLease)
        assert await _port_closed(lease.stack.egress_bridge_port)
        assert (
            await _navigate_text(lease, f"http://127.0.0.1:{origin_port}/direct")
            == "direct navigation"
        )
    finally:
        if lease is not None:
            await lease.aclose()
        server.close()
        await server.wait_closed()

    assert any(head.startswith(b"GET /direct HTTP/1.1") for head in origin_heads)


async def test_a_real_download_is_read_back_out_of_the_sandbox(
    tmp_path: Path,
) -> None:
    """The other half of the transport's file duty, against the real chain: Chrome writes a
    download inside the sandbox, and the lease is what carries those bytes back out."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    lease = await ext.SandboxChromeCdpProvider().lease(session)
    assert isinstance(lease, ext.SandboxChromeCdpLease)

    try:
        body = b"%PDF-1.7 downloaded through the sandbox"
        guid = f"probe-{uuid4()}"
        download_dir = await lease.download_dir()
        written = await session.bash(
            f"printf '%s' {json.dumps(body.decode())} > {download_dir}/{guid}"
        )
        assert written.exit_code == 0, written.stderr

        assert await lease.fetch_download(guid) == body
        assert await lease.download_dir() == lease.stack.download_dir
    finally:
        await lease.aclose()


async def test_the_bring_up_creates_the_download_directory(
    tmp_path: Path,
) -> None:
    """Chrome cannot write a download into a directory that does not exist, so the bring-up makes
    it — proven against the real filesystem the browser actually writes to."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    lease = await ext.SandboxChromeCdpProvider().lease(session)
    assert isinstance(lease, ext.SandboxChromeCdpLease)
    try:
        listed = await session.bash(f"test -d {lease.stack.download_dir} && echo present")
        assert listed.stdout.strip() == "present", listed.stderr
    finally:
        await lease.aclose()


async def test_pre_token_crash_recovery_finds_the_stack_and_refreshes_its_proxy_authorization(
    tmp_path: Path,
) -> None:
    """A hard worker crash leaves Chrome alive in the resumed conversation sandbox."""
    old_connects: list[bytes] = []
    recovered_connects: list[bytes] = []

    def handler(connects: list[bytes], text: bytes):
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                connects.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")
                body = b"<html><body>" + text + b"</body></html>"
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        return upstream

    old_server = await asyncio.start_server(handler(old_connects, b"before crash"), "127.0.0.1", 0)
    recovered_server = await asyncio.start_server(
        handler(recovered_connects, b"after recovery"), "127.0.0.1", 0
    )
    original = None
    recovered = None
    turn_id = uuid4()
    try:
        original_session = await _turn_sandbox(
            tmp_path,
            _real_chrome(),
            old_server.sockets[0].getsockname()[1],
            "old-session-token",
            turn_id,
        )
        provider = ext.SandboxChromeCdpProvider()
        original = await provider.lease(original_session)
        assert isinstance(original, ext.SandboxChromeCdpLease)
        assert await _navigate_text(original, "http://before-crash.test/page") == "before crash"
        recovered_session = await _turn_sandbox(
            tmp_path,
            _real_chrome(),
            recovered_server.sockets[0].getsockname()[1],
            "recovered-session-token",
            turn_id,
        )
        recovered = await provider.lease(recovered_session)
        assert isinstance(recovered, ext.SandboxChromeCdpLease)
        assert recovered.stack == original.stack
        assert (
            await _navigate_text(recovered, "http://after-recovery.test/page") == "after recovery"
        )
    finally:
        owner = recovered or original
        if owner is not None:
            await owner.aclose()
        old_server.close()
        recovered_server.close()
        await asyncio.gather(old_server.wait_closed(), recovered_server.wait_closed())

    old_auth = b"Proxy-Authorization: Basic " + base64.b64encode(b"old-session-token:ufo")
    recovered_auth = b"Proxy-Authorization: Basic " + base64.b64encode(
        b"recovered-session-token:ufo"
    )
    assert any(
        head.startswith(b"CONNECT before-crash.test:80 ") and old_auth in head
        for head in old_connects
    )
    assert any(
        head.startswith(b"CONNECT after-recovery.test:80 ") and recovered_auth in head
        for head in recovered_connects
    )
    assert all(recovered_auth not in head for head in old_connects)
    assert all(old_auth not in head for head in recovered_connects)


async def test_closing_one_concurrent_lease_does_not_stop_its_sibling(
    tmp_path: Path,
) -> None:
    """Sibling subagent turns can overlap in one sandbox; each lease has distinct ports, profile,
    token-bearing bridge and cleanup targets, and closing either one cannot end the other."""
    first_connects: list[bytes] = []
    second_connects: list[bytes] = []

    def handler(connects: list[bytes], body: bytes):
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                connects.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/html\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                await writer.drain()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        return upstream

    first_body = b"<html><body>first lease</body></html>"
    second_body = b"<html><body>second lease</body></html>"
    first_server = await asyncio.start_server(handler(first_connects, first_body), "127.0.0.1", 0)
    second_server = await asyncio.start_server(
        handler(second_connects, second_body), "127.0.0.1", 0
    )
    first_upstream_port = first_server.sockets[0].getsockname()[1]
    second_upstream_port = second_server.sockets[0].getsockname()[1]
    first = None
    second = None
    try:
        first_session = await _turn_sandbox(
            tmp_path, _real_chrome(), first_upstream_port, "first-session-token"
        )
        second_session = await _turn_sandbox(
            tmp_path, _real_chrome(), second_upstream_port, "second-session-token"
        )
        provider = ext.SandboxChromeCdpProvider()
        first, second = await asyncio.gather(
            provider.lease(first_session), provider.lease(second_session)
        )
        assert isinstance(first, ext.SandboxChromeCdpLease)
        assert isinstance(second, ext.SandboxChromeCdpLease)
        assert first.stack.slot != second.stack.slot
        assert (await first.endpoint()).url != (await second.endpoint()).url
        first_text, second_text = await asyncio.gather(
            _navigate_text(first, "http://first-lease.test/one"),
            _navigate_text(second, "http://second-lease.test/two"),
        )
        assert first_text == "first lease"
        assert second_text == "second lease"

        await second.aclose()
        assert await _navigate_text(first, "http://first-lease.test/after-close") == "first lease"
        isolated = await first_session.bash(
            f"test -d {first.stack.root} && test ! -e {second.stack.root}"
        )
        assert isolated.exit_code == 0, isolated.stderr
    finally:
        leases = [lease for lease in (first, second) if lease is not None]
        await asyncio.gather(*(lease.aclose() for lease in leases))
        first_server.close()
        second_server.close()
        await asyncio.gather(first_server.wait_closed(), second_server.wait_closed())

    first_auth = b"Proxy-Authorization: Basic " + base64.b64encode(b"first-session-token:ufo")
    second_auth = b"Proxy-Authorization: Basic " + base64.b64encode(b"second-session-token:ufo")
    first_host_connects = [
        head for head in first_connects if head.startswith(b"CONNECT first-lease.test:80 ")
    ]
    second_host_connects = [
        head for head in second_connects if head.startswith(b"CONNECT second-lease.test:80 ")
    ]
    assert len(first_host_connects) >= 2
    assert second_host_connects
    assert all(first_auth in head for head in first_host_connects)
    assert all(second_auth in head for head in second_host_connects)
    assert all(second_auth not in head for head in first_connects)
    assert all(first_auth not in head for head in second_connects)


async def test_a_browser_that_cannot_run_reports_its_own_log_at_once(
    tmp_path: Path,
) -> None:
    """A browser that exits is diagnosed the moment it does — with its log, inside the carrier's
    deadline — never by spending the readiness budget and losing the reason to a killed command."""
    turn_id = uuid4()
    session = await _turn_sandbox(
        tmp_path,
        '#!/bin/sh\necho "cannot create the profile lock" >&2\nexit 1\n',
        turn_id=turn_id,
    )

    with pytest.raises(RuntimeError, match="cannot create the profile lock") as failure:
        await ext.SandboxChromeCdpProvider().lease(session)

    assert "the browser exited 1" in str(failure.value)
    stacks = await session.bash(
        'ls -l "${TMPDIR:-/tmp}/ufo-browser/slots" "${TMPDIR:-/tmp}/ufo-browser/leases"'
    )
    assert stacks.exit_code == 0, stacks.stderr
    assert turn_id.hex not in stacks.stdout
    supervisors = await asyncio.create_subprocess_exec(
        "pgrep", "-f", turn_id.hex, stdout=asyncio.subprocess.PIPE
    )
    assert (await supervisors.communicate())[0] == b""
