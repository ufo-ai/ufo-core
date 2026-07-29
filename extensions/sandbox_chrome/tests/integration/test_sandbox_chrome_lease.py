"""The sandbox_chrome lease end to end: the real launch, proxy and resolve commands run through a
real carrier under the turn's real egress environment, against a real Chrome, and the endpoint the
lease yields answers a real CDP command.

The local carrier runs each command as a host subprocess carrying the turn's egress environment —
`HTTP(S)_PROXY` pointing at the egress proxy — which is the condition the in-sandbox readiness
probes actually run under and the one no fake carrier reproduces. The proxy refuses a destination
that is not globally routable, so a probe that reaches Chrome only by way of the proxy cannot
succeed; this is the live proof the whole chain (launch, the Host-rewriting proxy carrying the
WebSocket upgrade, the resolved DevTools path, a CDP round trip) works from inside. Missing Chrome
infrastructure fails the required integration gate and skips an optional local run."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from signal import SIGKILL
from time import monotonic, sleep
from uuid import uuid4

import pytest
import ufo_ext_sandbox_chrome as ext
import websockets
from ufo_testsupport.plugin import integration_dependency_available

from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)

CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
PID_FILES = (Path("/tmp/ufo-browser.pid"), Path("/tmp/ufo-browser-proxy.pid"))
CDP_ROUND_TRIP_TIMEOUT_SECONDS = 20
PORT_RELEASE_TIMEOUT_SECONDS = 10
POLL_SLEEP_SECONDS = 0.1
WEDGED_EXIT_TIMEOUT_SECONDS = 5
"""The bring-up has already ended the wedged process by the time the lease returns, so this bounds a
regression into a failure instead of a wait on a process that is never killed."""
UNREACHABLE_PROXY_PORT = 9999
"""No egress proxy listens here, so any in-sandbox request that routes to the proxy instead of
straight to loopback fails outright — which is what makes this a proof and not a coincidence."""


def _chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate) if "/" not in candidate else candidate
        if found and Path(found).exists():
            return found
    integration_dependency_available(False, "Chrome/Chromium binary is not available")
    return None


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


DEVTOOLS_PORTS_FREE = integration_dependency_available(
    all(_port_free(port) for port in (ext.BROWSER_CDP_PORT, ext.BROWSER_CDP_PROXY_PORT)),
    "DevTools ports are already held",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.serial,
    pytest.mark.skipif(_chrome() is None, reason="no Chrome/Chromium binary for a live lease"),
    pytest.mark.skipif(
        not DEVTOOLS_PORTS_FREE,
        reason="the DevTools ports are already held on this machine",
    ),
]


@dataclass(frozen=True)
class LoopbackHostCarrier(LocalCarrier):
    """The local carrier plus the one thing it deliberately lacks — an external per-port host — so
    the endpoint the lease builds is dialable on this machine. Command execution, the egress
    environment and the workspace stay the local carrier's own."""

    async def host(self, handle: SandboxHandle, port: int) -> str:
        return f"127.0.0.1:{port}"


@pytest.fixture
def browser_processes() -> Iterator[None]:
    """End the Chrome and proxy a lease starts: they persist with the sandbox by design, so the
    lease's own `aclose` is a no-op and nothing else would reclaim them on this machine. The ports
    are waited free, so the next test's bring-up cannot probe a browser that is still dying."""
    yield
    for pidfile in PID_FILES:
        if not pidfile.exists():
            continue
        with contextlib.suppress(ValueError, OSError):
            os.kill(int(pidfile.read_text().strip()), SIGKILL)
        pidfile.unlink(missing_ok=True)
    deadline = monotonic() + PORT_RELEASE_TIMEOUT_SECONDS
    while monotonic() < deadline and not all(
        _port_free(port) for port in (ext.BROWSER_CDP_PORT, ext.BROWSER_CDP_PROXY_PORT)
    ):
        sleep(POLL_SLEEP_SECONDS)


async def _turn_sandbox(tmp_path: Path, browser: str) -> SandboxSession:
    """A turn's session over the local carrier — real subprocesses, the real egress environment, a
    per-port host so the endpoint the lease builds is dialable here — with `browser` installed as
    the `chromium` on PATH the launch finds. A wrapper script, never a symlink: a macOS Chrome
    resolves its own framework relative to the path it was invoked through."""
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    wrapper = shim / "chromium"
    wrapper.write_text(browser)
    wrapper.chmod(0o755)
    carrier = LoopbackHostCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            proxy=ProxyEndpoint(port=UNREACHABLE_PROXY_PORT, ca_cert="CA-PEM-BYTES"),
            run_token="run-token-abc",
            env={"PATH": f"{shim}:{os.environ['PATH']}"},
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


def _record_pid(pid_path: str, pid: int) -> None:
    Path(pid_path).write_text(str(pid))


def _real_chrome() -> str:
    binary = _chrome()
    assert binary is not None
    return f'#!/bin/sh\nexec "{binary}" "$@"\n'


async def test_lease_yields_an_endpoint_a_real_cdp_connection_drives(
    tmp_path: Path, browser_processes: None
) -> None:
    session = await _turn_sandbox(tmp_path, _real_chrome())

    lease = await ext.SandboxChromeCdpProvider().lease(session)
    endpoint = await lease.endpoint()

    assert endpoint.url.startswith(f"ws://127.0.0.1:{ext.BROWSER_CDP_PROXY_PORT}/devtools/browser/")
    async with websockets.connect(
        endpoint.url,
        additional_headers=endpoint.headers,
        open_timeout=CDP_ROUND_TRIP_TIMEOUT_SECONDS,
    ) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion", "params": {}}))
        reply = json.loads(await ws.recv())

    assert "Chrome" in reply["result"]["product"]


async def test_a_real_download_is_read_back_out_of_the_sandbox(
    tmp_path: Path, browser_processes: None
) -> None:
    """The other half of the transport's file duty, against the real chain: Chrome writes a download
    inside the sandbox, and the lease is what carries those bytes back out. A serve-side temp
    directory could not — the file exists only where the browser ran."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    lease = await ext.SandboxChromeCdpProvider().lease(session)

    body = b"%PDF-1.7 downloaded through the sandbox"
    guid = f"probe-{uuid4()}"
    written = await session.bash(
        f"printf '%s' {json.dumps(body.decode())} > {ext.DOWNLOAD_DIR}/{guid}"
    )
    assert written.exit_code == 0, written.stderr

    assert await lease.fetch_download(guid) == body
    assert await lease.download_dir() == ext.DOWNLOAD_DIR


async def test_the_bring_up_creates_the_download_directory(
    tmp_path: Path, browser_processes: None
) -> None:
    """Chrome cannot write a download into a directory that does not exist, so the bring-up makes
    it — proven against the real filesystem the browser actually writes to."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    await ext.SandboxChromeCdpProvider().lease(session)
    listed = await session.bash(f"test -d {ext.DOWNLOAD_DIR} && echo present")
    assert listed.stdout.strip() == "present", listed.stderr


async def test_a_second_lease_reuses_the_running_browser(
    tmp_path: Path, browser_processes: None
) -> None:
    """Chrome and its proxy persist with the sandbox, so the next turn's lease resolves the same
    DevTools target instead of launching a second browser."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    provider = ext.SandboxChromeCdpProvider()

    first = await (await provider.lease(session)).endpoint()
    second = await (await provider.lease(session)).endpoint()

    assert first.url == second.url


@pytest.mark.parametrize("pid_path", [ext.CHROME_PID_PATH, ext.PROXY_PID_PATH])
async def test_a_recorded_process_that_is_not_serving_is_replaced(
    tmp_path: Path, browser_processes: None, pid_path: str
) -> None:
    """Readiness is the port answering, not the pid existing. Whichever stage a failed start left
    recorded — the browser or its proxy — is ended and relaunched, so one bad start cannot poison
    every later lease of this sandbox, the way the reported outage failed ten straight attempts."""
    session = await _turn_sandbox(tmp_path, _real_chrome())
    wedged = await asyncio.create_subprocess_exec("sleep", "300")
    _record_pid(pid_path, wedged.pid)

    endpoint = await (await ext.SandboxChromeCdpProvider().lease(session)).endpoint()

    assert endpoint.url.startswith(f"ws://127.0.0.1:{ext.BROWSER_CDP_PROXY_PORT}/devtools/browser/")
    assert await asyncio.wait_for(wedged.wait(), WEDGED_EXIT_TIMEOUT_SECONDS) != 0


async def test_a_browser_that_cannot_run_reports_its_own_log_at_once(
    tmp_path: Path, browser_processes: None
) -> None:
    """A browser that exits is diagnosed the moment it does — with its log, inside the carrier's
    deadline — never by spending the readiness budget and losing the reason to a killed command."""
    session = await _turn_sandbox(
        tmp_path, '#!/bin/sh\necho "cannot create the profile lock" >&2\nexit 1\n'
    )

    with pytest.raises(RuntimeError, match="cannot create the profile lock") as failure:
        await ext.SandboxChromeCdpProvider().lease(session)

    assert "the browser exited 1" in str(failure.value)
