"""The sandbox-chrome cdp provider: Chrome driven inside each conversation's own sandbox.

A hosted deploy runs one headless Chrome per conversation, inside that conversation's sandbox, and
the serve process reaches its DevTools endpoint over the carrier's public per-port host. Selected by
`[browser] cdp_provider = "sandbox-chrome"`, this provider's per-turn `lease` receives the turn's
`SandboxSession` and, against it: launches Chrome idempotently on port 9222 (pidfile-guarded,
backgrounded so the exec returns while it keeps running), starts an in-sandbox TCP proxy on 9223
that rewrites each request's `Host:` header to `127.0.0.1:9222` (Chrome's DevTools rejects a
non-localhost Host, and `--remote-allow-origins=*` does not fix that — the proxy is load-bearing:
it carries the WebSocket upgrade and frames through), reads Chrome's `webSocketDebuggerUrl` path,
and builds a
`wss://<public-host-of-9223><ws-path>` endpoint carrying the sandbox's traffic token as a connection
header. Chrome and the proxy persist across turns with the per-conversation sandbox, so a lease's
`aclose` is a no-op and a fresh lease reuses the running Chrome. The BUA engine (the browser
extension) connects whatever endpoint the lease yields — only the transport is this provider's
concern, never the engine."""

from dataclasses import dataclass

from ufo.sdk.browser import CdpEndpoint, CdpLease, SessionGone
from ufo.sdk.manifest import CdpProviderSpec, Manifest
from ufo.sdk.sandbox import SandboxSession

NAME = "sandbox_chrome"
VERSION = "0.1.0"
CDP_BACKEND = "sandbox-chrome"
BROWSER_CDP_PORT = 9222
BROWSER_CDP_PROXY_PORT = 9223
BROWSER_START_TIMEOUT_SECONDS = 20
BROWSER_READY_ATTEMPTS = 150
BROWSER_READY_SLEEP_SECONDS = 0.1
TRAFFIC_ACCESS_HEADER = "e2b-traffic-access-token"

CHROME_START_COMMAND = f"""
if [ -f /tmp/ufo-browser.pid ] && kill -0 "$(cat /tmp/ufo-browser.pid)" 2>/dev/null; then
  exit 0
fi
browser="$(command -v chromium || command -v chromium-browser \
  || command -v google-chrome || command -v google-chrome-stable)"
if [ -z "$browser" ]; then
  echo "Chromium is required in the sandbox image" >&2
  exit 127
fi
mkdir -p /tmp/ufo-browser
nohup "$browser" --headless=new --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --remote-debugging-address=0.0.0.0 --remote-debugging-port={BROWSER_CDP_PORT} \
  --remote-allow-origins='*' --user-data-dir=/tmp/ufo-browser/profile about:blank \
  >/tmp/ufo-browser/chromium.log 2>&1 &
echo "$!" >/tmp/ufo-browser.pid
for i in $(seq 1 {BROWSER_READY_ATTEMPTS}); do
  python3 - <<'PY' && exit 0 || sleep {BROWSER_READY_SLEEP_SECONDS}
import sys
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:{BROWSER_CDP_PORT}/json/version", timeout=0.2).read()
except Exception:
    sys.exit(1)
PY
done
cat /tmp/ufo-browser/chromium.log >&2
exit 1
""".strip()

WS_PATH_COMMAND = f"""
python3 - <<'PY'
import json
import sys
import time
import urllib.request

last_error = ""
for _ in range({BROWSER_READY_ATTEMPTS}):
    try:
        version = json.load(
            urllib.request.urlopen(
                "http://127.0.0.1:{BROWSER_CDP_PORT}/json/version",
                timeout=0.2,
            )
        )
    except Exception as error:
        last_error = str(error)
        time.sleep({BROWSER_READY_SLEEP_SECONDS})
        continue
    print(version["webSocketDebuggerUrl"])
    sys.exit(0)
raise SystemExit(f"failed to resolve browser websocket: {{last_error}}")
PY
""".strip()

PROXY_START_COMMAND = f"""
cat >/tmp/ufo-browser-proxy.py <<'PY'
import asyncio
import contextlib

CHROME_HOST = "127.0.0.1"
CHROME_PORT = {BROWSER_CDP_PORT}
PROXY_HOST = "0.0.0.0"
PROXY_PORT = {BROWSER_CDP_PROXY_PORT}
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
            f"Host: {{CHROME_HOST}}:{{CHROME_PORT}}"
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
PY
if [ -f /tmp/ufo-browser-proxy.pid ] \
  && kill -0 "$(cat /tmp/ufo-browser-proxy.pid)" 2>/dev/null; then
  python3 - <<'PY' && exit 0 || kill "$(cat /tmp/ufo-browser-proxy.pid)" 2>/dev/null || true
import socket
import sys

try:
    sock = socket.create_connection(("127.0.0.1", {BROWSER_CDP_PROXY_PORT}), timeout=0.2)
    sock.close()
except OSError:
    sys.exit(1)
PY
fi
nohup python3 /tmp/ufo-browser-proxy.py >/tmp/ufo-browser/proxy.log 2>&1 &
echo "$!" >/tmp/ufo-browser-proxy.pid
for i in $(seq 1 {BROWSER_READY_ATTEMPTS}); do
  python3 - <<'PY' && exit 0 || sleep {BROWSER_READY_SLEEP_SECONDS}
import socket
import sys

try:
    sock = socket.create_connection(("127.0.0.1", {BROWSER_CDP_PROXY_PORT}), timeout=0.2)
    sock.close()
except OSError:
    sys.exit(1)
PY
done
cat /tmp/ufo-browser/proxy.log >&2
exit 1
""".strip()


@dataclass(frozen=True)
class SandboxChromeCdpLease:
    """The per-turn lease over the sandbox's Chrome: `endpoint` returns the resolved wss endpoint,
    `token` the durable reattach handle (that endpoint's url), `aclose` a no-op because Chrome and
    its proxy persist inside the per-conversation sandbox and outlive the turn."""

    endpoint_: CdpEndpoint

    async def endpoint(self) -> CdpEndpoint:
        return self.endpoint_

    async def token(self) -> str:
        return self.endpoint_.url

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class SandboxChromeCdpProvider:
    """Core's `cdp_providers` seam backed by Chrome inside the turn's sandbox. `lease` runs the
    idempotent launch/proxy scripts against the sandbox, reads the DevTools websocket path, and
    builds the wss endpoint over the sandbox's public per-port host. `reattach` reports the session
    gone so a recovered turn re-grounds through a fresh `lease` — the endpoint resolves only from
    the live sandbox, which the caller supplies at lease time, never from the token alone."""

    async def lease(self, sandbox: SandboxSession | None = None) -> CdpLease:
        if sandbox is None:
            raise RuntimeError(
                "the sandbox-chrome cdp provider needs the turn's sandbox to reach its Chrome"
            )
        await _run(sandbox, CHROME_START_COMMAND, "start browser")
        await _run(sandbox, PROXY_START_COMMAND, "start browser proxy")
        ws_path = _ws_path(await _run(sandbox, WS_PATH_COMMAND, "resolve browser websocket"))
        host = await sandbox.host(BROWSER_CDP_PROXY_PORT)
        headers = {TRAFFIC_ACCESS_HEADER: sandbox.traffic_token} if sandbox.traffic_token else {}
        endpoint = CdpEndpoint(url=_remote_ws_url(host, ws_path), headers=headers)
        return SandboxChromeCdpLease(endpoint)

    async def reattach(self, token: str) -> CdpLease:
        raise SessionGone(token)


async def _run(sandbox: SandboxSession, command: str, what: str) -> str:
    result = await sandbox.bash(command, timeout_s=BROWSER_START_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        raise RuntimeError(f"sandbox-chrome failed to {what}: {result.stderr or result.stdout}")
    return result.stdout


def _ws_path(url: str) -> str:
    stripped = url.strip()
    for prefix in (f"ws://127.0.0.1:{BROWSER_CDP_PORT}", f"ws://localhost:{BROWSER_CDP_PORT}"):
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix)
    raise RuntimeError(f"browser websocket url must be local: {stripped!r}")


def _remote_ws_url(host: str, path: str) -> str:
    base = _remote_url(host).rstrip("/")
    if base.startswith("https://"):
        return f"wss://{base.removeprefix('https://')}{path}"
    if base.startswith("http://"):
        return f"ws://{base.removeprefix('http://')}{path}"
    raise ValueError(f"remote host must resolve to http or https: {host!r}")


def _remote_url(host: str) -> str:
    if host.startswith(("http://", "https://")):
        return host
    scheme = "http" if host.startswith(("localhost:", "127.0.0.1:")) else "https"
    return f"{scheme}://{host}"


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
