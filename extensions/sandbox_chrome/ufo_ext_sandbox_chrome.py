"""The sandbox_chrome cdp provider: Chrome driven inside each turn's conversation sandbox.

A hosted turn owns one isolated stack inside the sandbox: an authenticated egress bridge, a
headless Chrome, its profile and downloads, and a Host-rewriting DevTools proxy. Its durable turn
id names the stack before any sandbox side effect, so a worker crash during launch is recoverable.
Sibling turns can share one conversation container, so no port, process journal, browser profile,
credential, or cleanup target is sandbox-wide. An atomic slot directory assigns three ports to
each lease and the lease releases only that slot and stack when it closes.

Chrome's DevTools rejects a non-localhost `Host` header, so the CDP proxy is load-bearing: it
rewrites the HTTP upgrade and then carries WebSocket frames unchanged. `lease` reads the browser's
resolved WebSocket path through that proxy before dialing the carrier's public host for the same
per-lease port. Address, TLS and traffic headers remain the carrier's facts.

A browser that exits during bring-up reports its log at once instead of spending the readiness
budget. The enclosed waits end strictly before the carrier command deadline; if the carrier still
times out, the provider reads the per-lease logs back from the sandbox. The BUA engine connects the
endpoint the lease yields — only the transport is this provider's concern. Chrome runs
`--no-sandbox` on every carrier: its own sandbox cannot initialize nested inside the local
carrier's Seatbelt profile. A remote carrier's container holds the browser; the local carrier
confines only its writes, so a compromised renderer there reads what the member can and reaches
the network.

Chromium does not authenticate from credentials embedded in the standard proxy environment, while
the egress proxy intentionally accepts only authenticated CONNECT requests. A loopback bridge
therefore converts Chromium's HTTP proxy requests into CONNECT tunnels and adds the current turn's
proxy authorization. Its detached `ufo run` supervisor stays alive with the lease, which keeps the
turn's TLS loopback proxy and authorization alive. Plain HTTP is one request per bridge connection:
the origin receives `Connection: close`, and a later request for another host must open its own
authenticated tunnel instead of entering the first host's tunnel."""

import asyncio
import base64
import json
import shlex
from dataclasses import dataclass

from ufo.sdk.browser import CdpEndpoint, CdpLease, FileBytes, SessionGone
from ufo.sdk.manifest import CdpProviderSpec, Manifest
from ufo.sdk.sandbox import PLAYWRIGHT_VERSION, ExecResult, Sandbox

NAME = "sandbox_chrome"
VERSION = "0.1.0"
CDP_BACKEND = "sandbox_chrome"
TOKEN_VERSION = 1
STACK_PORT_START = 10000
STACK_PORT_SLOTS = 7000
STACK_PORTS_PER_SLOT = 3
CHROME_PORT_OFFSET = 0
CDP_PROXY_PORT_OFFSET = 1
EGRESS_BRIDGE_PORT_OFFSET = 2
CHROME_READY_BUDGET_SECONDS = 45
"""The wait for a cold chromium to bind its DevTools port."""
PROXY_READY_BUDGET_SECONDS = 10
PROCESS_END_BUDGET_SECONDS = 5
"""Each stage waits on its own budget, so a slow chromium cannot spend the proxy's and leave it
reporting a budget it never had — one shared clock across stages starves whichever runs last."""
IN_SANDBOX_WAIT_SECONDS = CHROME_READY_BUDGET_SECONDS + 2 * PROXY_READY_BUDGET_SECONDS
COMMAND_REPORT_MARGIN_SECONDS = 15
BROWSER_START_TIMEOUT_SECONDS = IN_SANDBOX_WAIT_SECONDS + COMMAND_REPORT_MARGIN_SECONDS
"""The carrier's deadline for the bring-up command, held above every wait it can enclose: a command
the carrier kills reports only its own timeout, losing the browser log that says what failed."""
BROWSER_PROBE_TIMEOUT_SECONDS = 0.2
BROWSER_POLL_SLEEP_SECONDS = 0.1
HEADLESS_SHELL_COMMAND = "chrome-headless-shell"
BROWSER_COMMANDS = {
    "darwin": (HEADLESS_SHELL_COMMAND,),
    "linux": (
        HEADLESS_SHELL_COMMAND,
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ),
}
"""The sandbox's PATH lookup by its `sys.platform`, first found wins. Under the local carrier's
Seatbelt profile full Chrome aborts with SIGABRT and an empty log, so macOS takes only the shell."""
MISSING_BROWSER = (
    f"no usable browser on PATH; install one with `npx playwright@{PLAYWRIGHT_VERSION} install "
    f"chromium-headless-shell` and put the directory holding its {HEADLESS_SHELL_COMMAND} on PATH, "
    f"or a {HEADLESS_SHELL_COMMAND} script that execs it; a symlink to it aborts on macOS"
)
DEFAULT_TMPDIR = "/tmp"
BROWSER_DIRNAME = "ufo-browser"
"""Created under the sandbox's `$TMPDIR`, the temp root every carrier leaves writable: the local
carrier's kernel sandbox admits writes there, and on macOS `$TMPDIR` is never `/tmp`."""
FIXED_BROWSER_DIR = f"{DEFAULT_TMPDIR}/{BROWSER_DIRNAME}"
"""The stack root of a token that names no `dir`: the release this one replaces writes its tokens
without one, every stack under this root, and a rolling deploy recovers its turns here."""
STACK_LOCK_DIRNAME = "slots"
STACK_ROOT_DIRNAME = "leases"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
STACK_ALLOCATE_TIMEOUT_SECONDS = 15
STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS = 15
STACK_TASK_START_TIMEOUT_SECONDS = 15
STACK_CLEANUP_TIMEOUT_SECONDS = 3 * PROCESS_END_BUDGET_SECONDS + 5
LOG_TAIL_LINES = 20
LOG_TAIL_BUDGET_SECONDS = 15
"""Reading the tail of two files inside the sandbox, on the failure path of a command the carrier
already killed — so the wait is short and stated rather than the 120s default."""
DOWNLOAD_SIZE_BUDGET_SECONDS = 30
DOWNLOAD_READ_BUDGET_SECONDS = 600
"""The two reads of a download carry the budgets their own work needs: `wc -c` answers at once,
while encoding a file up to `MAX_DOWNLOAD_BYTES` hands about 27 MiB back on one command's stdout.
Neither is a browser launch, so neither borrows the bring-up's deadline."""


@dataclass(frozen=True)
class BrowserStack:
    lease_id: str
    slot: int
    browser_dir: str
    chrome_port: int
    cdp_proxy_port: int
    egress_bridge_port: int

    @property
    def root(self) -> str:
        return f"{self.browser_dir}/{STACK_ROOT_DIRNAME}/{self.lease_id}"

    @property
    def chrome_log(self) -> str:
        return f"{self.root}/chromium.log"

    @property
    def chrome_pid(self) -> str:
        return f"{self.root}/chromium.pid"

    @property
    def chrome_profile(self) -> str:
        return f"{self.root}/profile"

    @property
    def download_dir(self) -> str:
        return f"{self.root}/downloads"

    @property
    def proxy_log(self) -> str:
        return f"{self.root}/cdp-proxy.log"

    @property
    def proxy_pid(self) -> str:
        return f"{self.root}/cdp-proxy.pid"

    @property
    def proxy_script(self) -> str:
        return f"{self.root}/cdp-proxy.py"

    @property
    def bridge_script(self) -> str:
        return f"{self.root}/egress-bridge.py"

    @property
    def task_base(self) -> str:
        return f"{self.root}/egress-bridge-task"

    @property
    def lock_path(self) -> str:
        return f"{self.browser_dir}/{STACK_LOCK_DIRNAME}/{self.slot}"


def _stack(lease_id: str, slot: int, browser_dir: str) -> BrowserStack:
    if len(lease_id) != 32 or any(character not in "0123456789abcdef" for character in lease_id):
        raise RuntimeError("sandbox_chrome returned an invalid browser lease id")
    if not 0 <= slot < STACK_PORT_SLOTS:
        raise RuntimeError(f"sandbox_chrome returned invalid browser slot {slot}")
    if not browser_dir.startswith("/") or "\n" in browser_dir:
        raise RuntimeError(f"sandbox_chrome returned an invalid browser directory {browser_dir!r}")
    base = STACK_PORT_START + slot * STACK_PORTS_PER_SLOT
    return BrowserStack(
        lease_id=lease_id,
        slot=slot,
        browser_dir=browser_dir,
        chrome_port=base + CHROME_PORT_OFFSET,
        cdp_proxy_port=base + CDP_PROXY_PORT_OFFSET,
        egress_bridge_port=base + EGRESS_BRIDGE_PORT_OFFSET,
    )


PROXY_PROGRAM = """
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

EGRESS_BRIDGE_PROGRAM = r"""
import asyncio
import base64
import contextlib
import os
import ssl
from urllib.parse import unquote, urlsplit

BUFFER_BYTES = 65536
MAX_HEADER_BYTES = 65536
UPSTREAM_TIMEOUT_SECONDS = 30


async def read_head(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > MAX_HEADER_BYTES:
        raise ValueError("proxy request headers exceed the bridge limit")
    return head


def proxy_config() -> tuple[str, int, ssl.SSLContext | None, str]:
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        raise ValueError("the browser needs the sandbox egress proxy URL")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("the sandbox proxy URL is invalid")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    context = ssl.create_default_context() if parsed.scheme == "https" else None
    return parsed.hostname, port, context, f"Basic {encoded}"


async def open_tunnel(
    authority: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes, bool]:
    host, port, context, authorization = proxy_config()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host,
            port,
            ssl=context,
            server_hostname=host if context is not None else None,
        ),
        timeout=UPSTREAM_TIMEOUT_SECONDS,
    )
    writer.write(
        (
            f"CONNECT {authority} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"Proxy-Authorization: {authorization}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    reply = await read_head(reader)
    status = reply.split(b"\r\n", 1)[0].split()
    accepted = len(status) >= 2 and status[1] == b"200"
    return reader, writer, reply, accepted


def request_target(target: str, headers: list[str]) -> tuple[str, str]:
    parsed = urlsplit(target)
    if parsed.hostname is not None:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        authority = f"{host}:{port}"
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        return authority, path
    host_header = next(
        (line.split(":", 1)[1].strip() for line in headers if line.lower().startswith("host:")),
        "",
    )
    if not host_header:
        raise ValueError("an HTTP proxy request has no absolute target or Host header")
    authority = host_header if ":" in host_header else f"{host_header}:80"
    return authority, target or "/"


def origin_request(method: str, target: str, version: str, headers: list[str]) -> bytes:
    kept = [
        line
        for line in headers
        if not line.lower().startswith(
            ("proxy-authorization:", "proxy-connection:", "connection:")
        )
    ]
    return (
        "\r\n".join((f"{method} {target} {version}", *kept, "Connection: close", "", ""))
    ).encode("iso-8859-1")


def header_value(headers: list[str], name: str) -> str | None:
    prefix = f"{name.lower()}:"
    return next(
        (line.split(":", 1)[1].strip() for line in headers if line.lower().startswith(prefix)),
        None,
    )


async def forward_request_body(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    headers: list[str],
) -> None:
    transfer_encoding = header_value(headers, "transfer-encoding")
    if transfer_encoding and "chunked" in {
        item.strip().lower() for item in transfer_encoding.split(",")
    }:
        while True:
            line = await reader.readuntil(b"\r\n")
            size = int(line.split(b";", 1)[0].strip(), 16)
            writer.write(line)
            if size:
                writer.write(await reader.readexactly(size + 2))
            else:
                while True:
                    trailer = await reader.readuntil(b"\r\n")
                    writer.write(trailer)
                    if trailer == b"\r\n":
                        break
                await writer.drain()
                return
            await writer.drain()
    content_length = header_value(headers, "content-length")
    if content_length is not None:
        remaining = int(content_length)
        while remaining:
            data = await reader.readexactly(min(remaining, BUFFER_BYTES))
            writer.write(data)
            await writer.drain()
            remaining -= len(data)


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(BUFFER_BYTES):
        writer.write(data)
        await writer.drain()


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
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        head = await read_head(client_reader)
        lines = head[:-4].decode("iso-8859-1").split("\r\n")
        request = lines[0].split()
        if len(request) != 3:
            raise ValueError("malformed proxy request line")
        method, target, version = request
        if method.upper() == "CONNECT":
            authority, inner = target, b""
        else:
            authority, path = request_target(target, lines[1:])
            inner = origin_request(method, path, version, lines[1:])
        upstream_reader, upstream_writer, reply, accepted = await open_tunnel(authority)
        if not accepted:
            client_writer.write(reply)
            await client_writer.drain()
            return
        if method.upper() == "CONNECT":
            client_writer.write(reply)
            await client_writer.drain()
        else:
            upstream_writer.write(inner)
            await upstream_writer.drain()
        if method.upper() == "CONNECT":
            await asyncio.gather(
                pipe(client_reader, upstream_writer),
                pipe(upstream_reader, client_writer),
            )
        else:
            await forward_request_body(client_reader, upstream_writer, lines[1:])
            await relay(upstream_reader, client_writer)
    except Exception:
        with contextlib.suppress(Exception):
            client_writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
            )
            await client_writer.drain()
    finally:
        client_writer.close()
        with contextlib.suppress(Exception):
            await client_writer.wait_closed()
        if upstream_writer is not None:
            upstream_writer.close()
            with contextlib.suppress(Exception):
                await upstream_writer.wait_closed()


async def main() -> None:
    proxy_config()
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


asyncio.run(main())
"""


PROCESS_PROGRAM = r"""
import ctypes
import os
import signal
import sys
import time
from pathlib import Path

CTL_KERN = 1
KERN_PROCARGS2 = 49


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def signal_process(pid, sig):
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def process_command(pid):
    if Path("/proc").is_dir():
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="replace"
            )
        except OSError:
            return ""
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return ""
    procargs = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, procargs, ctypes.byref(size), None, 0) != 0:
        return ""
    raw = procargs.raw[: size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    _executable, _, rest = raw[4:].partition(b"\0")
    return b" ".join(rest.lstrip(b"\0").split(b"\0")[:argc]).decode(errors="replace")


def end_recorded(path, marker):
    try:
        pid = int(Path(path).read_text())
    except (OSError, ValueError):
        return
    if marker not in process_command(pid):
        return
    signal_process(pid, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_END_BUDGET_SECONDS
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(POLL_SLEEP_SECONDS)
    if alive(pid):
        signal_process(pid, signal.SIGKILL)


lock = Path(LOCK_PATH)
try:
    owns_slot = lock.is_symlink() and os.readlink(lock) == LEASE_ID
except OSError:
    owns_slot = False
"""
"""What both cleanups share: a recorded pid is signalled only while its command still carries the
lease's marker, so a pid the sandbox reused is left alone. macOS has no `/proc`, and `ps` is setuid,
which the local carrier's Seatbelt profile refuses to exec, so the command line comes from
`KERN_PROCARGS2`."""


STACK_CLEANUP_PROGRAM = r"""
import shutil

if owns_slot:
    end_recorded(f"{TASK_BASE}.pid", TASK_BASE)
    end_recorded(CHROME_PID, ROOT)
    end_recorded(PROXY_PID, ROOT)
for suffix in ("pid", "exit", "lock", "log"):
    try:
        Path(f"{TASK_BASE}.{suffix}").unlink()
    except OSError:
        pass
shutil.rmtree(ROOT, ignore_errors=True)
try:
    if owns_slot:
        lock.unlink()
except OSError:
    pass
"""


BRIDGE_CLEANUP_PROGRAM = r"""
if owns_slot:
    end_recorded(f"{TASK_BASE}.pid", TASK_BASE)
for suffix in ("pid", "exit", "lock", "log"):
    try:
        Path(f"{TASK_BASE}.{suffix}").unlink()
    except OSError:
        pass
"""


BROWSER_LOOKUP_PROGRAM = """
import sys
from shutil import which

browser = next((found for found in map(which, BROWSER_COMMANDS[sys.platform]) if found), None)
if browser is None:
    raise SystemExit(MISSING_BROWSER)
"""


BRING_UP_PROGRAM = '''
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


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


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def await_port(port, exit_path, log_path, budget, what):
    deadline = time.monotonic() + budget
    while True:
        if port_open(port):
            return
        if Path(exit_path).exists():
            raise SystemExit(
                f"{what} exited without serving port {port}\\n{tail(log_path)}"
            )
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"{what} did not serve port {port} within {budget}s\\n{tail(log_path)}"
            )
        time.sleep(POLL_SLEEP_SECONDS)


def serving(url, argv, log_path, pid_path, budget, what):
    """Launch one member of this lease's private stack and wait for its version endpoint."""
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


Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
platform_argv = LINUX_ARGV if sys.platform.startswith("linux") else []
await_port(
    EGRESS_BRIDGE_PORT,
    EGRESS_BRIDGE_TASK_EXIT,
    EGRESS_BRIDGE_TASK_LOG,
    PROXY_READY_BUDGET_SECONDS,
    "the browser egress bridge",
)
serving(
    CHROME_URL,
    [browser] + platform_argv + CHROME_ARGV_TAIL,
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


STACK_DIRS_PROGRAM = f"""
import json
import os
from pathlib import Path

BROWSER_DIR = Path(os.environ.get("TMPDIR") or {DEFAULT_TMPDIR!r}) / {BROWSER_DIRNAME!r}
LOCK_DIR = BROWSER_DIR / {STACK_LOCK_DIRNAME!r}
ROOT_DIR = BROWSER_DIR / {STACK_ROOT_DIRNAME!r}
"""


def _assignments(values: dict[str, object]) -> str:
    return "\n".join(f"{name} = {value!r}" for name, value in values.items())


def _allocate_command(lease_id: str) -> str:
    config = _assignments(
        {
            "LEASE_ID": lease_id,
            "PORT_START": STACK_PORT_START,
            "PORT_SLOTS": STACK_PORT_SLOTS,
            "PORTS_PER_SLOT": STACK_PORTS_PER_SLOT,
        }
    )
    return f"""python3 - <<'ALLOCATE_BROWSER_STACK'
# sandbox_chrome allocate
{config}
{STACK_DIRS_PROGRAM}
import socket

LOCK_DIR.mkdir(parents=True, exist_ok=True)
ROOT_DIR.mkdir(parents=True, exist_ok=True)
root = ROOT_DIR / LEASE_ID
root.mkdir()
for slot in range(PORT_SLOTS):
    lock = LOCK_DIR / str(slot)
    probes = []
    try:
        first_port = PORT_START + slot * PORTS_PER_SLOT
        for port in range(first_port, first_port + PORTS_PER_SLOT):
            probe = socket.socket()
            probe.bind(("127.0.0.1", port))
            probes.append(probe)
        (root / "slot").write_text(str(slot))
        lock.symlink_to(LEASE_ID)
    except FileExistsError:
        continue
    except OSError:
        continue
    finally:
        for probe in probes:
            probe.close()
    (root / "ready").touch()
    print(json.dumps({{"slot": slot, "dir": str(BROWSER_DIR)}}))
    break
else:
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    raise SystemExit("sandbox_chrome has no free browser stack slots")
ALLOCATE_BROWSER_STACK"""


def _find_allocation_command(lease_id: str) -> str:
    config = _assignments(
        {
            "LEASE_ID": lease_id,
            "WAIT_SECONDS": STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS,
            "POLL_SECONDS": BROWSER_POLL_SLEEP_SECONDS,
        }
    )
    return f"""python3 - <<'FIND_BROWSER_STACK'
# sandbox_chrome find allocation
{config}
{STACK_DIRS_PROGRAM}
import time

root = ROOT_DIR / LEASE_ID
deadline = time.monotonic() + WAIT_SECONDS
while True:
    try:
        slot = int((root / "slot").read_text())
        lock = LOCK_DIR / str(slot)
        if (
            (root / "ready").is_file()
            and lock.is_symlink()
            and os.readlink(lock) == LEASE_ID
        ):
            print(json.dumps({{"slot": slot, "dir": str(BROWSER_DIR)}}))
            break
    except (OSError, ValueError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit(f"no browser stack allocation is owned by {{LEASE_ID}}")
    time.sleep(POLL_SECONDS)
FIND_BROWSER_STACK"""


def _abandon_allocation_command(lease_id: str) -> str:
    config = _assignments({"LEASE_ID": lease_id})
    return f"""python3 - <<'ABANDON_BROWSER_STACK'
# sandbox_chrome abandon allocation
{config}
{STACK_DIRS_PROGRAM}
import shutil

if LOCK_DIR.is_dir():
    for lock in LOCK_DIR.iterdir():
        try:
            if lock.is_symlink() and os.readlink(lock) == LEASE_ID:
                lock.unlink()
        except OSError:
            pass
shutil.rmtree(ROOT_DIR / LEASE_ID, ignore_errors=True)
ABANDON_BROWSER_STACK"""


def _egress_bridge_up_command(stack: BrowserStack) -> str:
    config = _assignments({"LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": stack.egress_bridge_port})
    script = shlex.quote(stack.bridge_script)
    return f"""cat >{script} <<'EGRESS_BRIDGE'
{config}
{EGRESS_BRIDGE_PROGRAM}
EGRESS_BRIDGE
exec python3 {script}"""


def _browser_up_command(stack: BrowserStack) -> str:
    proxy_config = _assignments(
        {
            "CHROME_HOST": "127.0.0.1",
            "CHROME_PORT": stack.chrome_port,
            "PROXY_HOST": "0.0.0.0",
            "PROXY_PORT": stack.cdp_proxy_port,
        }
    )
    browser_config = _assignments(
        {
            "BROWSER_COMMANDS": BROWSER_COMMANDS,
            "MISSING_BROWSER": MISSING_BROWSER,
            "DOWNLOAD_DIR": stack.download_dir,
            "LINUX_ARGV": ["--disable-dev-shm-usage"],
            "CHROME_URL": f"http://127.0.0.1:{stack.chrome_port}/json/version",
            "CHROME_ARGV_TAIL": [
                "--no-sandbox",
                "--headless=new",
                "--disable-gpu",
                "--use-mock-keychain",
                "--password-store=basic",
                "--remote-debugging-address=0.0.0.0",
                f"--remote-debugging-port={stack.chrome_port}",
                "--remote-allow-origins=*",
                f"--proxy-server=http://127.0.0.1:{stack.egress_bridge_port}",
                f"--user-data-dir={stack.chrome_profile}",
                "about:blank",
            ],
            "CHROME_LOG": stack.chrome_log,
            "CHROME_PID": stack.chrome_pid,
            "PROXIED_URL": f"http://127.0.0.1:{stack.cdp_proxy_port}/json/version",
            "PROXY_ARGV": ["python3", stack.proxy_script],
            "PROXY_LOG": stack.proxy_log,
            "PROXY_PID": stack.proxy_pid,
            "EGRESS_BRIDGE_PORT": stack.egress_bridge_port,
            "EGRESS_BRIDGE_TASK_EXIT": f"{stack.task_base}.exit",
            "EGRESS_BRIDGE_TASK_LOG": f"{stack.task_base}.log",
            "PROBE_TIMEOUT_SECONDS": BROWSER_PROBE_TIMEOUT_SECONDS,
            "POLL_SLEEP_SECONDS": BROWSER_POLL_SLEEP_SECONDS,
            "CHROME_READY_BUDGET_SECONDS": CHROME_READY_BUDGET_SECONDS,
            "PROXY_READY_BUDGET_SECONDS": PROXY_READY_BUDGET_SECONDS,
            "LOG_TAIL_LINES": LOG_TAIL_LINES,
        }
    )
    return f"""cat >{shlex.quote(stack.proxy_script)} <<'CDP_PROXY'
{proxy_config}
{PROXY_PROGRAM}
CDP_PROXY
python3 - <<'BRING_UP_BROWSER_STACK'
# sandbox_chrome bring up
{browser_config}
{BROWSER_LOOKUP_PROGRAM}
{BRING_UP_PROGRAM}
BRING_UP_BROWSER_STACK"""


def _stack_down_command(stack: BrowserStack) -> str:
    config = _assignments(
        {
            "ROOT": stack.root,
            "LEASE_ID": stack.lease_id,
            "LOCK_PATH": stack.lock_path,
            "TASK_BASE": stack.task_base,
            "CHROME_PID": stack.chrome_pid,
            "PROXY_PID": stack.proxy_pid,
            "PROCESS_END_BUDGET_SECONDS": PROCESS_END_BUDGET_SECONDS,
            "POLL_SLEEP_SECONDS": BROWSER_POLL_SLEEP_SECONDS,
        }
    )
    return f"""python3 - <<'CLEAN_UP_BROWSER_STACK'
# sandbox_chrome clean up
{config}
{PROCESS_PROGRAM}
{STACK_CLEANUP_PROGRAM}
CLEAN_UP_BROWSER_STACK"""


def _bridge_down_command(stack: BrowserStack) -> str:
    config = _assignments(
        {
            "LEASE_ID": stack.lease_id,
            "LOCK_PATH": stack.lock_path,
            "TASK_BASE": stack.task_base,
            "PROCESS_END_BUDGET_SECONDS": PROCESS_END_BUDGET_SECONDS,
            "POLL_SLEEP_SECONDS": BROWSER_POLL_SLEEP_SECONDS,
        }
    )
    return f"""python3 - <<'REPLACE_BROWSER_BRIDGE'
# sandbox_chrome stop bridge
{config}
{PROCESS_PROGRAM}
{BRIDGE_CLEANUP_PROGRAM}
REPLACE_BROWSER_BRIDGE"""


def _resolve_command(stack: BrowserStack) -> str:
    config = _assignments(
        {
            "PROXIED_URL": f"http://127.0.0.1:{stack.cdp_proxy_port}/json/version",
            "BRIDGE_PORT": stack.egress_bridge_port,
            "TASK_EXIT": f"{stack.task_base}.exit",
            "TASK_LOG": f"{stack.task_base}.log",
            "PROBE_TIMEOUT_SECONDS": BROWSER_PROBE_TIMEOUT_SECONDS,
            "WAIT_SECONDS": PROXY_READY_BUDGET_SECONDS,
            "POLL_SECONDS": BROWSER_POLL_SLEEP_SECONDS,
            "LOG_TAIL_LINES": LOG_TAIL_LINES,
        }
    )
    return f"""python3 - <<'RESOLVE_BROWSER_STACK'
# sandbox_chrome resolve
{config}
import json
import socket
import time
import urllib.request
from pathlib import Path

deadline = time.monotonic() + WAIT_SECONDS
while True:
    try:
        with socket.create_connection(("127.0.0.1", BRIDGE_PORT), timeout=PROBE_TIMEOUT_SECONDS):
            break
    except OSError:
        if Path(TASK_EXIT).exists():
            try:
                lines = Path(TASK_LOG).read_text(errors="replace").splitlines()
                detail = "\\n".join(lines[-LOG_TAIL_LINES:])
            except OSError:
                detail = "the bridge wrote no log"
            raise SystemExit(f"the browser egress bridge exited before reattach\\n{{detail}}")
        if time.monotonic() >= deadline:
            raise SystemExit("the browser egress bridge did not restart before reattach")
        time.sleep(POLL_SECONDS)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
try:
    with opener.open(PROXIED_URL, timeout=PROBE_TIMEOUT_SECONDS) as reply:
        version = json.load(reply)
except Exception as error:
    raise SystemExit(f"the browser stack no longer answers: {{error}}") from error
print(version["webSocketDebuggerUrl"])
RESOLVE_BROWSER_STACK"""


@dataclass(frozen=True)
class SandboxChromeCdpLease:
    """One isolated Chrome stack owned for exactly the lifetime of this lease."""

    endpoint_: CdpEndpoint
    sandbox: Sandbox
    stack: BrowserStack

    async def endpoint(self) -> CdpEndpoint:
        return self.endpoint_

    async def token(self) -> str:
        return _stack_token(self.stack)

    async def place_file(self, path: str, read: FileBytes) -> str:
        """This Chrome runs inside the turn's own sandbox and opens the workspace directly, so the
        path it can reach is the path the caller already holds. `read` goes unawaited: nothing is
        copied out of the sandbox and back to hand a local browser a file it can already see."""
        return path

    async def download_dir(self) -> str:
        """A path inside the sandbox: this Chrome runs there, so that is the only filesystem it can
        write to and the only one the bytes can be read back from."""
        return self.stack.download_dir

    async def fetch_download(self, guid: str) -> bytes:
        """The download's bytes, read out of the sandbox Chrome wrote them to. `allowAndName` stores
        a completed download under its guid, so that is the file to read. The encoding reads stdin
        rather than passing a width flag: the flag is GNU-only, and the local carrier runs this on
        whatever host the deploy sits on. It stands alone in the command so the exit code is its own
        — a pipeline reports its last stage, which would mask an unreadable file as an empty
        download. Decoding drops the line wrapping, and runs in a thread: a whole file's worth of it
        is CPU work every other turn on this one loop would wait through. The file is sized first —
        it crosses whole into this process, and what a page downloads is not ours to trust."""
        stored = shlex.quote(f"{self.stack.download_dir}/{guid}")
        sized = await self.sandbox.bash(f"wc -c < {stored}", timeout_s=DOWNLOAD_SIZE_BUDGET_SECONDS)
        if sized.exit_code != 0:
            raise RuntimeError(f"sandbox_chrome could not read download {guid}: {sized.stderr}")
        if int(sized.stdout.strip()) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"download {guid} is {sized.stdout.strip()} bytes; this browser returns at most "
                f"{MAX_DOWNLOAD_BYTES}"
            )
        result = await self.sandbox.bash(
            f"base64 < {stored}",
            timeout_s=DOWNLOAD_READ_BUDGET_SECONDS,
        )
        if result.timed_out_after_s is not None:
            raise RuntimeError(
                f"sandbox_chrome stopped reading download {guid} after {result.timed_out_after_s}s"
            )
        if result.exit_code != 0:
            raise RuntimeError(f"sandbox_chrome could not read download {guid}: {result.stderr}")
        return await asyncio.to_thread(base64.b64decode, result.stdout)

    async def aclose(self) -> None:
        await _stop_stack(self.sandbox, self.stack)


@dataclass(frozen=True)
class SandboxChromeCdpProvider:
    """Core's CDP seam backed by a private Chrome stack in the turn's sandbox."""

    async def lease(self, sandbox: Sandbox | None = None) -> CdpLease:
        if sandbox is None:
            raise RuntimeError(
                "the sandbox_chrome cdp provider needs the turn's sandbox to reach its Chrome"
            )
        if sandbox.turn_id is None:
            raise RuntimeError(
                "the sandbox_chrome cdp provider needs a durable turn id to own its Chrome"
            )
        return await self._lease(sandbox, retry_recovered_stack=True)

    async def _lease(self, sandbox: Sandbox, retry_recovered_stack: bool) -> CdpLease:
        assert sandbox.turn_id is not None
        lease_id = sandbox.turn_id.hex
        allocated = await sandbox.bash(
            _allocate_command(lease_id), timeout_s=STACK_ALLOCATE_TIMEOUT_SECONDS
        )
        found = await sandbox.bash(
            _find_allocation_command(lease_id),
            timeout_s=STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS + COMMAND_REPORT_MARGIN_SECONDS,
        )
        try:
            if found.exit_code != 0:
                raise ValueError(found.stderr.strip() or found.stdout.strip())
            stack = _allocated_stack(lease_id, found.stdout)
        except (RuntimeError, ValueError) as error:
            await _abandon_allocation(sandbox, lease_id)
            allocation_error = allocated.stderr.strip() or allocated.stdout.strip()
            if allocated.exit_code != 0 and retry_recovered_stack:
                return await self._lease(sandbox, retry_recovered_stack=False)
            raise RuntimeError(
                "sandbox_chrome failed to allocate a recoverable browser stack: "
                f"{allocation_error or error}"
            ) from error
        if allocated.exit_code != 0:
            try:
                return await self.reattach(_stack_token(stack), sandbox)
            except SessionGone as error:
                if not retry_recovered_stack:
                    raise RuntimeError(
                        "sandbox_chrome could not reclaim the turn's interrupted browser stack"
                    ) from error
                return await self._lease(sandbox, retry_recovered_stack=False)
        try:
            await _start_bridge(sandbox, stack)
            result = await sandbox.bash(
                _browser_up_command(stack), timeout_s=BROWSER_START_TIMEOUT_SECONDS
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    "sandbox_chrome failed to bring up the browser: "
                    f"{await _bring_up_failure(sandbox, stack, result)}"
                )
            endpoint = await _endpoint(sandbox, stack, result.stdout)
        except BaseException as error:
            try:
                await asyncio.shield(_stop_stack(sandbox, stack))
            except Exception as leaked:
                error.add_note(f"the browser stack was not released: {leaked}")
            raise
        return SandboxChromeCdpLease(endpoint, sandbox, stack)

    async def reattach(self, token: str, sandbox: Sandbox | None = None) -> CdpLease:
        if sandbox is None or sandbox.turn_id is None:
            raise RuntimeError(
                "sandbox_chrome needs the recovered turn's sandbox to reconcile its Chrome"
            )
        stack = _stack_from_token(token)
        if stack.lease_id != sandbox.turn_id.hex:
            await _stop_stack(sandbox, stack)
            raise SessionGone(token)
        try:
            owned = await sandbox.bash(
                _find_allocation_command(stack.lease_id),
                timeout_s=STACK_ALLOCATION_RECOVERY_TIMEOUT_SECONDS + COMMAND_REPORT_MARGIN_SECONDS,
            )
            if owned.exit_code != 0 or _allocated_stack(stack.lease_id, owned.stdout) != stack:
                raise RuntimeError("the browser stack token no longer owns its slot")
            await _stop_bridge(sandbox, stack)
            await _start_bridge(sandbox, stack)
            resolved = await sandbox.bash(
                _resolve_command(stack),
                timeout_s=PROXY_READY_BUDGET_SECONDS + COMMAND_REPORT_MARGIN_SECONDS,
            )
            if resolved.exit_code != 0:
                raise RuntimeError(resolved.stderr.strip() or resolved.stdout.strip())
            endpoint = await _endpoint(sandbox, stack, resolved.stdout)
        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(_stop_stack(sandbox, stack))
            except Exception as leaked:
                cancelled.add_note(f"the browser stack was not released: {leaked}")
            raise
        except Exception:
            gone = SessionGone(token)
            try:
                await asyncio.shield(_stop_stack(sandbox, stack))
            except Exception as leaked:
                gone.add_note(f"the browser stack was not released: {leaked}")
            raise gone from None
        return SandboxChromeCdpLease(endpoint, sandbox, stack)


async def _endpoint(sandbox: Sandbox, stack: BrowserStack, local_url: str) -> CdpEndpoint:
    target = await sandbox.dial(stack.cdp_proxy_port)
    scheme = "wss" if target.tls else "ws"
    return CdpEndpoint(
        url=f"{scheme}://{target.host}{_ws_path(local_url, stack.chrome_port)}",
        headers=dict(target.headers),
    )


async def _start_bridge(sandbox: Sandbox, stack: BrowserStack) -> None:
    started = await sandbox.bash_task(
        _egress_bridge_up_command(stack),
        stack.task_base,
        detach=True,
        model_authored=False,
        timeout_s=STACK_TASK_START_TIMEOUT_SECONDS,
    )
    if started.exit_code != 0:
        raise RuntimeError(
            "sandbox_chrome failed to start the browser egress bridge: "
            f"{started.stderr.strip() or started.stdout.strip()}"
        )


async def _stop_bridge(sandbox: Sandbox, stack: BrowserStack) -> None:
    stopped = await sandbox.bash(
        _bridge_down_command(stack),
        timeout_s=PROCESS_END_BUDGET_SECONDS + COMMAND_REPORT_MARGIN_SECONDS,
    )
    if stopped.exit_code != 0:
        raise RuntimeError(
            "sandbox_chrome failed to replace the browser egress bridge: "
            f"{stopped.stderr.strip() or stopped.stdout.strip()}"
        )


async def _abandon_allocation(sandbox: Sandbox, lease_id: str) -> None:
    try:
        await sandbox.bash(
            _abandon_allocation_command(lease_id), timeout_s=STACK_ALLOCATE_TIMEOUT_SECONDS
        )
    except Exception:
        pass


async def _stop_stack(sandbox: Sandbox, stack: BrowserStack) -> None:
    stopped = await sandbox.bash(
        _stack_down_command(stack),
        timeout_s=STACK_CLEANUP_TIMEOUT_SECONDS,
    )
    if stopped.exit_code != 0:
        raise RuntimeError(
            "sandbox_chrome failed to stop the browser stack: "
            f"{stopped.stderr.strip() or stopped.stdout.strip()}"
        )


def _stack_from_token(token: str) -> BrowserStack:
    try:
        value = json.loads(token)
        if not isinstance(value, dict) or value.get("version") != TOKEN_VERSION:
            raise ValueError
        lease_id = value["lease_id"]
        if not isinstance(lease_id, str):
            raise ValueError
        return _located_stack(lease_id, {"dir": FIXED_BROWSER_DIR} | value)
    except (KeyError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        raise SessionGone(token) from None


def _stack_token(stack: BrowserStack) -> str:
    return json.dumps(
        {
            "version": TOKEN_VERSION,
            "lease_id": stack.lease_id,
            "slot": stack.slot,
            "dir": stack.browser_dir,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _allocated_stack(lease_id: str, reply: str) -> BrowserStack:
    try:
        return _located_stack(lease_id, json.loads(reply))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"sandbox_chrome read an unrecognized allocation {reply.strip()!r}"
        ) from error


def _located_stack(lease_id: str, value: object) -> BrowserStack:
    match value:
        case {"slot": int() as slot, "dir": str() as browser_dir} if not isinstance(slot, bool):
            return _stack(lease_id, slot, browser_dir)
    raise TypeError(f"not a browser stack location: {value!r}")


async def _bring_up_failure(sandbox: Sandbox, stack: BrowserStack, result: ExecResult) -> str:
    """A carrier deadline kills the command and its report with it, so the reason is read from the
    logs."""
    reported = result.stderr.strip() or result.stdout.strip()
    if result.timed_out_after_s is None:
        return reported
    logs = await sandbox.bash(
        shlex.join(
            (
                "tail",
                "-n",
                str(LOG_TAIL_LINES),
                stack.chrome_log,
                stack.proxy_log,
                f"{stack.task_base}.log",
            )
        )
        + " 2>&1 || true",
        timeout_s=LOG_TAIL_BUDGET_SECONDS,
    )
    stopped = f"the sandbox stopped the bring-up after {result.timed_out_after_s}s"
    tail = logs.stdout.strip()
    return f"{stopped}\n{tail}" if tail else f"{stopped}: {reported}"


def _ws_path(url: str, chrome_port: int) -> str:
    stripped = url.strip()
    for prefix in (f"ws://127.0.0.1:{chrome_port}", f"ws://localhost:{chrome_port}"):
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
