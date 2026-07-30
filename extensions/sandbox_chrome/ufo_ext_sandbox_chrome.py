"""The sandbox_chrome cdp provider: Chrome driven inside each conversation's own sandbox.

A hosted deploy runs one headless Chrome per conversation, inside that conversation's sandbox, and
the serve process reaches its DevTools endpoint over the carrier's public per-port host. Selected by
`[browser] cdp_provider = "sandbox_chrome"`, this provider's per-turn `lease` receives the turn's
`SandboxSession` and runs one bring-up command against it: launch Chrome on port 9222 if nothing
answers there, launch an in-sandbox TCP proxy on 9223 that rewrites each request's `Host:` header to
`127.0.0.1:9222` (Chrome's DevTools rejects a non-localhost Host, and `--remote-allow-origins=*`
does not fix that — the proxy is load-bearing: it carries the WebSocket upgrade and frames through),
and read Chrome's `webSocketDebuggerUrl` back *through* that proxy. A lease therefore returns only
once the whole in-sandbox chain answers, and `lease` builds its endpoint from the carrier's own dial
of port 9223 — address, scheme and the headers that port needs to answer are the carrier's to state,
so nothing here guesses a scheme or names a provider's traffic header.

Readiness is a port that answers, never a pid that exists: a browser recorded but not serving is
ended and relaunched, so one bad start cannot poison every later lease of the same sandbox. A
browser that exits during bring-up reports its own log at once instead of spending the wait, and the
wait itself stays strictly inside the carrier's command deadline, so a failure surfaces the
browser's log rather than the carrier's timeout.

Chrome and the proxy persist across turns with the per-conversation sandbox, so a lease's `aclose`
is a no-op and a fresh lease reuses the running Chrome. The BUA engine (the browser extension)
connects whatever endpoint the lease yields — only the transport is this provider's concern, never
the engine."""

import asyncio
import base64
import shlex
from dataclasses import dataclass

from ufo.sdk.browser import CdpEndpoint, CdpLease, FileBytes, SessionGone
from ufo.sdk.manifest import CdpProviderSpec, Manifest
from ufo.sdk.sandbox import SandboxSession

NAME = "sandbox_chrome"
VERSION = "0.1.0"
CDP_BACKEND = "sandbox_chrome"
BROWSER_CDP_PORT = 9222
BROWSER_CDP_PROXY_PORT = 9223
CHROME_READY_BUDGET_SECONDS = 45
"""The wait for a cold chromium to bind its DevTools port."""
PROXY_READY_BUDGET_SECONDS = 10
PROCESS_END_BUDGET_SECONDS = 5
"""Each stage waits on its own budget, so a slow chromium cannot spend the proxy's and leave it
reporting a budget it never had — one shared clock across stages starves whichever runs last."""
IN_SANDBOX_WAIT_SECONDS = (
    CHROME_READY_BUDGET_SECONDS + PROXY_READY_BUDGET_SECONDS + 2 * PROCESS_END_BUDGET_SECONDS
)
COMMAND_REPORT_MARGIN_SECONDS = 15
BROWSER_START_TIMEOUT_SECONDS = IN_SANDBOX_WAIT_SECONDS + COMMAND_REPORT_MARGIN_SECONDS
"""The carrier's deadline for the bring-up command, held above every wait it can enclose: a command
the carrier kills reports only its own timeout, losing the browser log that says what failed."""
BROWSER_PROBE_TIMEOUT_SECONDS = 0.2
BROWSER_POLL_SLEEP_SECONDS = 0.1
BROWSER_DIR = "/tmp/ufo-browser"
CHROME_LOG_PATH = f"{BROWSER_DIR}/chromium.log"
CHROME_PROFILE_DIR = f"{BROWSER_DIR}/profile"
DOWNLOAD_DIR = f"{BROWSER_DIR}/downloads"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
CHROME_PID_PATH = "/tmp/ufo-browser.pid"
PROXY_LOG_PATH = f"{BROWSER_DIR}/proxy.log"
PROXY_PID_PATH = "/tmp/ufo-browser-proxy.pid"
PROXY_SCRIPT_PATH = "/tmp/ufo-browser-proxy.py"
LOG_TAIL_LINES = 20

PROXY_SOURCE = (
    f"""CHROME_HOST = "127.0.0.1"
CHROME_PORT = {BROWSER_CDP_PORT}
PROXY_HOST = "0.0.0.0"
PROXY_PORT = {BROWSER_CDP_PROXY_PORT}
"""
    + """
import asyncio
import contextlib

BUFFER_BYTES = 65536


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(BUFFER_BYTES):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            CHROME_HOST,
            CHROME_PORT,
        )
        request = await client_reader.readuntil(b"\\r\\n\\r\\n")
        lines = request.decode("iso-8859-1").split("\\r\\n")
        rewritten = [
            f"Host: {CHROME_HOST}:{CHROME_PORT}"
            if line.lower().startswith("host:")
            else line
            for line in lines
        ]
        upstream_writer.write("\\r\\n".join(rewritten).encode("iso-8859-1"))
        await upstream_writer.drain()
        await asyncio.gather(
            pipe(client_reader, upstream_writer),
            pipe(upstream_reader, client_writer),
        )
    except Exception:
        client_writer.close()
        with contextlib.suppress(Exception):
            await client_writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(handle, PROXY_HOST, PROXY_PORT)
    async with server:
        await server.serve_forever()


asyncio.run(main())
"""
)

BRING_UP_SOURCE = (
    f"""BROWSER_DIR = "{BROWSER_DIR}"
DOWNLOAD_DIR = "{DOWNLOAD_DIR}"
CHROME_URL = "http://127.0.0.1:{BROWSER_CDP_PORT}/json/version"
CHROME_ARGV_TAIL = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--remote-debugging-address=0.0.0.0",
    "--remote-debugging-port={BROWSER_CDP_PORT}",
    "--remote-allow-origins=*",
    "--user-data-dir={CHROME_PROFILE_DIR}",
    "about:blank",
]
CHROME_LOG = "{CHROME_LOG_PATH}"
CHROME_PID = "{CHROME_PID_PATH}"
PROXIED_URL = "http://127.0.0.1:{BROWSER_CDP_PROXY_PORT}/json/version"
PROXY_ARGV = ["python3", "{PROXY_SCRIPT_PATH}"]
PROXY_LOG = "{PROXY_LOG_PATH}"
PROXY_PID = "{PROXY_PID_PATH}"
PROBE_TIMEOUT_SECONDS = {BROWSER_PROBE_TIMEOUT_SECONDS}
POLL_SLEEP_SECONDS = {BROWSER_POLL_SLEEP_SECONDS}
CHROME_READY_BUDGET_SECONDS = {CHROME_READY_BUDGET_SECONDS}
PROXY_READY_BUDGET_SECONDS = {PROXY_READY_BUDGET_SECONDS}
PROCESS_END_BUDGET_SECONDS = {PROCESS_END_BUDGET_SECONDS}
LOG_TAIL_LINES = {LOG_TAIL_LINES}
"""
    + '''
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from shutil import which

BROWSER_COMMANDS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def answer(url):
    """The DevTools version document, or None while nothing is serving that url yet."""
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as reply:
            return json.load(reply)
    except Exception:
        return None


def tail(log_path):
    try:
        lines = Path(log_path).read_text(errors="replace").splitlines()
    except OSError:
        return f"{log_path} holds no log"
    return "\\n".join(lines[-LOG_TAIL_LINES:])


def end_recorded(pid_path):
    """End what a prior bring-up recorded. It is not serving, so it is wedged, and leaving it alive
    holds the port and the profile lock a fresh launch needs. Each launch is its own session leader,
    so the recorded pid names the whole tree — a browser's renderers hold that lock too, and killing
    the leader alone would leave them. Then wait for the pid to go, or the port stays taken."""
    try:
        pid = int(Path(pid_path).read_text())
    except (OSError, ValueError):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return
    deadline = time.monotonic() + PROCESS_END_BUDGET_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(POLL_SLEEP_SECONDS)


def launch(argv, log_path, pid_path):
    log = open(log_path, "wb", buffering=0)
    child = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    Path(pid_path).write_text(str(child.pid))
    return child


def serving(url, argv, log_path, pid_path, budget, what):
    """The version document `url` answers, launching `argv` first when nothing does. The budget is
    this stage's own, so the wait it reports is the wait it had. A child that exits is reported the
    moment it does, so a browser that cannot run never spends the budget at all."""
    served = answer(url)
    if served is not None:
        return served
    end_recorded(pid_path)
    child = launch(argv, log_path, pid_path)
    deadline = time.monotonic() + budget
    while True:
        served = answer(url)
        if served is not None:
            return served
        if child.poll() is not None:
            raise SystemExit(
                f"{what} exited {child.returncode} without serving {url}\\n{tail(log_path)}"
            )
        if time.monotonic() >= deadline:
            raise SystemExit(f"{what} did not serve {url} within {budget}s\\n{tail(log_path)}")
        time.sleep(POLL_SLEEP_SECONDS)


Path(BROWSER_DIR).mkdir(parents=True, exist_ok=True)
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
browser = next((found for found in map(which, BROWSER_COMMANDS) if found), None)
if browser is None:
    raise SystemExit("Chromium is required in the sandbox image")
serving(
    CHROME_URL,
    [browser] + CHROME_ARGV_TAIL,
    CHROME_LOG,
    CHROME_PID,
    CHROME_READY_BUDGET_SECONDS,
    "the browser",
)
proxied = serving(
    PROXIED_URL,
    PROXY_ARGV,
    PROXY_LOG,
    PROXY_PID,
    PROXY_READY_BUDGET_SECONDS,
    "the browser proxy",
)
print(proxied["webSocketDebuggerUrl"])
'''
)

BROWSER_UP_COMMAND = f"""
cat >{PROXY_SCRIPT_PATH} <<'PROXY'
{PROXY_SOURCE}
PROXY
python3 - <<'BRINGUP'
{BRING_UP_SOURCE}
BRINGUP
""".strip()


@dataclass(frozen=True)
class SandboxChromeCdpLease:
    """The per-turn lease over the sandbox's Chrome: `endpoint` returns the resolved wss endpoint,
    `token` the durable reattach handle (that endpoint's url), `aclose` a no-op because Chrome and
    its proxy persist inside the per-conversation sandbox and outlive the turn. Files move by path,
    not by wire: this Chrome opens the workspace directly, and a download it takes lands in the
    sandbox — so the lease holds the sandbox to read those bytes back out of it."""

    endpoint_: CdpEndpoint
    sandbox: SandboxSession

    async def endpoint(self) -> CdpEndpoint:
        return self.endpoint_

    async def token(self) -> str:
        return self.endpoint_.url

    async def place_file(self, path: str, read: FileBytes) -> str:
        """This Chrome runs inside the turn's own sandbox and opens the workspace directly, so the
        path it can reach is the path the caller already holds. `read` goes unawaited: nothing is
        copied out of the sandbox and back to hand a local browser a file it can already see."""
        return path

    async def download_dir(self) -> str:
        """A path inside the sandbox: this Chrome runs there, so that is the only filesystem it can
        write to and the only one the bytes can be read back from."""
        return DOWNLOAD_DIR

    async def fetch_download(self, guid: str) -> bytes:
        """The download's bytes, read out of the sandbox Chrome wrote them to. `allowAndName` stores
        a completed download under its guid, so that is the file to read. The encoding reads stdin
        rather than passing a width flag: the flag is GNU-only, and the local carrier runs this on
        whatever host the deploy sits on. It stands alone in the command so the exit code is its own
        — a pipeline reports its last stage, which would mask an unreadable file as an empty
        download. Decoding drops the line wrapping, and runs in a thread: a whole file's worth of it
        is CPU work every other turn on this one loop would wait through. The file is sized first —
        it crosses whole into this process, and what a page downloads is not ours to trust."""
        stored = f"{DOWNLOAD_DIR}/{shlex.quote(guid)}"
        sized = await self.sandbox.bash(f"wc -c < {stored}")
        if sized.exit_code != 0:
            raise RuntimeError(f"sandbox_chrome could not read download {guid}: {sized.stderr}")
        if int(sized.stdout.strip()) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"download {guid} is {sized.stdout.strip()} bytes; this browser returns at most "
                f"{MAX_DOWNLOAD_BYTES}"
            )
        result = await self.sandbox.bash(
            f"base64 < {stored}",
            timeout_s=BROWSER_START_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"sandbox_chrome could not read download {guid}: {result.stderr}")
        return await asyncio.to_thread(base64.b64decode, result.stdout)

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class SandboxChromeCdpProvider:
    """Core's `cdp_providers` seam backed by Chrome inside the turn's sandbox. `lease` runs the
    idempotent bring-up against the sandbox — which yields the DevTools websocket path read through
    the in-sandbox proxy, so the whole chain is proven live before the endpoint is handed back — and
    builds the wss endpoint over the sandbox's public per-port host. `reattach` reports the session
    gone so a recovered turn re-grounds through a fresh `lease` — the endpoint resolves only from
    the live sandbox, which the caller supplies at lease time, never from the token alone."""

    async def lease(self, sandbox: SandboxSession | None = None) -> CdpLease:
        if sandbox is None:
            raise RuntimeError(
                "the sandbox_chrome cdp provider needs the turn's sandbox to reach its Chrome"
            )
        result = await sandbox.bash(BROWSER_UP_COMMAND, timeout_s=BROWSER_START_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            raise RuntimeError(
                f"sandbox_chrome failed to bring up the browser: {result.stderr or result.stdout}"
            )
        target = await sandbox.dial(BROWSER_CDP_PROXY_PORT)
        scheme = "wss" if target.tls else "ws"
        endpoint = CdpEndpoint(
            url=f"{scheme}://{target.host}{_ws_path(result.stdout)}",
            headers=dict(target.headers),
        )
        return SandboxChromeCdpLease(endpoint, sandbox)

    async def reattach(self, token: str) -> CdpLease:
        raise SessionGone(token)


def _ws_path(url: str) -> str:
    stripped = url.strip()
    for prefix in (f"ws://127.0.0.1:{BROWSER_CDP_PORT}", f"ws://localhost:{BROWSER_CDP_PORT}"):
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix)
    raise RuntimeError(f"browser websocket url must be local: {stripped!r}")


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        cdp_providers=(
            CdpProviderSpec(
                backend=CDP_BACKEND, build=lambda credentials: SandboxChromeCdpProvider()
            ),
        ),
    )
