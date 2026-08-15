"""The sandbox proxy: scoped egress for every agent process.

Agent processes reach the network solely through this proxy (their HTTP(S)_PROXY). The rule set is
resolved from the deployment-signed token in the `Proxy-Authorization` header — a run token naming
the turn, hence the turn's agent and acting member, or a probe token naming a conversation and
carrying its own deadline — so a sandbox sees only its own agent's egress: the workspace's model and
credential rules plus that agent's OAuth grants. An unsigned, unknown, ended, or expired principal
reaches no host, and a probe resolves no model-key injection: an unattended exec is not the
deployment's model spend to make.

Default-deny is a CONNECT the proxy refuses: ScopeRule admits exact model and grant hosts.
InternetRule admits a live turn's globally routable IPv4 after resolving and pinning DNS;
tokenless, ended-turn, private, and IPv6 destinations are refused. Every admitted host requires a
turn the DB still reports running, or an unspent probe deadline. An admitted host carrying an
InjectionRule or ForwardRule is
MITM'd; the injection swaps in the real model or credential key. Authorized, the proxy terminates
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
keyed to the principal — per CONNECT for a tunnelled host, per MITM'd request otherwise — except the
model host, whose teed SSE response is parsed for its token usage and metered under
`sandbox_tokens`, disjoint from the host turn loop's terminal `tokens` bill (which runs the model
host-side and never touches the proxy). A probe's `egress` row names no turn: it is billed to its
workspace, where a background job's spend is billed."""

import asyncio
import ipaddress
import json
import ssl
import struct
import tempfile
import time
import zlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Protocol
from uuid import UUID

import dns.asyncresolver
import dns.exception
import dns.name
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import (
    TOKENS_DIMENSION,
    record_egress_request,
    record_probe_egress_request,
    record_sandbox_tokens,
)
from ufo.agent_scope import agent
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.manifest import CredentialSlot
from ufo.grants import GrantStore
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
from ufo.o11y import emit_metric, log, log_error, warn
from ufo.sandbox.containment import contained_file, contained_leaf
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
    derive_credential_rules,
    derive_grant_rules,
)
from ufo.sandbox.session import (
    SENTINEL_MODEL_KEY,
    ProbeToken,
    ProbeTokenCodec,
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
)
from ufo.schema import tables
from ufo.schema.records import RUNNING, Usage
from ufo.workspace import ws

PROXY_BIND_HOST = "0.0.0.0"
RELAY_CHUNK_BYTES = 65536
MAX_HEADER_BYTES = 65536
PROXY_HEADER_TIMEOUT_SECONDS = 10
MAX_PROXY_CONNECTIONS = 512
MAX_PROXY_CONNECTIONS_PER_WORKSPACE = 64
CONNECT_UPSTREAM_TIMEOUT_SECONDS = 30
RELAY_RESPONSE_IDLE_TIMEOUT_SECONDS = 300
DEFAULT_HTTPS_PORT = 443
MIN_CONNECT_PORT = 1
MAX_CONNECT_PORT = 65535
CA_VALID_DAYS = "3650"
LEAF_VALID_DAYS = "365"
LEAF_NAME_PREFIX = "leaf-"
LEAF_UNNAMED_HOST = "unnamed"
LEAF_FILE_MODE = 0o600
RULE_CACHE_MAX = 4096
RULE_CACHE_TTL_SECONDS = 240
METER_QUEUE_MAX = 4096
METER_BATCH_MAX = 256
METER_BATCH_WINDOW_SECONDS = 0.01
MAX_SSE_BUFFER_BYTES = 1_048_576
MAX_FORWARD_BODY_BYTES = 1_048_576
MAX_REFUSAL_DRAIN_BYTES = 8 * 1_048_576
REFUSAL_DRAIN_TIMEOUT_SECONDS = 5
EGRESS_AUTHORIZATION_UNAVAILABLE = "egress authorization unavailable"

EgressPrincipal = RunToken | ProbeToken
"""What a CONNECT presents itself as: a turn's run token, or one probe exec's own token. Both are
signed by the one deploy secret and name their own domain, so the wire cannot pass one as the
other."""

RuleResolver = Callable[["EgressPrincipal | None"], Awaitable[tuple[Rule, ...]]]
TurnAuthorizer = Callable[["RunToken"], Awaitable[bool]]
PublicAddressResolver = Callable[[str, int], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class _ProbeRuleKey:
    """What a probe's rule set actually depends on. A probe token is minted per exec, so keying the
    cache on the token itself would make every entry single-use — a monitor fleet would churn the
    shared cache and evict live turns to store rules nothing looks up again. Its `probe_id` and
    deadline reach no rule: the agent comes from the conversation and the forwards from the member,
    and both are here. Authorization is unaffected, being re-decided per CONNECT from the token."""

    workspace_id: UUID
    conversation_id: UUID
    acting_member_id: UUID | None


_RuleKey = RunToken | _ProbeRuleKey
"""What the rule cache is keyed on. A run token is its own key — workspace, turn and member are
exactly what its rules derive from."""


@dataclass(frozen=True, slots=True)
class _CachedRules:
    expires_at: float
    rules: tuple[Rule, ...]


@dataclass(frozen=True, slots=True)
class _EgressMeter:
    """One metered request, and who owes it: the workspace, and the turn that made it — None for a
    probe, which runs off every turn and bills its workspace directly."""

    workspace_id: UUID
    turn_id: UUID | None


@dataclass(frozen=True, slots=True)
class _TokenMeter:
    workspace_id: UUID
    turn_id: UUID
    model: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class _HeaderRefusal:
    status: HTTPStatus
    message: str


class _ContentDecoder(Protocol):
    @property
    def unconsumed_tail(self) -> bytes: ...

    def decompress(self, data: bytes, max_length: int = 0) -> bytes: ...

    def flush(self) -> bytes: ...


_MeterRecord = _EgressMeter | _TokenMeter


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


@dataclass(frozen=True, slots=True)
class _Authority:
    """Whose egress a principal carries: the agent whose rules derive, that agent's snapshotted
    internet policy, and the member whose private grants its CLI forwards may draw on."""

    agent_id: UUID
    internet_access_allowed: bool
    acting_member_id: UUID | None


@dataclass(frozen=True)
class PerAgentRules:
    """Resolve the proxy's rule set for one principal's agent, derived from its token each call: the
    workspace-wide model base, that workspace's own keyed-credential rules, and that agent's own
    OAuth grant rules. Per-agent authentication is the wire's isolation — agent A's turn resolves
    only A's grants, so A cannot inject or forward through another agent's account — and
    per-workspace resolution is the tenant's: a stored secret is read against the run token's own
    `workspace_id`, so one shared proxy injects for every workspace and none of them holds another's
    key. A run with no or unknown token yields the base alone; a resolution error raises to the
    proxy, which returns service unavailable without caching it — never a policy denial, broad
    allow, or another workspace's secret. Deriving each call (not once at boot) is the liveness: a
    grant recorded or a slot filled mid-serve is live for the next turn.

    A probe token resolves the same chain under the same agent, reached through its conversation
    rather than a turn, minus the deployment's model key."""

    base: tuple[Rule, ...]
    grants: GrantStore | None
    credentials: CredentialStore | None = None
    slots: tuple[CredentialSlot, ...] = ()
    internet: tuple[InternetRule, ...] = ()
    transfer_hosts: ConnectorTransferHosts = field(
        default_factory=lambda: ConnectorTransferHosts(explicit={})
    )
    clis: Mapping[str, CliCredential] = field(default_factory=dict)

    async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]:
        if principal is None:
            return self.base
        with ws(principal.workspace_id):
            match principal:
                case RunToken():
                    authority = await self._turn_of(principal)
                case ProbeToken():
                    authority = await self._conversation_of(principal)
            if authority is None:
                return self.base
            with agent(authority.agent_id):
                rules = (
                    (*self.base, *self.internet) if authority.internet_access_allowed else self.base
                )
                if self.credentials is not None and self.slots:
                    rules = (
                        *rules,
                        *await derive_credential_rules(
                            self.slots, principal.workspace_id, self.credentials
                        ),
                    )
                if self.grants is not None:
                    granted = await self.grants.active_grants()
                    rules = (
                        *rules,
                        *derive_grant_rules(granted, self.transfer_hosts),
                        *derive_cli_rules(granted, authority.acting_member_id, self.clis),
                    )
                if isinstance(principal, ProbeToken):
                    return self._without_the_model_key(rules)
                return rules

    async def _turn_of(self, run: RunToken) -> _Authority | None:
        """The turn's agent and snapshotted internet policy in one indexed read."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.agent_id,
                        tables.agent.c.internet_access_allowed,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.agent,
                            tables.agent.c.id == tables.turn.c.agent_id,
                        )
                    )
                    .where(
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.workspace_id == run.workspace_id,
                        tables.agent.c.workspace_id == run.workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return _Authority(row.agent_id, row.internet_access_allowed, run.acting_member_id)

    async def _conversation_of(self, probe: ProbeToken) -> _Authority | None:
        """The probed conversation's agent and snapshotted internet policy — the same two columns
        a turn's read answers, reached through the conversation because a probe names no turn. The
        member comes off the token rather than a row: whoever armed the work this exec serves, so a
        command that reached their own connected account in the arming turn keeps reaching it, the
        way a scheduled fire keeps its initiator's private connectors. A memberless probe forwards
        only what is shared with the workspace."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.agent_id,
                        tables.agent.c.internet_access_allowed,
                    )
                    .select_from(
                        tables.conversation.join(
                            tables.agent,
                            tables.agent.c.id == tables.conversation.c.agent_id,
                        )
                    )
                    .where(
                        tables.conversation.c.id == probe.conversation_id,
                        tables.conversation.c.workspace_id == probe.workspace_id,
                        tables.agent.c.workspace_id == probe.workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return _Authority(row.agent_id, row.internet_access_allowed, probe.acting_member_id)

    def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
        """`rules` minus the deployment's own model-key injection. A probe's environment exports
        no model sentinel, but a carrier's base environment does, so the withholding is made true
        at the enforcement point rather than left to what a sandbox happens to carry: a probe that
        reaches a model host reaches it unauthenticated. Everything else derives as a turn's does —
        the workspace's keyed credentials, so a `git fetch` probe still authenticates, and the
        agent's grants, so a `gh run view` probe still forwards through the broker."""
        return tuple(
            rule
            for rule in rules
            if not (isinstance(rule, InjectionRule) and SENTINEL_MODEL_KEY in rule.sentinel)
        )

    async def turn_live(self, run: RunToken) -> bool:
        """The egress-authorization gate: True only while the run token names a turn the DB still
        reports running. A keyed host's real-key injection is applied only for a live turn, so a
        token for a turn that has ended, a turn that never existed, or (checked before this) no
        token at all is denied at CONNECT and the key never reaches the wire. Read fresh per request
        — never the per-turn rule cache — so a turn that ends between requests can no longer draw
        the key, and it costs one indexed lookup on the turn's primary key."""
        with ws(run.workspace_id):
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
    run_tokens: RunTokenCodec
    resolve_public: PublicAddressResolver | None = None
    pricing: Pricing = CORE_PRICING
    _server: asyncio.Server | None = field(default=None, init=False)
    _workdir: tempfile.TemporaryDirectory | None = field(default=None, init=False)
    _contexts: dict[str, ssl.SSLContext] = field(default_factory=dict, init=False)
    _mint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _connection_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _active_connections: int = field(default=0, init=False)
    _workspace_connections: dict[UUID, int] = field(default_factory=dict, init=False)
    _meter_queue: asyncio.Queue[_MeterRecord | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=METER_QUEUE_MAX), init=False
    )
    _meter_worker: asyncio.Task[None] | None = field(default=None, init=False)
    _rule_cache: dict[_RuleKey, _CachedRules] = field(default_factory=dict, init=False)
    _rule_tasks: dict[_RuleKey, asyncio.Task[tuple[Rule, ...]]] = field(
        default_factory=dict, init=False
    )

    @property
    def probe_tokens(self) -> ProbeTokenCodec:
        """The codec for the other token class this proxy admits, derived from the run codec's
        secret rather than wired beside it: both are the one deploy secret's tokens, and a deploy
        that could wire them to different keys would sign probes nothing verifies."""
        return ProbeTokenCodec(secret=self.run_tokens.secret)

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
        self._server = await asyncio.start_server(
            self._handle, bind_host, port, limit=MAX_HEADER_BYTES + 1
        )
        bound = self._server.sockets[0].getsockname()[1]
        return ProxyEndpoint(port=bound, ca_cert=self.ca_cert, public_url=public_url)

    async def stop(self, graceful_shutdown_seconds: int = 0) -> None:
        """Close the listener, drain live connections for the grace window, cancel stragglers,
        then wait the server down — in that order, because `wait_closed()` blocks until every
        handler task finishes and would park an unbounded shutdown ahead of the bounded drain.
        A connection accepted just before the listener closed schedules its handler after the
        drain snapshot; `_handle` refuses service once the listener stops, so a late handler
        finishes immediately instead of parking `wait_closed()` past the window. Rule resolutions
        left after the connection drain are orphaned: they cancel immediately and are awaited
        against what the one window has left, never a second window of their own — a drain that
        already spent it leaves them pending rather than parking the shutdown behind them."""
        drain_deadline = time.monotonic() + graceful_shutdown_seconds
        if self._server is not None:
            self._server.close()
        if self._connection_tasks:
            _, pending = await asyncio.wait(
                self._connection_tasks, timeout=graceful_shutdown_seconds
            )
            for connection_task in pending:
                connection_task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        rule_tasks = tuple(self._rule_tasks.values())
        if rule_tasks:
            for rule_task in rule_tasks:
                rule_task.cancel()
            await asyncio.wait(rule_tasks, timeout=max(0.0, drain_deadline - time.monotonic()))
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None
        if self._meter_worker is not None:
            if self._meter_worker.done():
                await self._meter_worker
            else:
                await self._meter_queue.put(None)
                await self._meter_worker
            self._meter_worker = None
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One client connection: refuse anything that is not a proxy CONNECT, then tunnel it."""
        if self._server is None or not self._server.is_serving():
            writer.close()
            return
        if self._active_connections >= MAX_PROXY_CONNECTIONS:
            await _respond(writer, 503, "proxy connection capacity reached")
            writer.close()
            return
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("proxy connection has no asyncio task")
        workspace_connection: UUID | None = None
        self._active_connections += 1
        self._connection_tasks.add(task)
        try:
            try:
                request = await _read_request_head(reader)
                if isinstance(request, _HeaderRefusal):
                    await _respond(writer, request.status, request.message)
                    return
                if request is None:
                    return
                request_line, headers = request
                parts = request_line.decode("latin-1").split()
                if len(parts) != 3:
                    await _respond(writer, 400, "malformed proxy request line")
                    return
                method, target, _ = parts
                if method != "CONNECT":
                    await _respond(writer, 405, "only CONNECT is proxied")
                    return
                host, _, port_text = target.partition(":")
                proxy_auth = ""
                for line in headers:
                    name, _, value = line.decode(errors="replace").partition(":")
                    if name.strip().lower() == "proxy-authorization":
                        proxy_auth = value.strip()
            except OSError:
                return
            try:
                port = int(port_text or DEFAULT_HTTPS_PORT)
            except ValueError:
                await _respond(writer, 400, "invalid CONNECT port")
                return
            if not MIN_CONNECT_PORT <= port <= MAX_CONNECT_PORT:
                await _respond(writer, 400, "invalid CONNECT port")
                return
            principal = self._principal(proxy_auth)
            if principal is None:
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            try:
                authorized = await self._authorized(principal)
            except Exception:
                await _respond(writer, 503, EGRESS_AUTHORIZATION_UNAVAILABLE)
                return
            if not authorized:
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            workspace_connections = self._workspace_connections.get(principal.workspace_id, 0)
            if workspace_connections >= MAX_PROXY_CONNECTIONS_PER_WORKSPACE:
                await _respond(writer, 429, "workspace proxy connection capacity reached")
                return
            self._workspace_connections[principal.workspace_id] = workspace_connections + 1
            workspace_connection = principal.workspace_id
            try:
                rules = await self._rules_for(principal)
            except Exception:
                await _respond(writer, 503, EGRESS_AUTHORIZATION_UNAVAILABLE)
                return
            connect_host = host
            exactly_scoped = any(
                isinstance(rule, ScopeRule) and host in rule.allowed_hosts for rule in rules
            )
            if not exactly_scoped and not any(isinstance(rule, InternetRule) for rule in rules):
                await _respond(writer, 403, f"egress to {host} is not permitted")
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
                    reader, writer, host, port, principal, rules, connect_host=connect_host
                )
            else:
                await self._mitm(reader, writer, host, port, injections, forwards, principal, rules)
        finally:
            writer.close()
            if workspace_connection is not None:
                workspace_connections = self._workspace_connections[workspace_connection] - 1
                if workspace_connections == 0:
                    del self._workspace_connections[workspace_connection]
                else:
                    self._workspace_connections[workspace_connection] = workspace_connections
            self._active_connections -= 1
            self._connection_tasks.discard(task)

    async def _authorized(self, principal: EgressPrincipal) -> bool:
        """The egress-authorization gate, decided fresh per CONNECT. A run token is authorized
        while the DB still reports its turn running, so a key injected for a turn cannot be drawn
        once that turn ends. A probe token names no turn to read: it carries its own deadline, is
        authorized until that deadline passes, and nothing renews it — so a probe's egress can never
        outlive the one exec it was minted for, and the check costs a comparison, not a query."""
        match principal:
            case ProbeToken():
                return principal.expires_at > int(datetime.now(UTC).timestamp())
            case RunToken():
                return await self._turn_authorized(principal)

    async def _turn_authorized(self, run: RunToken) -> bool:
        """The turn-liveness gate, whose fault is recorded here and raised on. Both authorization
        faults — this and rule resolution — log once where they are raised, so `_handle` answers
        each the same way and never writes a second record for one fault."""
        try:
            return await self.authorize(run)
        except Exception as error:
            log_error(
                "egress.authorize_failed",
                workspace_id=str(run.workspace_id),
                turn=str(run.turn_id),
                error_class=type(error).__name__,
            )
            raise

    async def _rules_for(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]:
        """The resolved rule set for this principal's agent, bounded and refreshed before an
        injected short-lived credential can expire. Concurrent misses for one principal share one
        resolution. A
        resolution error fails closed with a service error, is not cached, and is recorded once by
        that shared resolution rather than once per connection waiting on it."""
        if principal is None:
            return await self.resolve(None)
        key = _rule_key(principal)
        hit = self._rule_cache.get(key)
        if hit is not None and hit.expires_at > time.monotonic():
            return hit.rules
        if hit is not None:
            del self._rule_cache[key]
        task = self._rule_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._resolve_rules(key, principal))
            task.add_done_callback(_read_fault)
            self._rule_tasks[key] = task
        return await asyncio.shield(task)

    async def _resolve_rules(self, key: _RuleKey, principal: EgressPrincipal) -> tuple[Rule, ...]:
        task = asyncio.current_task()
        try:
            try:
                rules = await self.resolve(principal)
            except Exception as error:
                log_error(
                    "egress.resolve_failed",
                    workspace_id=str(principal.workspace_id),
                    turn="" if isinstance(principal, ProbeToken) else str(principal.turn_id),
                    error_class=type(error).__name__,
                )
                raise
            if len(self._rule_cache) >= RULE_CACHE_MAX:
                del self._rule_cache[next(iter(self._rule_cache))]
            self._rule_cache[key] = _CachedRules(
                expires_at=time.monotonic() + RULE_CACHE_TTL_SECONDS, rules=rules
            )
            return rules
        finally:
            if self._rule_tasks.get(key) is task:
                del self._rule_tasks[key]

    def _principal(self, proxy_auth: str) -> EgressPrincipal | None:
        """The signed identity this CONNECT carries, or None when it carries none this deployment
        made. Each codec refuses the other's domain, so a run token presented where a probe token
        would be — or either one forged — is no principal at all and reaches no host."""
        if not proxy_auth:
            return None
        try:
            return self.run_tokens.from_proxy_auth(proxy_auth)
        except ValueError:
            pass
        try:
            return self.probe_tokens.from_proxy_auth(proxy_auth)
        except ValueError:
            return None

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
        principal: EgressPrincipal,
        rules: tuple[Rule, ...],
        connect_host: str,
    ) -> None:
        """An admitted host with no key to inject (a grant holds its token server-side): relay bytes
        opaquely, never terminating TLS. A MeterRule host is counted once the tunnel is
        established — the proxy cannot see individual requests inside the opaque TLS, so egress to a
        granted host is metered at CONNECT granularity, the metric and the `egress` ledger row
        written off the relay path against whichever identity the principal names. A connection that
        never opens (502) is not counted."""
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
        await self._meter_ledger(host, principal, rules)
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _mitm(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
        injections: list[InjectionRule],
        forwards: list[ForwardRule],
        principal: EgressPrincipal,
        rules: tuple[Rule, ...],
    ) -> None:
        """Terminate the sandbox's TLS with a minted leaf and dispatch on what the request carries:
        a grant sentinel in a ForwardRule's header executes through that grant's broker (the
        credential is injected server-side, never here); otherwise the sentinel Authorization value
        is swapped for its account's real key (selected among this host's injections by the exact
        sentinel) and the request re-originates upstream over verified TLS — the response streams
        straight back. A sandbox that stalls or vanishes during the handshake or before sending its
        request ends the exchange silently; internal faults before the handshake still propagate."""
        leaf_context = await self._leaf_context(host)
        try:
            client_reader, client_writer = await _start_tls_server(reader, writer, leaf_context)
            request = await _read_request_head(client_reader)
        except (OSError, ssl.SSLError):
            return
        if isinstance(request, _HeaderRefusal):
            await _respond(client_writer, request.status, request.message)
            return
        if request is None:
            return
        matched = _forward_match(request[1], forwards)
        if matched is not None:
            await self._forward_broker(
                client_reader, client_writer, matched, request, host, principal, rules
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
        await self._meter_ledger(host, principal, rules)
        tokens_metered = any(
            isinstance(rule, MeterRule) and rule.host == host and rule.dimension == TOKENS_DIMENSION
            for rule in rules
        )
        if not tokens_metered:
            await _relay(client_reader, client_writer, upstream_reader, upstream_writer)
            return
        accumulator = HttpTokenUsage(host)
        await _relay(
            client_reader, client_writer, upstream_reader, upstream_writer, accumulator.feed
        )
        await self._meter_tokens(principal, accumulator)

    async def _forward_broker(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        rule: ForwardRule,
        request: tuple[bytes, list[bytes]],
        host: str,
        principal: EgressPrincipal,
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
        if isinstance(body, _Refusal):
            log("egress.forward_refused", host=host, status=int(body.status), reason=body.message)
            await _respond(client_writer, body.status, body.message)
            await _drain_refused_body(client_reader, body.pending)
            return
        self._meter(host, rules)
        await self._meter_ledger(host, principal, rules)
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
        """The TLS context this proxy answers a CONNECT for `host` with, minting the leaf once.

        The host is a string the sandbox sent, so it names none of these files directly: it is cut
        to one filename component, and the extension file is written through the containment guard,
        which stages `O_CREAT|O_EXCL|O_NOFOLLOW` under the proxy's own workdir. A rule host carrying
        a `/` or a `..` would otherwise have named openssl's `-out` — the equality match against a
        rule host is an allowlist, not a path check."""
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
            stem = f"{LEAF_NAME_PREFIX}{contained_leaf(host, LEAF_UNNAMED_HOST)}"
            cert_path = root / f"{stem}.crt"
            csr_path = root / f"{stem}.csr"
            with contained_file(f"{stem}.ext", root) as extensions:
                extensions.replace_text(
                    f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n", LEAF_FILE_MODE
                )
                ext_path = extensions.path
            await _openssl(
                "req",
                "-new",
                "-key",
                str(root / "leaf.key"),
                "-subj",
                f"/CN={host}",
                "-out",
                str(csr_path),
            )
            await _openssl(
                "x509",
                "-req",
                "-in",
                str(csr_path),
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

    async def _meter_ledger(
        self, host: str, principal: EgressPrincipal, rules: tuple[Rule, ...]
    ) -> None:
        if not any(
            isinstance(rule, MeterRule) and rule.host == host and rule.dimension != TOKENS_DIMENSION
            for rule in rules
        ):
            return
        turn_id = principal.turn_id if isinstance(principal, RunToken) else None
        await self._enqueue_meter(_EgressMeter(principal.workspace_id, turn_id))

    async def _meter_tokens(
        self, principal: EgressPrincipal, accumulator: "HttpTokenUsage"
    ) -> None:
        """Bill the model call this exchange carried to its turn. A probe cannot have made one: it
        exports no model sentinel and resolves no model-key injection, so its request to a model
        host goes upstream unauthenticated and reports no usage. Reaching here with a probe would
        mean the deployment's key was spent off every turn, which is recorded rather than billed to
        a turn that does not exist."""
        if isinstance(principal, ProbeToken):
            warn(
                "egress.probe_model_usage",
                host=accumulator.host,
                probe_id=str(principal.probe_id),
            )
            return
        parsed = accumulator.usage()
        if parsed is None:
            log("egress.tokens_usage_absent", host=accumulator.host)
            return
        model, usage = parsed
        await self._enqueue_meter(
            _TokenMeter(principal.workspace_id, principal.turn_id, model, usage)
        )

    async def _enqueue_meter(self, record: _MeterRecord) -> None:
        worker = self._meter_worker
        if worker is None:
            worker = asyncio.create_task(self._meter_loop())
            self._meter_worker = worker
        elif worker.done():
            await worker
        await self._meter_queue.put(record)

    async def _meter_loop(self) -> None:
        while True:
            first = await self._meter_queue.get()
            records: list[_MeterRecord] = []
            batch_items = 1
            stopping = first is None
            if first is not None:
                records.append(first)
            await asyncio.sleep(METER_BATCH_WINDOW_SECONDS)
            while batch_items < METER_BATCH_MAX:
                try:
                    record = self._meter_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                batch_items += 1
                if record is None:
                    stopping = True
                else:
                    records.append(record)
            try:
                if records:
                    await self._write_meter_batch(records)
            except Exception as error:
                log_error(
                    "egress.meter_batch_failed",
                    records=len(records),
                    error_class=type(error).__name__,
                )
            finally:
                for _ in range(batch_items):
                    self._meter_queue.task_done()
            if stopping:
                return

    async def _write_meter_batch(self, records: list[_MeterRecord]) -> None:
        egress: dict[tuple[UUID, UUID | None], int] = {}
        tokens: dict[tuple[UUID, UUID | None], dict[str, Usage]] = {}
        record_counts: dict[tuple[UUID, UUID | None], int] = {}
        for record in records:
            billed = (record.workspace_id, record.turn_id)
            match record:
                case _EgressMeter():
                    egress[billed] = egress.get(billed, 0) + 1
                case _TokenMeter(model=model, usage=usage):
                    billed_tokens = tokens.setdefault(billed, {})
                    previous = billed_tokens.get(model, Usage())
                    billed_tokens[model] = Usage(
                        input_tokens=previous.input_tokens + usage.input_tokens,
                        output_tokens=previous.output_tokens + usage.output_tokens,
                        cache_read_tokens=previous.cache_read_tokens + usage.cache_read_tokens,
                        cache_write_5m_tokens=(
                            previous.cache_write_5m_tokens + usage.cache_write_5m_tokens
                        ),
                        cache_write_1h_tokens=(
                            previous.cache_write_1h_tokens + usage.cache_write_1h_tokens
                        ),
                    )
            record_counts[billed] = record_counts.get(billed, 0) + 1
        for (workspace_id, turn_id), count in record_counts.items():
            try:
                with ws(workspace_id):
                    async with workspace_tx() as connection:
                        await self._write_billed(
                            connection,
                            workspace_id,
                            turn_id,
                            egress.get((workspace_id, turn_id)),
                            tokens.get((workspace_id, turn_id), {}),
                        )
            except Exception as error:
                log_error(
                    "egress.meter_run_failed",
                    workspace_id=str(workspace_id),
                    turn_id="" if turn_id is None else str(turn_id),
                    records=count,
                    error_class=type(error).__name__,
                )

    async def _write_billed(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID | None,
        requests: int | None,
        tokens: dict[str, Usage],
    ) -> None:
        """One billed identity's rows for this batch. A turn-less identity is a probe: it owes its
        request count on a NULL-turn `egress` row and never model tokens, which `_meter_tokens`
        refuses to enqueue for it — so the shape holds here as well as there."""
        if turn_id is None:
            if requests is not None:
                await record_probe_egress_request(connection, workspace_id, amount=requests)
            return
        if requests is not None:
            await record_egress_request(connection, workspace_id, turn_id, amount=requests)
        for model, usage in tokens.items():
            await record_sandbox_tokens(
                connection, workspace_id, turn_id, model, usage, self.pricing
            )


def _rule_key(principal: EgressPrincipal) -> _RuleKey:
    """The cache key for one principal's rule set: a run token keys on itself, a probe on the three
    things its rules derive from, so two execs of one watch share the resolution their tokens would
    otherwise each pay for."""
    match principal:
        case RunToken():
            return principal
        case ProbeToken():
            return _ProbeRuleKey(
                workspace_id=principal.workspace_id,
                conversation_id=principal.conversation_id,
                acting_member_id=principal.acting_member_id,
            )


def _read_fault(task: asyncio.Task[tuple[Rule, ...]]) -> None:
    """Read the fault of a rule resolution whose every waiter cancelled. Left unread, asyncio
    reports the abandoned task on its own logger with the exception rendered in full, and that text
    leaves through the root-logger OTLP bridge, which redacts by field name and never scans a
    message. Waiters that remain still see the fault raised as their own."""
    if not task.cancelled():
        task.exception()


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
) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None:
    try:
        async with asyncio.timeout(PROXY_HEADER_TIMEOUT_SECONDS):
            request_line = await reader.readline()
            if not request_line:
                return None
            headers: list[bytes] = []
            total = len(request_line)
            if total > MAX_HEADER_BYTES:
                return _HeaderRefusal(
                    HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                    "request headers exceed the proxy limit",
                )
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b""):
                    break
                total += len(line)
                if total > MAX_HEADER_BYTES:
                    return _HeaderRefusal(
                        HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                        "request headers exceed the proxy limit",
                    )
                headers.append(line)
    except TimeoutError:
        return _HeaderRefusal(HTTPStatus.REQUEST_TIMEOUT, "request headers timed out")
    except ValueError:
        return _HeaderRefusal(
            HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
            "request headers exceed the proxy limit",
        )
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


@dataclass(frozen=True)
class _Refusal:
    """Why a forwarded request body was not read, and an upper bound on the body bytes still
    inbound: the declared remainder when content-length named it, the drain cap when the length is
    undeclared or untrusted (chunked, unparseable, negative — decided from the header alone, so the
    wire is not known to be dry), zero only when the reader already saw EOF. The client is
    mid-upload when the refusal is decided, so it cannot read the answer until it finishes
    writing — `pending` is what the proxy drains and discards to let it get there."""

    status: HTTPStatus
    message: str
    pending: int


async def _read_request_body(
    reader: asyncio.StreamReader, headers: list[bytes]
) -> bytes | _Refusal:
    """The whole body of a broker-forwarded request, bounded next to the one external call that
    sends it — the broker takes an enveloped API request, never a stream. A chunked, over-bound,
    negative-length, or truncated body answers a `_Refusal` naming that exact condition and its
    numbers, never a silent clip and never one status standing for four causes — the size ceiling is
    otherwise discoverable only by bisection. A negative length is caught here because it would
    otherwise reach `readexactly`, whose ValueError escapes the caller's IncompleteReadError guard
    and drops the connection with no response."""
    length = 0
    for line in headers:
        name, _, value = line.partition(b":")
        match name.strip().lower():
            case b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    return _Refusal(
                        HTTPStatus.LENGTH_REQUIRED,
                        "forwarded request declares an unparseable content-length",
                        MAX_REFUSAL_DRAIN_BYTES,
                    )
            case b"transfer-encoding":
                return _Refusal(
                    HTTPStatus.LENGTH_REQUIRED,
                    "forwarded request body must declare a content-length; chunked is not forwarded"
                    " — the broker takes one enveloped request, never a stream",
                    MAX_REFUSAL_DRAIN_BYTES,
                )
    if length == 0:
        return b""
    if length < 0:
        return _Refusal(
            HTTPStatus.BAD_REQUEST,
            f"forwarded request declares a negative content-length {length}",
            MAX_REFUSAL_DRAIN_BYTES,
        )
    if length > MAX_FORWARD_BODY_BYTES:
        return _Refusal(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"forwarded request body is {length} bytes, over the"
            f" {MAX_FORWARD_BODY_BYTES} byte limit — a base64 payload inflates by 4/3, so the"
            " raw ceiling is lower",
            length,
        )
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as error:
        return _Refusal(
            HTTPStatus.BAD_REQUEST,
            f"forwarded request body ended after {len(error.partial)} of {length} bytes",
            0,
        )


async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None:
    """Discard the body a refused client is still writing, so its send completes and it reads the
    refusal already on the wire instead of failing on a socket the proxy closed under it — the
    difference between a 413 that names the limit and `use of closed network connection`. Bytes are
    discarded as they arrive, never buffered, and the drain is bounded three ways: past
    `MAX_REFUSAL_DRAIN_BYTES`, past `REFUSAL_DRAIN_TIMEOUT_SECONDS` of a stalled peer, or at the
    client's own close, the connection closes and the client's own write error stands — a peer that
    stops sending without closing cannot park this task past the deadline."""
    remaining = min(pending, MAX_REFUSAL_DRAIN_BYTES)
    try:
        async with asyncio.timeout(REFUSAL_DRAIN_TIMEOUT_SECONDS):
            while remaining > 0:
                chunk = await reader.read(min(remaining, RELAY_CHUNK_BYTES))
                if not chunk:
                    return
                remaining -= len(chunk)
    except TimeoutError:
        return


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


def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes:
    """Rewrite the header block: swap a header the sandbox set to a candidate's sentinel for that
    candidate's real secret. Authorization clients may add `token` or `Bearer` before the sentinel;
    the exact sentinel still selects the credential. A foreign or absent sentinel passes upstream
    unchanged. Force `Connection: close` so each request is a fresh MITM that re-applies the
    swap."""
    rebuilt = bytearray()
    for line in headers:
        field_name, _, value = line.partition(b":")
        name = field_name.strip().lower()
        if name in (b"connection", b"proxy-connection"):
            continue
        supplied = value.strip()
        parts = supplied.split(maxsplit=1)
        chosen = next(
            (
                c
                for c in candidates
                if name == c.header.encode().lower()
                and (
                    supplied == c.sentinel.encode()
                    or (
                        len(parts) == 2
                        and parts[0].lower() in (b"token", b"bearer")
                        and parts[1] == c.sentinel.encode()
                    )
                )
            ),
            None,
        )
        if chosen is not None:
            real = chosen.real.encode()
            if len(parts) == 2 and b" " not in real:
                real = parts[0] + b" " + real
            rebuilt += chosen.header.encode() + b": " + real + b"\r\n"
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
    """Keep a progressing response alive after request EOF.

    on_downstream receives each response chunk after it is forwarded.
    """
    downstream_progress = asyncio.Event()
    down = asyncio.create_task(
        _pump(upstream_reader, client_writer, on_downstream, downstream_progress.set)
    )
    down.add_done_callback(lambda _task: downstream_progress.set())
    up = asyncio.create_task(_pump(client_reader, upstream_writer))
    try:
        completed, _ = await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
        if up in completed and down not in completed:
            if upstream_writer.can_write_eof():
                upstream_writer.write_eof()
            while not down.done():
                downstream_progress.clear()
                try:
                    async with asyncio.timeout(RELAY_RESPONSE_IDLE_TIMEOUT_SECONDS):
                        await downstream_progress.wait()
                except TimeoutError:
                    break
            if down.done():
                await down
    finally:
        for task in (down, up):
            task.cancel()
        upstream_writer.close()


async def _pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    on_chunk: Callable[[bytes], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> None:
    try:
        while chunk := await reader.read(RELAY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
            if on_progress is not None:
                on_progress()
            if on_chunk is not None:
                on_chunk(chunk)
    except (OSError, asyncio.CancelledError):
        pass


def _int_field(usage: dict[str, object], name: str) -> int:
    value = usage.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _cached_field(usage: dict[str, object], details_name: str) -> int:
    details = usage.get(details_name)
    return _int_field(details, "cached_tokens") if isinstance(details, dict) else 0


@dataclass
class HttpTokenUsage:
    """Decode one HTTP response and recover model-reported usage from its SSE or JSON body."""

    host: str
    _head: bytearray = field(default_factory=bytearray, init=False)
    _body: bytearray = field(default_factory=bytearray, init=False)
    _chunk_buffer: bytearray = field(default_factory=bytearray, init=False)
    _headers_complete: bool = field(default=False, init=False)
    _chunked: bool = field(default=False, init=False)
    _chunk_remaining: int | None = field(default=None, init=False)
    _chunk_needs_crlf: bool = field(default=False, init=False)
    _chunk_done: bool = field(default=False, init=False)
    _decompressor: _ContentDecoder | None = field(default=None, init=False)
    _decoder_finished: bool = field(default=False, init=False)
    _overflowed: bool = field(default=False, init=False)
    _seen: bool = field(default=False, init=False)
    _model: str = field(default="", init=False)
    _input: int = field(default=0, init=False)
    _output: int = field(default=0, init=False)
    _cache_read: int = field(default=0, init=False)
    _cache_write_5m: int = field(default=0, init=False)
    _cache_write_1h: int = field(default=0, init=False)

    def feed(self, chunk: bytes) -> None:
        if self._overflowed:
            return
        if self._headers_complete:
            self._feed_wire_body(chunk)
            return
        self._head.extend(chunk)
        marker = self._head.find(b"\r\n\r\n")
        if marker == -1:
            if len(self._head) > MAX_HEADER_BYTES:
                self._fail()
            return
        if marker > MAX_HEADER_BYTES:
            self._fail()
            return
        raw_head = bytes(self._head[:marker])
        body = bytes(self._head[marker + 4 :])
        self._head.clear()
        headers: dict[bytes, bytes] = {}
        for line in raw_head.split(b"\r\n")[1:]:
            name, separator, value = line.partition(b":")
            if separator:
                headers[name.strip().lower()] = value.strip().lower()
        transfer = headers.get(b"transfer-encoding", b"")
        self._chunked = b"chunked" in (part.strip() for part in transfer.split(b","))
        content_encoding = headers.get(b"content-encoding", b"identity")
        match content_encoding:
            case b"identity" | b"":
                pass
            case b"gzip":
                self._decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
            case b"deflate":
                self._decompressor = zlib.decompressobj()
            case _:
                self._fail()
                return
        self._headers_complete = True
        self._feed_wire_body(body)

    def usage(self) -> tuple[str, Usage] | None:
        self._finish_decoder()
        if not self._seen:
            self._maybe_json_body(bytes(self._body).strip())
        if not self._seen:
            return None
        return self._model, Usage(
            input_tokens=self._input,
            output_tokens=self._output,
            cache_read_tokens=self._cache_read,
            cache_write_5m_tokens=self._cache_write_5m,
            cache_write_1h_tokens=self._cache_write_1h,
        )

    def _feed_wire_body(self, chunk: bytes) -> None:
        if self._chunk_done:
            return
        if not self._chunked:
            self._decode(chunk)
            return
        self._chunk_buffer.extend(chunk)
        while not self._chunk_done:
            if self._chunk_remaining is None:
                marker = self._chunk_buffer.find(b"\r\n")
                if marker == -1:
                    if len(self._chunk_buffer) > MAX_HEADER_BYTES:
                        self._fail()
                    return
                size = bytes(self._chunk_buffer[:marker]).split(b";", 1)[0]
                del self._chunk_buffer[: marker + 2]
                try:
                    self._chunk_remaining = int(size, 16)
                except ValueError:
                    self._fail()
                    return
                if self._chunk_remaining < 0:
                    self._fail()
                    return
                if self._chunk_remaining == 0:
                    self._chunk_done = True
                    self._finish_decoder()
                    return
            if self._chunk_remaining > 0:
                consumed = min(self._chunk_remaining, len(self._chunk_buffer))
                if consumed == 0:
                    return
                payload = bytes(self._chunk_buffer[:consumed])
                del self._chunk_buffer[:consumed]
                self._chunk_remaining -= consumed
                self._decode(payload)
                if self._chunk_remaining > 0:
                    return
                self._chunk_needs_crlf = True
            if not self._chunk_needs_crlf or len(self._chunk_buffer) < 2:
                return
            if self._chunk_buffer[:2] != b"\r\n":
                self._fail()
                return
            del self._chunk_buffer[:2]
            self._chunk_remaining = None
            self._chunk_needs_crlf = False

    def _decode(self, chunk: bytes) -> None:
        if self._decompressor is None:
            self._feed_body(chunk)
            return
        pending = chunk
        try:
            while pending and not self._overflowed:
                limit = max(1, MAX_SSE_BUFFER_BYTES - len(self._body) + 1)
                decoded = self._decompressor.decompress(pending, limit)
                tail = self._decompressor.unconsumed_tail
                if not decoded and len(tail) == len(pending):
                    self._fail()
                    return
                self._feed_body(decoded)
                pending = tail
        except zlib.error:
            self._fail()

    def _finish_decoder(self) -> None:
        if self._decoder_finished or self._overflowed:
            return
        self._decoder_finished = True
        if self._decompressor is None:
            return
        try:
            self._feed_body(self._decompressor.flush())
        except zlib.error:
            self._fail()

    def _feed_body(self, chunk: bytes) -> None:
        if self._overflowed:
            return
        self._body.extend(chunk)
        consumed = 0
        while (newline := self._body.find(b"\n", consumed)) != -1:
            self._consume(bytes(self._body[consumed:newline]))
            consumed = newline + 1
        if consumed:
            del self._body[:consumed]
        if len(self._body) > MAX_SSE_BUFFER_BYTES:
            self._fail()

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
            creation = usage.get("cache_creation")
            if isinstance(creation, dict):
                self._cache_write_5m = _int_field(creation, "ephemeral_5m_input_tokens")
                self._cache_write_1h = _int_field(creation, "ephemeral_1h_input_tokens")
            else:
                self._cache_write_5m = _int_field(usage, "cache_creation_input_tokens")
        output = usage.get("output_tokens")
        if isinstance(output, int) and not isinstance(output, bool):
            self._output = output
        self._seen = True

    def _openai(self, event: dict[str, object]) -> None:
        """Both OpenAI surfaces report one usage block, under their own names: Chat Completions
        calls it prompt/completion with `prompt_tokens_details`, Responses calls it input/output
        with `input_tokens_details`. A usage object that answers to neither is a shape this parser
        does not know, and a billable call must never fall to zero without a trace."""
        model = event.get("model")
        if isinstance(model, str):
            self._model = model
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return
        if "prompt_tokens" in usage:
            self._absorb_openai(
                _int_field(usage, "prompt_tokens"),
                _int_field(usage, "completion_tokens"),
                _cached_field(usage, "prompt_tokens_details"),
            )
        elif "input_tokens" in usage:
            self._absorb_openai(
                _int_field(usage, "input_tokens"),
                _int_field(usage, "output_tokens"),
                _cached_field(usage, "input_tokens_details"),
            )
        else:
            log("egress.tokens_usage_unparsed", host=self.host)

    def _absorb_openai(self, prompt: int, output: int, cached: int) -> None:
        """An OpenAI prompt count is INCLUSIVE of the cached prefix, so the cached share is
        subtracted out and carried as the cache-read dimension — the same normalization the
        host-side adapters in `ufo.models.openai` apply, so a call the sandbox makes is priced
        exactly as the host would price it. Where those adapters refuse a cached count above the
        prompt it is part of, this parser only reads along a response the sandbox client is already
        receiving: raising here would break a call that succeeded, over a billing reading. The
        cached share is clamped to the prompt instead — never a negative fresh-input count — and the
        reading is reported, so an impossible split is not billed without a trace."""
        if cached > prompt:
            log("egress.tokens_cached_over_prompt", host=self.host, prompt=prompt, cached=cached)
            cached = prompt
        self._input = prompt - cached
        self._output = output
        self._cache_read = cached
        self._seen = True

    def _fail(self) -> None:
        self._overflowed = True
        self._head.clear()
        self._body.clear()
        self._chunk_buffer.clear()


async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    """Write a terminal refusal to the client: the standard reason phrase on the status line and the
    proxy's own reason as a plain-text body. A reason carried only in the phrase reaches almost no
    caller — an HTTP client surfaces the status and the body, so a refusal written that way is
    indistinguishable from a bare status. `content-length` and `connection: close` frame it, so the
    client never reads on for a body that will not come. A peer that vanished before reading it is
    routine — the connection is closing either way."""
    body = message.encode()
    try:
        writer.write(
            f"HTTP/1.1 {int(status)} {HTTPStatus(status).phrase}\r\n"
            "content-type: text/plain; charset=utf-8\r\n"
            f"content-length: {len(body)}\r\n"
            "connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
    except OSError:
        pass
