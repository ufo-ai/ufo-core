"""The egress proxy: the container's only route out, and the seam where the real key hits the wire.

The sandbox reaches the network solely through this proxy (its HTTP(S)_PROXY). The rule set is
resolved per request from the run token in the `Proxy-Authorization` header — the turn, hence the
turn's agent — so a sandbox sees only its own agent's egress: the workspace's model and credential
rules plus that agent's OAuth grants, derived fresh (never registered) and cached per turn. A run
with no or unknown token resolves to the model and credential base alone — never a broad allow.

Default-deny is a CONNECT the proxy refuses: a host no resolved ScopeRule admits gets a 403 and
never leaves the machine. An admitted host carrying an InjectionRule is MITM'd — the proxy
terminates TLS with a leaf minted from the per-process CA (in the container's trust store), swaps
the sentinel Authorization value the sandbox sees for the real credential (selecting by the exact
sentinel, so two accounts on one host each draw only their own token and a foreign sentinel is
passed upstream untouched), and re-originates over its own verified TLS, so the raw key is never
inside the sandbox. An admitted host with no InjectionRule is tunnelled opaquely. Each forwarded
request to a metered host emits an egress metric and, off the relay path, writes an `egress` request
row to the ledger keyed to the turn — except the model host, whose cost is the token bill the turn
commits at terminal, so metering it here would double-count."""

import asyncio
import ssl
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from selfhost.accounting import TOKENS_DIMENSION, record_egress_request
from selfhost.db import workspace_tx
from selfhost.grants import GrantStore
from selfhost.o11y import emit_metric, log
from selfhost.sandbox.proxy.rules import (
    InjectionRule,
    MeterRule,
    Rule,
    ScopeRule,
    derive_grant_rules,
)
from selfhost.sandbox.session import ProxyEndpoint, RunToken
from selfhost.schema import tables

PROXY_BIND_HOST = "0.0.0.0"
RELAY_CHUNK_BYTES = 65536
MAX_HEADER_BYTES = 65536
CONNECT_UPSTREAM_TIMEOUT_SECONDS = 30
DEFAULT_HTTPS_PORT = 443
CERT_VALID_DAYS = "1"
RULE_CACHE_MAX = 4096

RuleResolver = Callable[["RunToken | None"], Awaitable[tuple[Rule, ...]]]


async def generate_ca() -> tuple[str, str]:
    """A self-signed CA (cert, key) minted via the openssl CLI — no crypto library dependency."""
    with tempfile.TemporaryDirectory() as work:
        key_path = Path(work) / "ca.key"
        cert_path = Path(work) / "ca.crt"
        await _openssl(
            "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", CERT_VALID_DAYS, "-subj", "/CN=selfhost-sandbox-proxy",
        )
        return cert_path.read_text(), key_path.read_text()


async def _openssl(*argv: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "openssl", *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"openssl {argv[0]} failed: {stderr.decode().strip()}")


@dataclass(frozen=True)
class PerAgentRules:
    """Resolve the proxy's rule set for one turn's agent, derived from the run token each call: the
    workspace-wide model and credential base plus that agent's own OAuth grant rules. Per-agent
    scoping is the wire's isolation — agent A's turn resolves only A's grants, so A can neither
    reach (no ScopeRule) nor inject (no matching sentinel) another agent's granted account. A run
    with no or unknown token yields the base alone; a resolution error raises to the proxy, which
    fails closed to the base without caching it — never a broad allow, never another agent's grant.
    Deriving each call (not once at boot) is the liveness: a grant recorded mid-serve is live for
    the next turn."""

    base: tuple[Rule, ...]
    grants: GrantStore | None

    async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]:
        if run is None or self.grants is None:
            return self.base
        agent_id = await self._agent_of(run)
        if agent_id is None:
            return self.base
        granted = await self.grants.active_grants(run.workspace_id, agent_id)
        return (*self.base, *derive_grant_rules(granted))

    async def _agent_of(self, run: RunToken) -> UUID | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.turn.c.agent_id).where(
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.workspace_id == run.workspace_id,
                    )
                )
            ).scalar_one_or_none()


@dataclass
class EgressProxy:
    resolve: RuleResolver
    ca_cert: str
    ca_key: str
    _server: asyncio.Server | None = field(default=None, init=False)
    _workdir: tempfile.TemporaryDirectory | None = field(default=None, init=False)
    _contexts: dict[str, ssl.SSLContext] = field(default_factory=dict, init=False)
    _mint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _meter_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _rule_cache: dict[str, tuple[Rule, ...]] = field(default_factory=dict, init=False)

    async def start(self, bind_host: str = PROXY_BIND_HOST) -> ProxyEndpoint:
        self._workdir = tempfile.TemporaryDirectory()
        root = Path(self._workdir.name)
        (root / "ca.crt").write_text(self.ca_cert)
        (root / "ca.key").write_text(self.ca_key)
        await _openssl("genrsa", "-out", str(root / "leaf.key"), "2048")
        self._server = await asyncio.start_server(self._handle, bind_host, 0)
        port = self._server.sockets[0].getsockname()[1]
        return ProxyEndpoint(port=port, ca_cert=self.ca_cert)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._meter_tasks:
            await asyncio.gather(*self._meter_tasks, return_exceptions=True)
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            method, _, rest = request_line.decode(errors="replace").partition(" ")
            if method != "CONNECT":
                await _respond(writer, 405, "only CONNECT is proxied")
                return
            host, _, port_text = rest.split(" ", 1)[0].partition(":")
            proxy_auth = ""
            while (line := await reader.readline()) not in (b"\r\n", b""):
                name, _, value = line.decode(errors="replace").partition(":")
                if name.strip().lower() == "proxy-authorization":
                    proxy_auth = value.strip()
            rules = await self._rules_for(_run_token(proxy_auth))
            if not any(isinstance(r, ScopeRule) and host in r.allowed_hosts for r in rules):
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            port = int(port_text or DEFAULT_HTTPS_PORT)
            injections = [r for r in rules if isinstance(r, InjectionRule) and r.host == host]
            if not injections:
                await self._tunnel(reader, writer, host, port)
            else:
                await self._mitm(reader, writer, host, port, injections, proxy_auth, rules)
        finally:
            writer.close()

    async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]:
        """The resolved rule set for this turn's agent, cached per run token so the DB is hit once
        per turn, not once per request. The run token is unique per turn, so a grant recorded
        mid-serve is live for the next turn (a fresh token) without a proxy restart. Bounded by
        RULE_CACHE_MAX, evicting the oldest entry once full. A resolution that errors fails closed
        to the base and is NOT cached — a transient DB blip degrades one request, never the turn."""
        if run is None:
            return await self.resolve(None)
        key = run.encode()
        hit = self._rule_cache.get(key)
        if hit is not None:
            return hit
        try:
            rules = await self.resolve(run)
        except Exception as error:
            log("egress.resolve_failed", turn=str(run.turn_id), error_class=type(error).__name__)
            return await self.resolve(None)
        if len(self._rule_cache) >= RULE_CACHE_MAX:
            del self._rule_cache[next(iter(self._rule_cache))]
        self._rule_cache[key] = rules
        return rules

    async def _tunnel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        """An admitted host with no key to inject: relay bytes opaquely, never terminating TLS."""
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError):
            await _respond(writer, 502, f"cannot reach {host}")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _mitm(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
        injections: list[InjectionRule],
        proxy_auth: str,
        rules: tuple[Rule, ...],
    ) -> None:
        """Terminate the sandbox's TLS with a minted leaf, swap the sentinel the sandbox sent for
        its account's real key (selected among this host's injections by the exact sentinel), and
        re-originate the request upstream over verified TLS — the response streams straight back."""
        leaf_context = await self._leaf_context(host)
        client_reader, client_writer = await _start_tls_server(reader, writer, leaf_context)
        request = await _read_request_head(client_reader)
        if request is None:
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=True, server_hostname=host),
                timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError, ssl.SSLError):
            return
        request_line, headers = request
        upstream_writer.write(request_line)
        upstream_writer.write(_inject(headers, injections))
        upstream_writer.write(b"\r\n")
        await upstream_writer.drain()
        self._meter(host, rules)
        self._meter_ledger(host, proxy_auth, rules)
        await _relay(client_reader, client_writer, upstream_reader, upstream_writer)

    async def _leaf_context(self, host: str) -> ssl.SSLContext:
        cached = self._contexts.get(host)
        if cached is not None:
            return cached
        async with self._mint_lock:
            cached = self._contexts.get(host)
            if cached is not None:
                return cached
            root = Path(self._workdir.name)
            cert_path = root / f"leaf-{host}.crt"
            ext_path = root / f"leaf-{host}.ext"
            ext_path.write_text(f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n")
            await _openssl(
                "req", "-new", "-key", str(root / "leaf.key"),
                "-subj", f"/CN={host}", "-out", str(root / f"leaf-{host}.csr"),
            )
            await _openssl(
                "x509", "-req", "-in", str(root / f"leaf-{host}.csr"),
                "-CA", str(root / "ca.crt"), "-CAkey", str(root / "ca.key"), "-CAcreateserial",
                "-days", CERT_VALID_DAYS, "-extfile", str(ext_path), "-out", str(cert_path),
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(cert_path), keyfile=str(root / "leaf.key"))
            context.set_alpn_protocols(["http/1.1"])
            self._contexts[host] = context
            return context

    def _meter(self, host: str, rules: tuple[Rule, ...]) -> None:
        for rule in rules:
            if isinstance(rule, MeterRule) and rule.host == host:
                emit_metric("sandbox_egress_total", host=host, dimension=rule.dimension)

    def _meter_ledger(self, host: str, proxy_auth: str, rules: tuple[Rule, ...]) -> None:
        """Meter egress to the ledger off the relay path so a slow DB never stalls the sandbox's
        egress. A metered host whose MeterRule dimension is not `tokens` writes one `egress` request
        row keyed to the turn; the model host (dimension `tokens`) is skipped — its cost is the
        token bill `record_turn_usage` commits at terminal, so metering here would double-count."""
        if not any(
            isinstance(rule, MeterRule) and rule.host == host and rule.dimension != TOKENS_DIMENSION
            for rule in rules
        ):
            return
        task = asyncio.ensure_future(self._write_egress(host, proxy_auth))
        self._meter_tasks.add(task)
        task.add_done_callback(self._meter_tasks.discard)

    async def _write_egress(self, host: str, proxy_auth: str) -> None:
        try:
            run = RunToken.from_proxy_auth(proxy_auth)
            async with workspace_tx() as connection:
                await record_egress_request(connection, run.workspace_id, run.turn_id)
        except Exception as error:
            log("egress.meter_failed", host=host, error_class=type(error).__name__)


async def _start_tls_server(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Send the CONNECT 200 and upgrade the connection to TLS as the server side, keeping the same
    StreamReader/StreamWriter so the caller reads and writes decrypted bytes. Reading is paused
    before the 200 so the client's TLS ClientHello (which follows immediately) stays in the socket
    buffer for the SSL layer instead of being consumed into the plaintext reader."""
    loop = asyncio.get_running_loop()
    transport = writer.transport
    transport.pause_reading()
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    protocol = transport.get_protocol()
    tls_transport = await loop.start_tls(transport, protocol, context, server_side=True)
    protocol._transport = tls_transport
    writer._transport = tls_transport
    return reader, writer


async def _read_request_head(
    reader: asyncio.StreamReader,
) -> tuple[bytes, list[bytes]] | None:
    """The request line and header lines (each still terminated), bounded so a hostile client cannot
    exhaust memory before the body is even streamed."""
    request_line = await reader.readline()
    if not request_line:
        return None
    headers: list[bytes] = []
    total = len(request_line)
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b""):
            break
        total += len(line)
        if total > MAX_HEADER_BYTES:
            return None
        headers.append(line)
    return request_line, headers


def _run_token(proxy_auth: str) -> RunToken | None:
    """The turn behind a `Proxy-Authorization` header, or None when it is absent or malformed — an
    unattributed request resolves to the base rules, never a broad allow."""
    if not proxy_auth:
        return None
    try:
        return RunToken.from_proxy_auth(proxy_auth)
    except ValueError:
        return None


def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes:
    """Rewrite the header block: swap a header the sandbox set to a candidate's sentinel for that
    candidate's real secret — selection is by the exact sentinel seen, so among two accounts on one
    host each sentinel draws only its own token, and a foreign or absent sentinel is passed upstream
    unchanged (never another account's token). Force `Connection: close` so each request is a fresh
    MITM that re-applies the swap."""
    rebuilt = bytearray()
    for line in headers:
        field_name, _, value = line.partition(b":")
        name = field_name.strip().lower()
        if name in (b"connection", b"proxy-connection"):
            continue
        chosen = next(
            (
                c
                for c in candidates
                if name == c.header.encode().lower() and value.strip() == c.sentinel.encode()
            ),
            None,
        )
        if chosen is not None:
            rebuilt += chosen.header.encode() + b": " + chosen.real.encode() + b"\r\n"
        else:
            rebuilt += line
    rebuilt += b"connection: close\r\n"
    return bytes(rebuilt)


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    """Pump both directions until either closes; the response (upstream→client) reaching EOF ends
    the exchange, so a streamed response relays chunk by chunk and stops when the upstream shuts."""
    down = asyncio.create_task(_pump(upstream_reader, client_writer))
    up = asyncio.create_task(_pump(client_reader, upstream_writer))
    await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    for task in (down, up):
        task.cancel()
    upstream_writer.close()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(RELAY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass


async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    writer.write(f"HTTP/1.1 {status} {message}\r\n\r\n".encode())
    await writer.drain()
