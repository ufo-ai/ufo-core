"""The sandbox proxy: scoped egress plus the root-owned workspace credential endpoint.

Agent processes reach the network solely through this proxy (their HTTP(S)_PROXY). The rule set is
resolved per request from the run token in the `Proxy-Authorization` header — the turn, hence the
turn's agent and acting member — so a sandbox sees only its own agent's egress: the workspace's
model and credential rules plus that agent's OAuth grants, derived fresh (never registered) and
cached per turn. A run with no or unknown token resolves to the model and credential base alone —
never a broad allow.

The same listener serves s3fs's ECS metadata fetch. Its unguessable signed path token resolves one
conversation and mints a short-lived STS credential whose inline policy reaches only that
conversation's workspace prefix. The local relay forwards the token from the private mount file.

Default-deny is a CONNECT the proxy refuses: ScopeRule admits exact model and grant hosts.
InternetRule admits a live turn's globally routable IPv4 after resolving and pinning DNS;
tokenless, ended-turn, private, and IPv6 destinations are refused. An admitted host carrying an
InjectionRule or ForwardRule is MITM'd — but only for a turn the DB still reports running: the
injection swaps in the real model or credential key, so a tokenless, unknown-turn, or terminal-turn
CONNECT to a keyed host is refused (403) and the key never reaches the wire (the gate the local
carrier leans on — a host process can reach the proxy directly). Authorized, the proxy terminates
TLS with a leaf minted from the per-process CA (in the container's trust store) and dispatches on
what the request carries: a grant sentinel in a
ForwardRule's header executes through the grant's broker under the granted account (the credential
exists only broker-side — the wire analog of a connector tool call); otherwise the sentinel
Authorization value is swapped for the real credential (selecting by the exact sentinel, so two
accounts on one host each draw only their own token and a foreign sentinel is passed upstream
untouched) and the request re-originates over its own verified TLS, so the raw key is never inside
the sandbox. An admitted host with neither rule kind is tunnelled
opaquely — a grant injects nothing, so its host is reached opaquely yet still counted. Each metered
host emits an egress metric and, off the relay path, writes an `egress` request row to the ledger
keyed to the turn — per CONNECT for a tunnelled host, per MITM'd request otherwise — except the
model host, whose teed SSE response is parsed for its token usage and metered under
`sandbox_tokens`, disjoint from the host turn loop's terminal `tokens` bill (which runs the model
host-side and never touches the proxy)."""

import asyncio
import ipaddress
import json
import ssl
import struct
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import dns.asyncresolver
import dns.exception
import dns.name
import sqlalchemy as sa

from ufo.accounting import (
    TOKENS_DIMENSION,
    record_egress_request,
    record_sandbox_tokens,
)
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.db import workspace_tx
from ufo.grants import GrantStore
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
from ufo.o11y import emit_metric, log
from ufo.sandbox.fs_creds import (
    SANDBOX_FS_CREDENTIAL_PATH,
    InvalidSandboxFsToken,
    SandboxFsCredentials,
)
from ufo.sandbox.proxy.rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    REQUEST_METER_DIMENSION,
    ConnectorTransferHosts,
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    Rule,
    ScopeRule,
    derive_cli_rules,
    derive_grant_rules,
)
from ufo.sandbox.session import ProxyEndpoint, RunToken
from ufo.schema import tables
from ufo.schema.records import RUNNING, Usage

PROXY_BIND_HOST = "0.0.0.0"
RELAY_CHUNK_BYTES = 65536
MAX_HEADER_BYTES = 65536
CONNECT_UPSTREAM_TIMEOUT_SECONDS = 30
DEFAULT_HTTPS_PORT = 443
MIN_CONNECT_PORT = 1
MAX_CONNECT_PORT = 65535
CA_VALID_DAYS = "3650"
LEAF_VALID_DAYS = "365"
RULE_CACHE_MAX = 4096
MAX_SSE_BUFFER_BYTES = 1_048_576
MAX_FORWARD_BODY_BYTES = 1_048_576

RuleResolver = Callable[["RunToken | None"], Awaitable[tuple[Rule, ...]]]
TurnAuthorizer = Callable[["RunToken"], Awaitable[bool]]
WorkspaceCredentials = Callable[[str, TurnAuthorizer], Awaitable[SandboxFsCredentials]]
PublicAddressResolver = Callable[[str, int], Awaitable[str]]


async def generate_ca() -> tuple[str, str]:
    """A self-signed CA (cert, key) minted via the openssl CLI — no crypto library dependency. It is
    minted once at boot and signs every per-host leaf for the life of the process (which outlives
    every turn), so it is valid well beyond any leaf's lifetime — a process running for months keeps
    serving certs the sandbox's trust store still validates, never an expired chain."""
    with tempfile.TemporaryDirectory() as work:
        key_path = Path(work) / "ca.key"
        cert_path = Path(work) / "ca.crt"
        await _openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            CA_VALID_DAYS,
            "-subj",
            "/CN=ufo-sandbox-proxy",
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
    authentication is the wire's isolation — agent A's turn resolves only A's grants, so A cannot
    inject or forward through another agent's account. A run with no or unknown token yields the
    base alone; a resolution error raises to the proxy, which fails closed to the base without
    caching it — never a broad allow, never another agent's grant. Deriving each call (not once at
    boot) is the liveness: a grant recorded mid-serve is live for the next turn."""

    base: tuple[Rule, ...]
    grants: GrantStore | None
    internet: tuple[InternetRule, ...] = ()
    transfer_hosts: ConnectorTransferHosts = field(
        default_factory=lambda: ConnectorTransferHosts(explicit={})
    )
    clis: Mapping[str, CliCredential] = field(default_factory=dict)

    async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]:
        if run is None:
            return self.base
        turn = await self._turn_of(run)
        if turn is None:
            return self.base
        agent_id, acting_member_id = turn
        rules = (*self.base, *self.internet)
        if self.grants is None:
            return rules
        granted = await self.grants.active_grants(run.workspace_id, agent_id)
        return (
            *rules,
            *derive_grant_rules(granted, self.transfer_hosts),
            *derive_cli_rules(granted, acting_member_id, self.clis),
        )

    async def _turn_of(self, run: RunToken) -> tuple[UUID, UUID | None] | None:
        """The turn's agent and acting member — the speaker when one authored the turn, else the
        member it acts on behalf of (a scheduled fire, a subagent chain) — in one indexed read.
        CLI-credential use gates on the acting member exactly as connector tools do, so a member's
        private grant forwards only on their own turns."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.agent_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.on_behalf_of_member_id,
                    ).where(
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.workspace_id == run.workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        acting = (
            row.speaker_member_id
            if row.speaker_member_id is not None
            else row.on_behalf_of_member_id
        )
        return row.agent_id, acting

    async def turn_live(self, run: RunToken) -> bool:
        """The egress-authorization gate: True only while the run token names a turn the DB still
        reports running. A keyed host's real-key injection is applied only for a live turn, so a
        token for a turn that has ended, a turn that never existed, or (checked before this) no
        token at all is denied at CONNECT and the key never reaches the wire. Read fresh per request
        — never the per-turn rule cache — so a turn that ends between requests can no longer draw
        the key, and it costs one indexed lookup on the turn's primary key."""
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.workspace_id == run.workspace_id,
                    )
                )
            ).scalar_one_or_none()
        return status == RUNNING


@dataclass
class EgressProxy:
    resolve: RuleResolver
    authorize: TurnAuthorizer
    ca_cert: str
    ca_key: str
    resolve_public: PublicAddressResolver | None = None
    pricing: Pricing = CORE_PRICING
    workspace_credentials: WorkspaceCredentials | None = None
    _server: asyncio.Server | None = field(default=None, init=False)
    _workdir: tempfile.TemporaryDirectory | None = field(default=None, init=False)
    _contexts: dict[str, ssl.SSLContext] = field(default_factory=dict, init=False)
    _mint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _meter_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _rule_cache: dict[str, tuple[Rule, ...]] = field(default_factory=dict, init=False)

    async def start(
        self, bind_host: str = PROXY_BIND_HOST, port: int = 0, public_url: str | None = None
    ) -> ProxyEndpoint:
        """Bind the proxy and return the endpoint carriers thread into every sandbox. `port` is the
        stable port a deploy fixes so an off-cluster sandbox and its exposing LoadBalancer share one
        known address; the default 0 is an ephemeral bind for tests. `public_url` is the
        externally-reachable base an off-cluster sandbox dials, carried through to the endpoint."""
        self._workdir = tempfile.TemporaryDirectory()
        root = Path(self._workdir.name)
        (root / "ca.crt").write_text(self.ca_cert)
        (root / "ca.key").write_text(self.ca_key)
        await _openssl("genrsa", "-out", str(root / "leaf.key"), "2048")
        self._server = await asyncio.start_server(self._handle, bind_host, port)
        bound = self._server.sockets[0].getsockname()[1]
        return ProxyEndpoint(port=bound, ca_cert=self.ca_cert, public_url=public_url)

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
        """One client connection: dispatch an s3fs credential fetch or proxy CONNECT."""
        try:
            try:
                request_line = await reader.readline()
                method, _, rest = request_line.decode(errors="replace").partition(" ")
                target = rest.split(" ", 1)[0]
                if method == "GET":
                    await self._serve_workspace_credentials(writer, target)
                    return
                if method != "CONNECT":
                    await _respond(writer, 405, "only CONNECT is proxied")
                    return
                host, _, port_text = target.partition(":")
                proxy_auth = ""
                while (line := await reader.readline()) not in (b"\r\n", b""):
                    name, _, value = line.decode(errors="replace").partition(":")
                    if name.strip().lower() == "proxy-authorization":
                        proxy_auth = value.strip()
            except OSError:
                return
            run = _run_token(proxy_auth)
            rules = await self._rules_for(run)
            connect_host = host
            exactly_scoped = any(
                isinstance(rule, ScopeRule) and host in rule.allowed_hosts for rule in rules
            )
            if not exactly_scoped and (
                run is None
                or not any(isinstance(rule, InternetRule) for rule in rules)
                or not await self.authorize(run)
            ):
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            try:
                port = int(port_text or DEFAULT_HTTPS_PORT)
            except ValueError:
                await _respond(writer, 400, "invalid CONNECT port")
                return
            if not MIN_CONNECT_PORT <= port <= MAX_CONNECT_PORT:
                await _respond(writer, 400, "invalid CONNECT port")
                return
            if not exactly_scoped:
                try:
                    resolver = self.resolve_public or self._resolve_public_address
                    connect_host = await resolver(host, port)
                except PermissionError:
                    await _respond(writer, 403, f"egress to {host} is not permitted")
                    return
                except (OSError, TimeoutError):
                    await _respond(writer, 502, f"cannot reach {host}")
                    return
                rules = (*rules, MeterRule(host=host, dimension=REQUEST_METER_DIMENSION))
            injections = [r for r in rules if isinstance(r, InjectionRule) and r.host == host]
            forwards = [r for r in rules if isinstance(r, ForwardRule) and r.host == host]
            if not injections and not forwards:
                await self._tunnel(
                    reader, writer, host, port, proxy_auth, rules, connect_host=connect_host
                )
            elif run is None or not await self.authorize(run):
                await _respond(writer, 403, f"egress to {host} requires a live turn")
            else:
                await self._mitm(
                    reader, writer, host, port, injections, forwards, proxy_auth, rules
                )
        finally:
            writer.close()

    async def _serve_workspace_credentials(self, writer: asyncio.StreamWriter, target: str) -> None:
        if self.workspace_credentials is None or not target.startswith(SANDBOX_FS_CREDENTIAL_PATH):
            await _respond(writer, 404, "not found")
            return
        token = target.removeprefix(SANDBOX_FS_CREDENTIAL_PATH)
        if not token or "/" in token:
            await _respond(writer, 404, "not found")
            return
        try:
            credentials = await self.workspace_credentials(token, self.authorize)
        except InvalidSandboxFsToken:
            await _respond(writer, 403, "invalid sandbox-fs token")
            return
        except Exception as error:
            log("sandbox_fs.refresh_failed", error_class=type(error).__name__)
            await _respond(writer, 502, "credential mint failed")
            return
        body = credentials.ecs_json()
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(body)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except OSError:
            pass

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

    async def _resolve_public_address(self, host: str, port: int) -> str:
        """Resolve and pin globally routable IPv4 with cancellable async DNS."""
        if ":" in host:
            raise PermissionError(host)
        addresses: tuple[ipaddress.IPv4Address, ...]
        try:
            addresses = (ipaddress.IPv4Address(host),)
        except ipaddress.AddressValueError:
            try:
                name = dns.name.from_text(host)
                answers = await dns.asyncresolver.resolve(
                    name,
                    "A",
                    lifetime=CONNECT_UPSTREAM_TIMEOUT_SECONDS,
                    search=False,
                )
            except (
                UnicodeError,
                struct.error,
                dns.exception.SyntaxError,
                dns.name.NameTooLong,
            ) as error:
                raise PermissionError(host) from error
            except dns.exception.DNSException as error:
                raise OSError(host) from error
            addresses = tuple(
                dict.fromkeys(ipaddress.IPv4Address(str(answer)) for answer in answers)
            )
        if not addresses or any(
            not address.is_global or address.is_multicast for address in addresses
        ):
            raise PermissionError(host)
        return str(addresses[0])

    async def _tunnel(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
        proxy_auth: str,
        rules: tuple[Rule, ...],
        connect_host: str,
    ) -> None:
        """An admitted host with no key to inject (a grant holds its token server-side): relay bytes
        opaquely, never terminating TLS. A MeterRule host is counted once the tunnel is
        established — the proxy cannot see individual requests inside the opaque TLS, so egress to a
        granted host is metered at CONNECT granularity, the metric and the `egress` ledger row keyed
        to the turn off the relay path. A connection that never opens (502) is not counted."""
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, port),
                timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError):
            await _respond(writer, 502, f"cannot reach {host}")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        self._meter(host, rules)
        self._meter_ledger(host, proxy_auth, rules)
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _mitm(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
        injections: list[InjectionRule],
        forwards: list[ForwardRule],
        proxy_auth: str,
        rules: tuple[Rule, ...],
    ) -> None:
        """Terminate the sandbox's TLS with a minted leaf and dispatch on what the request carries:
        a grant sentinel in a ForwardRule's header executes through that grant's broker (the
        credential is injected server-side, never here); otherwise the sentinel Authorization value
        is swapped for its account's real key (selected among this host's injections by the exact
        sentinel) and the request re-originates upstream over verified TLS — the response streams
        straight back."""
        leaf_context = await self._leaf_context(host)
        client_reader, client_writer = await _start_tls_server(reader, writer, leaf_context)
        request = await _read_request_head(client_reader)
        if request is None:
            return
        matched = _forward_match(request[1], forwards)
        if matched is not None:
            await self._forward_broker(
                client_reader, client_writer, matched, request, host, proxy_auth, rules
            )
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
        tokens_metered = any(
            isinstance(rule, MeterRule) and rule.host == host and rule.dimension == TOKENS_DIMENSION
            for rule in rules
        )
        if not tokens_metered:
            await _relay(client_reader, client_writer, upstream_reader, upstream_writer)
            return
        accumulator = SseTokenUsage(host)
        await _relay(
            client_reader, client_writer, upstream_reader, upstream_writer, accumulator.feed
        )
        self._meter_tokens(proxy_auth, accumulator)

    async def _forward_broker(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        rule: ForwardRule,
        request: tuple[bytes, list[bytes]],
        host: str,
        proxy_auth: str,
        rules: tuple[Rule, ...],
    ) -> None:
        """Execute one sentinel-carrying request through the grant's broker instead of
        re-originating it — the wire analog of a connector tool call, so the account's credential
        exists only broker-side. The body is read whole and bounded (the broker call is one
        enveloped API request, never a stream), the sentinel header stays here, and the provider's
        reconstructed response is written back with `connection: close` so the next request is a
        fresh MITM that re-matches its own sentinel. A broker fault answers 502 — the client's
        wait always ends."""
        request_line, headers = request
        method, _, rest = request_line.decode(errors="replace").partition(" ")
        path = rest.split(" ", 1)[0]
        body = await _read_request_body(client_reader, headers)
        if body is None:
            await _respond(client_writer, 413, "forwarded request body is chunked or too large")
            return
        self._meter(host, rules)
        self._meter_ledger(host, proxy_auth, rules)
        try:
            response = await rule.forward.forward(
                rule.account_id,
                method,
                f"https://{host}{path}",
                _forward_headers(headers, rule),
                body,
            )
        except Exception as error:
            log("egress.forward_failed", host=host, error_class=type(error).__name__)
            await _respond(client_writer, 502, "broker forward failed")
            return
        try:
            client_writer.write(_forward_response_bytes(response))
            await client_writer.drain()
        except OSError:
            pass

    async def _leaf_context(self, host: str) -> ssl.SSLContext:
        cached = self._contexts.get(host)
        if cached is not None:
            return cached
        if self._workdir is None:
            raise RuntimeError("egress proxy must be started before minting a leaf context")
        async with self._mint_lock:
            cached = self._contexts.get(host)
            if cached is not None:
                return cached
            root = Path(self._workdir.name)
            cert_path = root / f"leaf-{host}.crt"
            ext_path = root / f"leaf-{host}.ext"
            ext_path.write_text(f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n")
            await _openssl(
                "req",
                "-new",
                "-key",
                str(root / "leaf.key"),
                "-subj",
                f"/CN={host}",
                "-out",
                str(root / f"leaf-{host}.csr"),
            )
            await _openssl(
                "x509",
                "-req",
                "-in",
                str(root / f"leaf-{host}.csr"),
                "-CA",
                str(root / "ca.crt"),
                "-CAkey",
                str(root / "ca.key"),
                "-CAcreateserial",
                "-days",
                LEAF_VALID_DAYS,
                "-extfile",
                str(ext_path),
                "-out",
                str(cert_path),
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
        row keyed to the turn; the model host (dimension `tokens`) writes no egress count — its cost
        is the token bill parsed from its teed response and written under `sandbox_tokens`."""
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

    def _meter_tokens(self, proxy_auth: str, accumulator: "SseTokenUsage") -> None:
        """Meter the model host's teed response under `sandbox_tokens`, off the relay path. A stream
        that reported no usage (a call that omitted OpenAI's `stream_options.include_usage`, or a
        body the parser could not read) is logged and skipped, never a failed relay."""
        parsed = accumulator.usage()
        if parsed is None:
            log("egress.tokens_usage_absent", host=accumulator.host)
            return
        model, usage = parsed
        task = asyncio.ensure_future(self._write_sandbox_tokens(proxy_auth, model, usage))
        self._meter_tasks.add(task)
        task.add_done_callback(self._meter_tasks.discard)

    async def _write_sandbox_tokens(self, proxy_auth: str, model: str, usage: Usage) -> None:
        try:
            run = RunToken.from_proxy_auth(proxy_auth)
            async with workspace_tx() as connection:
                await record_sandbox_tokens(
                    connection, run.workspace_id, run.turn_id, model, usage, self.pricing
                )
        except Exception as error:
            log("egress.tokens_meter_failed", error_class=type(error).__name__)


async def _start_tls_server(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Send the CONNECT 200 and upgrade the connection to TLS as the server side, keeping the same
    StreamReader/StreamWriter so the caller reads and writes decrypted bytes. Reading is paused
    before the 200 so the client's TLS ClientHello (which follows immediately) stays in the socket
    buffer for the SSL layer instead of being consumed into the plaintext reader."""
    loop = asyncio.get_running_loop()
    transport = writer.transport
    transport.pause_reading()  # type: ignore[attr-defined]
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    protocol = transport.get_protocol()
    tls_transport = await loop.start_tls(transport, protocol, context, server_side=True)
    protocol._transport = tls_transport  # type: ignore[attr-defined]
    writer._transport = tls_transport  # type: ignore[attr-defined]
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


def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None:
    """The ForwardRule whose grant sentinel this request carries: the rule's header holds the
    sentinel exactly, or scheme-prefixed (`token <sentinel>`, `Bearer <sentinel>`) as CLIs send
    auth. Selection is by the exact sentinel, so two accounts on one host each draw only their own
    grant, and a request with no sentinel falls through to the direct upstream path."""
    for line in headers:
        name, _, value = line.partition(b":")
        header = name.strip().lower()
        tokens = value.split()
        if not tokens or len(tokens) > 2:
            continue
        for rule in candidates:
            if header == rule.header.encode().lower() and tokens[-1] == rule.sentinel.encode():
                return rule
    return None


async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | None:
    """The whole body of a broker-forwarded request, bounded next to the one external call that
    sends it — the broker takes an enveloped API request, never a stream. A chunked, over-bound,
    negative-length, or truncated body answers None and the request is refused, never silently
    clipped — a negative length would otherwise reach `readexactly`, whose ValueError escapes the
    caller's IncompleteReadError guard and drops the connection with no response."""
    length = 0
    for line in headers:
        name, _, value = line.partition(b":")
        match name.strip().lower():
            case b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    return None
            case b"transfer-encoding":
                return None
    if length == 0:
        return b""
    if length < 0 or length > MAX_FORWARD_BODY_BYTES:
        return None
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None


def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]:
    """The request headers the broker forwards on: everything the client sent minus the
    sentinel-bearing header (the broker injects the real credential), the hop-by-hop connection
    headers this proxy owns, and host/content-length, which the broker's own client re-derives."""
    dropped = {b"connection", b"proxy-connection", b"host", b"content-length"}
    dropped.add(rule.header.encode().lower())
    forwarded: dict[str, str] = {}
    for line in headers:
        name, _, value = line.partition(b":")
        if name.strip().lower() in dropped:
            continue
        forwarded[name.strip().decode("latin-1")] = value.strip().decode("latin-1")
    return forwarded


def _forward_response_bytes(response: ForwardedResponse) -> bytes:
    """The provider response as one HTTP/1.1 exchange: the broker's reconstructed status and
    headers, the body length this proxy measured, and `connection: close` so the client re-CONNECTs
    for its next request. The broker's headers come from the provider's JSON envelope with no wire
    validation, so any header whose name or value carries a CR or LF is dropped — it would otherwise
    split the response written back into the sandbox's TLS stream (header injection)."""
    try:
        reason = HTTPStatus(response.status).phrase
    except ValueError:
        reason = ""
    dropped = {"content-length", "transfer-encoding", "content-encoding", "connection"}
    head = bytearray(f"HTTP/1.1 {response.status} {reason}".rstrip().encode() + b"\r\n")
    for name, value in response.headers.items():
        if name.lower() in dropped or _has_crlf(name) or _has_crlf(value):
            continue
        head += f"{name}: {value}".encode() + b"\r\n"
    head += b"content-length: " + str(len(response.body)).encode() + b"\r\n"
    head += b"connection: close\r\n\r\n"
    return bytes(head) + response.body


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value


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
    on_downstream: Callable[[bytes], None] | None = None,
) -> None:
    """Pump both directions until either closes; the response (upstream→client) reaching EOF ends
    the exchange, so a streamed response relays chunk by chunk and stops when the upstream shuts.
    `on_downstream`, when given, tees each response chunk after it is forwarded — the token meter
    reads the stream without ever holding the client's bytes back."""
    down = asyncio.create_task(_pump(upstream_reader, client_writer, on_downstream))
    up = asyncio.create_task(_pump(client_reader, upstream_writer))
    await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    for task in (down, up):
        task.cancel()
    upstream_writer.close()


async def _pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    on_chunk: Callable[[bytes], None] | None = None,
) -> None:
    try:
        while chunk := await reader.read(RELAY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
            if on_chunk is not None:
                on_chunk(chunk)
    except (OSError, asyncio.CancelledError):
        pass


def _int_field(usage: dict[str, object], name: str) -> int:
    value = usage.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass
class SseTokenUsage:
    """Recover a model host's token usage from its teed response without buffering the whole stream:
    forwarded chunks are fed here line by line, so only one partial line is ever held (bounded by
    MAX_SSE_BUFFER_BYTES; a line past the bound stops parsing, never the relay). A streamed SSE
    response reports usage in `data:` events — Anthropic input/cache on `message_start` and the
    final `output_tokens` on `message_delta`; OpenAI (with `stream_options.include_usage`) both on a
    terminal usage chunk. A non-streaming response is instead one JSON body whose top-level `usage`
    block is recovered the same way (its shape is the SSE payload without the `data:` frame), so a
    single-JSON in-sandbox completion is metered too. The two are mutually exclusive per response —
    an SSE stream sets usage before the body branch runs — so the JSON path never re-meters a
    stream. A response that carried no usage yields None, so the relay is metered only when the
    model actually reported it."""

    host: str
    _buffer: bytearray = field(default_factory=bytearray, init=False)
    _overflowed: bool = field(default=False, init=False)
    _seen: bool = field(default=False, init=False)
    _model: str = field(default="", init=False)
    _input: int = field(default=0, init=False)
    _output: int = field(default=0, init=False)
    _cache_read: int = field(default=0, init=False)
    _cache_write: int = field(default=0, init=False)

    def feed(self, chunk: bytes) -> None:
        if self._overflowed:
            return
        self._buffer += chunk
        while (newline := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            self._consume(line)
        if len(self._buffer) > MAX_SSE_BUFFER_BYTES:
            self._overflowed = True
            self._buffer.clear()

    def usage(self) -> tuple[str, Usage] | None:
        if not self._seen:
            self._maybe_json_body(bytes(self._buffer).strip())
        if not self._seen:
            return None
        return self._model, Usage(
            input_tokens=self._input,
            output_tokens=self._output,
            cache_read_tokens=self._cache_read,
            cache_write_tokens=self._cache_write,
        )

    def _consume(self, line: bytes) -> None:
        payload = line.strip()
        if not payload.startswith(b"data:"):
            self._maybe_json_body(payload)
            return
        payload = payload[len(b"data:") :].strip()
        if not payload.startswith(b"{"):
            return
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        if self.host == ANTHROPIC_HOST:
            self._anthropic(event)
        elif self.host == OPENAI_HOST:
            self._openai(event)

    def _maybe_json_body(self, payload: bytes) -> None:
        """A non-streaming completion's whole response body is a single JSON object, not `data:`
        events: parse its top-level usage when the SSE path saw none. Guarded on `_seen`, so once a
        stream has reported usage this never fires — the working SSE path is never re-metered.
        Called for each non-`data:` line (a newline-terminated body) and for the un-terminated
        trailing buffer at `usage()` (the common compact body), so either framing is recovered
        exactly once."""
        if self._seen or not payload.startswith(b"{"):
            return
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        if self.host == ANTHROPIC_HOST:
            model = event.get("model")
            if isinstance(model, str):
                self._model = model
            self._absorb_anthropic(event.get("usage"), initial=True)
        elif self.host == OPENAI_HOST:
            self._openai(event)

    def _anthropic(self, event: dict[str, object]) -> None:
        match event.get("type"):
            case "message_start":
                message = event.get("message")
                if isinstance(message, dict):
                    model = message.get("model")
                    if isinstance(model, str):
                        self._model = model
                    self._absorb_anthropic(message.get("usage"), initial=True)
            case "message_delta":
                self._absorb_anthropic(event.get("usage"), initial=False)

    def _absorb_anthropic(self, usage: object, initial: bool) -> None:
        if not isinstance(usage, dict):
            return
        if initial:
            self._input = _int_field(usage, "input_tokens")
            self._cache_read = _int_field(usage, "cache_read_input_tokens")
            self._cache_write = _int_field(usage, "cache_creation_input_tokens")
        output = usage.get("output_tokens")
        if isinstance(output, int) and not isinstance(output, bool):
            self._output = output
        self._seen = True

    def _openai(self, event: dict[str, object]) -> None:
        model = event.get("model")
        if isinstance(model, str):
            self._model = model
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return
        self._input = _int_field(usage, "prompt_tokens")
        self._output = _int_field(usage, "completion_tokens")
        self._seen = True


async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    """Write a terminal refusal to the client; a peer that vanished before reading it is routine —
    the connection is closing either way."""
    try:
        writer.write(f"HTTP/1.1 {status} {message}\r\n\r\n".encode())
        await writer.drain()
    except OSError:
        pass
