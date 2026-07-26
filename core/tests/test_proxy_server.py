import asyncio
import base64
import json
import os
import socket
import ssl
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import UUID, uuid4

import dns.asyncresolver
import pytest
import sqlalchemy as sa
from cryptography import x509
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialStore, HostChoice
from ufo.db import workspace_tx
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.grants import GrantStore, grant_sentinel
from ufo.loop.queue import GIT_PROXY_AUTH_ENV
from ufo.sandbox.fs_creds import (
    SANDBOX_FS_CREDENTIAL_PATH,
    SandboxFsCredentialMinter,
    SandboxFsCredentials,
)
from ufo.sandbox.proxy.rules import (
    OPENAI_HOST,
    REQUEST_METER_DIMENSION,
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    derive_manifest_rules,
)
from ufo.sandbox.proxy.server import (
    EgressProxy,
    PerAgentRules,
    SseTokenUsage,
    _forward_match,
    _forward_response_bytes,
    _inject,
    _read_request_body,
    _relay,
    generate_ca,
)
from ufo.sandbox.session import RunToken
from ufo.schema import tables
from ufo.schema.records import Usage

SEARCH_HOST = "api.search.test"
MODEL_HOST = "api.anthropic.com"

FULL_TOKEN_USAGE = Usage(
    input_tokens=1000, output_tokens=2000, cache_read_tokens=3000, cache_write_tokens=4000
)
ANTHROPIC_SSE = (
    b"event: message_start\r\n"
    b'data: {"type":"message_start","message":{"id":"m","model":"claude-opus-4-8",'
    b'"usage":{"input_tokens":1000,"cache_read_input_tokens":3000,'
    b'"cache_creation_input_tokens":4000,"output_tokens":1}}}\r\n\r\n'
    b"event: content_block_delta\r\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"hi"}}\r\n\r\n'
    b"event: message_delta\r\n"
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":2000}}\r\n\r\n'
    b"event: message_stop\r\n"
    b'data: {"type":"message_stop"}\r\n\r\n'
)
OPENAI_SSE = (
    b'data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.4",'
    b'"choices":[{"delta":{"content":"hi"}}],"usage":null}\n\n'
    b'data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.4","choices":[],'
    b'"usage":{"prompt_tokens":1000000,"completion_tokens":1000000,"total_tokens":2000000}}\n\n'
    b"data: [DONE]\n\n"
)
ANTHROPIC_JSON_BODY = (
    b"HTTP/1.1 200 OK\r\n"
    b"content-type: application/json\r\n"
    b"\r\n"
    b'{"id":"msg","type":"message","role":"assistant","model":"claude-opus-4-8",'
    b'"content":[{"type":"text","text":"hi"}],"stop_reason":"end_turn",'
    b'"usage":{"input_tokens":1000,"cache_read_input_tokens":3000,'
    b'"cache_creation_input_tokens":4000,"output_tokens":2000}}'
)
OPENAI_JSON_BODY = (
    b"HTTP/1.1 200 OK\r\n"
    b"content-type: application/json\r\n"
    b"\r\n"
    b'{"id":"c","object":"chat.completion","model":"gpt-5.4",'
    b'"choices":[{"message":{"role":"assistant","content":"hi"}}],'
    b'"usage":{"prompt_tokens":1000000,"completion_tokens":1000000,"total_tokens":2000000}}'
)


def _fixed(rules: tuple = ()) -> PerAgentRules:
    """The real resolver with no grant store: every run resolves to this fixed base — a genuine
    (degenerate) resolution, not a fake, standing in where a test drives paths other than grants."""
    return PerAgentRules(base=rules, grants=None)


def _egress(resolver: PerAgentRules, ca_cert: str = "x", ca_key: str = "x") -> EgressProxy:
    """Wire the proxy from a real resolver: its `resolve` for rules and its `turn_live` for the
    keyed-host liveness gate — both genuine `PerAgentRules` methods, never a fake."""
    return EgressProxy(
        resolve=resolver.resolve, authorize=resolver.turn_live, ca_cert=ca_cert, ca_key=ca_key
    )


async def _proxy() -> EgressProxy:
    cert, key = await generate_ca()
    proxy = _egress(_fixed(), ca_cert=cert, ca_key=key)
    await proxy.start(bind_host="127.0.0.1")
    return proxy


def _basic(run_token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_token}:".encode()).decode()


async def _connect(port: int, host: str, run_token: str = "", target_port: int | str = 443) -> int:
    """Drive one CONNECT through the proxy over its bound socket and return the status code,
    draining to EOF so any off-relay metering the exchange schedules is queued before the caller
    stops the proxy."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"CONNECT {host}:{target_port} HTTP/1.1\r\nHost: {host}\r\n"
    if run_token:
        head += f"Proxy-Authorization: {_basic(run_token)}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    status_line = await reader.readline()
    await reader.read()
    writer.close()
    return int(status_line.split()[1])


async def _get(port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nhost: localhost\r\n\r\n".encode())
    await writer.drain()
    head, _, body = (await reader.read()).partition(b"\r\n\r\n")
    writer.close()
    return int(head.split()[1]), body


async def test_stop_waits_for_active_connection_tasks() -> None:
    proxy = _egress(_fixed())
    release = asyncio.Event()
    connection = asyncio.create_task(release.wait())
    proxy._connection_tasks.add(connection)
    stopping = asyncio.create_task(proxy.stop(graceful_shutdown_seconds=1))
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping


async def test_handle_refuses_connections_once_the_listener_closed() -> None:
    """The accept race: a connection accepted just before the listener closes schedules its
    handler after stop's drain snapshot, invisible to the timed wait and the cancel sweep. Entry
    refuses service the moment the listener stops, so a late handler finishes at once instead of
    parking `wait_closed()` beyond the drain window."""
    proxy = _egress(_fixed())
    await proxy.start(bind_host="127.0.0.1")
    assert proxy._server is not None
    proxy._server.close()
    ours, theirs = socket.socketpair()
    reader, writer = await asyncio.open_connection(sock=ours)
    await proxy._handle(reader, writer)
    assert not proxy._connection_tasks
    theirs.close()
    await proxy.stop(graceful_shutdown_seconds=0)


async def test_stop_cancels_a_started_servers_live_connection() -> None:
    """A handler parked on a live socket keeps `wait_closed()` pending forever, so stop must
    drain and cancel connections before waiting the server down — not after."""
    proxy = _egress(_fixed())
    endpoint = await proxy.start(bind_host="127.0.0.1")
    _, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
    for _ in range(100):
        if proxy._connection_tasks:
            break
        await asyncio.sleep(0)
    assert proxy._connection_tasks
    await asyncio.wait_for(proxy.stop(graceful_shutdown_seconds=0), timeout=5)
    assert not proxy._connection_tasks
    writer.close()


async def test_workspace_credential_endpoint_serves_only_while_the_turn_is_live(db: None) -> None:
    class Sts:
        async def assume_role(
            self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int
        ) -> dict[str, object]:
            return {
                "Credentials": {
                    "AccessKeyId": "AKIA",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                    "Expiration": datetime(2026, 1, 2, tzinfo=UTC),
                }
            }

    now = datetime(2026, 1, 1, tzinfo=UTC)
    minter = SandboxFsCredentialMinter(
        sts=Sts(),
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket="ufo-blobs",
        s3_url="https://s3.test",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
        now=lambda: now,
    )
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    live_run = RunToken(workspace_id=seeded.workspace_id, turn_id=seeded.turn_id)
    token = minter.issue(seeded.conversation_id, live_run)

    cert, key = await generate_ca()
    resolver = _fixed()
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        workspace_credentials=minter.refresh,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        status, body = await _get(endpoint.port, f"{SANDBOX_FS_CREDENTIAL_PATH}{token}")
        refused, _ = await _get(endpoint.port, f"{SANDBOX_FS_CREDENTIAL_PATH}{token}x")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(status="done", terminal={"status": "done"})
                .where(tables.turn.c.id == seeded.turn_id)
            )
        ended_status, _ = await _get(endpoint.port, f"{SANDBOX_FS_CREDENTIAL_PATH}{token}")
    finally:
        await proxy.stop()

    assert status == 200
    assert json.loads(body) == {
        "AccessKeyId": "AKIA",
        "SecretAccessKey": "secret",
        "Token": "token",
        "Expiration": "2026-01-02T00:00:00Z",
        "RoleArn": "arn:aws:iam::0:role/sbxfs",
    }
    assert refused == 403
    assert ended_status == 403


@pytest.mark.parametrize(
    ("path", "wired"),
    [
        ("/other", True),
        (SANDBOX_FS_CREDENTIAL_PATH, True),
        (f"{SANDBOX_FS_CREDENTIAL_PATH}signed/extra", True),
        (f"{SANDBOX_FS_CREDENTIAL_PATH}signed", False),
    ],
)
async def test_workspace_credential_endpoint_rejects_non_token_paths(
    path: str, wired: bool
) -> None:
    async def unused(token: str, _authorize: object) -> SandboxFsCredentials:
        raise AssertionError(token)

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=_fixed().resolve,
        authorize=_fixed().turn_live,
        ca_cert=cert,
        ca_key=key,
        workspace_credentials=unused if wired else None,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        status, _ = await _get(endpoint.port, path)
    finally:
        await proxy.stop()

    assert status == 404


async def test_workspace_credential_validation_failure_is_a_mint_failure() -> None:
    async def refresh(token: str, _authorize: object) -> SandboxFsCredentials:
        return SandboxFsCredentials.model_validate({})

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=_fixed().resolve,
        authorize=_fixed().turn_live,
        ca_cert=cert,
        ca_key=key,
        workspace_credentials=refresh,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        status, body = await _get(endpoint.port, f"{SANDBOX_FS_CREDENTIAL_PATH}signed")
    finally:
        await proxy.stop()

    assert status == 502
    assert body == b""


async def test_a_resolution_error_fails_closed_to_base_and_is_not_cached() -> None:
    """A resolver that raises (a transient DB blip) yields the base for that one request and is NOT
    cached, so the next request re-resolves — a blip degrades one request, never the turn."""
    base = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)
    granted = (
        *base,
        ScopeRule(allowed_hosts=frozenset({SEARCH_HOST})),
        InjectionRule(host=SEARCH_HOST, header="authorization", sentinel="s", real="r"),
    )
    calls = {"n": 0}

    async def flaky(run: RunToken | None) -> tuple[object, ...]:
        if run is None:
            return base
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db blip")
        return granted

    proxy = EgressProxy(resolve=flaky, authorize=_fixed().turn_live, ca_cert="", ca_key="")
    run = RunToken(uuid4(), uuid4())
    assert await proxy._rules_for(run) == base
    assert await proxy._rules_for(run) == granted


class _Seeded(NamedTuple):
    workspace_id: UUID
    turn_id: UUID
    agent_id: UUID
    member_id: UUID
    conversation_id: UUID


async def _seed_turn(
    connection: AsyncConnection,
    status: str = "running",
    speaker: bool = False,
    internet_access_allowed: bool = True,
) -> _Seeded:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="a@b.c",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="claude-opus-4-8",
            internet_access_allowed=internet_access_allowed,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            surface="cli",
            queue_key=uuid4().hex,
            member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    terminal = None if status in ("queued", "running", "parked") else {"status": status}
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status=status,
            inbound="hi",
            speaker_member_id=member_id if speaker else None,
            terminal=terminal,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


async def test_concurrent_first_contact_mints_one_leaf_per_host() -> None:
    proxy = await _proxy()
    try:
        first, second = await asyncio.gather(
            proxy._leaf_context("api.example.com"),
            proxy._leaf_context("api.example.com"),
        )
        assert first is second
        assert first is proxy._contexts["api.example.com"]
    finally:
        await proxy.stop()


async def test_distinct_hosts_get_distinct_contexts() -> None:
    proxy = await _proxy()
    try:
        one, two = await asyncio.gather(
            proxy._leaf_context("api.anthropic.com"),
            proxy._leaf_context("api.openai.com"),
        )
        assert one is not two
        assert proxy._contexts["api.anthropic.com"] is one
        assert proxy._contexts["api.openai.com"] is two
    finally:
        await proxy.stop()


async def test_the_ca_and_leaf_outlive_a_long_running_process() -> None:
    """The proxy outlives every turn for the life of the process, so its boot-minted CA and every
    cached per-host leaf must stay valid far beyond a day — a deploy running past 24h with a 1-day
    cert would serve an expired chain and break in-sandbox TLS. The CA outlives the leaf it signs,
    so no leaf is left valid past its issuer."""
    now = datetime.now(UTC)
    cert_pem, _ = await generate_ca()
    assert x509.load_pem_x509_certificate(cert_pem.encode()).not_valid_after_utc > now + timedelta(
        days=300
    )

    proxy = await _proxy()
    try:
        await proxy._leaf_context(MODEL_HOST)
        root = Path(proxy._workdir.name)
        ca = x509.load_pem_x509_certificate((root / "ca.crt").read_bytes())
        leaf = x509.load_pem_x509_certificate((root / f"leaf-{MODEL_HOST}.crt").read_bytes())
    finally:
        await proxy.stop()
    assert leaf.not_valid_after_utc > now + timedelta(days=300)
    assert leaf.not_valid_after_utc <= ca.not_valid_after_utc


async def test_egress_write_attributes_a_row_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    await proxy._write_egress(SEARCH_HOST, _basic(RunToken(workspace_id, turn_id).encode()))
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_meter_ledger_meters_credential_host_and_skips_model_host(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    rules = (
        MeterRule(host=SEARCH_HOST, dimension="search"),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    proxy = _egress(_fixed(rules))
    header = _basic(RunToken(workspace_id, turn_id).encode())
    proxy._meter_ledger(MODEL_HOST, header, rules)
    assert proxy._meter_tasks == set()
    proxy._meter_ledger(SEARCH_HOST, header, rules)
    assert len(proxy._meter_tasks) == 1
    await proxy.stop()
    async with workspace_tx() as connection:
        dimensions = (
            (
                await connection.execute(
                    sa.select(tables.ledger.c.dimension).where(tables.ledger.c.turn_id == turn_id)
                )
            )
            .scalars()
            .all()
        )
    assert list(dimensions) == ["egress"]


async def test_egress_write_without_attribution_writes_nothing(db: None) -> None:
    async with workspace_tx() as connection:
        await _seed_turn(connection)
    proxy = _egress(_fixed())
    await proxy._write_egress(SEARCH_HOST, "")
    async with workspace_tx() as connection:
        count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.ledger))
        ).scalar_one()
    assert count == 0


async def test_client_reset_while_awaiting_the_request_line_does_not_crash_the_server() -> None:
    """A peer that resets the connection while `_handle` awaits the request line (a health check
    probe, a client that hangs up early) is routine TCP behavior, not a bug — it must not surface as
    an unhandled exception in `client_connected_cb`, and the server must keep serving other
    connections afterward."""
    cert, key = await generate_ca()
    proxy = _egress(_fixed(), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", endpoint.port))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        await asyncio.sleep(0.1)
        assert unhandled == []
        assert await _connect(endpoint.port, MODEL_HOST) == 403
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_a_db_fault_in_the_authorize_gate_surfaces_loud() -> None:
    """The reset guard is scoped to the client socket: an OSError out of the turn-liveness gate (the
    fresh DB connection behind it refused) is an internal fault, and must reach the loop's exception
    handler — never be swallowed as routine client noise."""

    async def refused(run: RunToken) -> bool:
        raise ConnectionRefusedError("db connection refused")

    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
    )
    cert, key = await generate_ca()
    proxy = EgressProxy(resolve=_fixed(rules).resolve, authorize=refused, ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        token = RunToken(workspace_id=uuid4(), turn_id=uuid4()).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert await reader.read() == b""
        writer.close()
        await asyncio.sleep(0.1)
        assert [type(context["exception"]) for context in unhandled] == [ConnectionRefusedError]
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_keyed_host_connect_denied_without_a_live_turn(db: None) -> None:
    """The turn-liveness gate: a keyed host (one carrying an InjectionRule) is MITM'd — hence its
    real key injected — only while the run token names a running turn. A tokenless CONNECT, a token
    for a turn that has ended, and a token for a turn that never existed are each refused at 403 and
    never reach `_mitm`, so the real key never leaves the proxy."""
    async with workspace_tx() as connection:
        workspace_id, running_turn, *_ = await _seed_turn(connection)
        _, ended_turn, *_ = await _seed_turn(connection, status="done")
    base = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(base), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert await _connect(endpoint.port, MODEL_HOST) == 403
        ended = RunToken(workspace_id, ended_turn).encode()
        assert await _connect(endpoint.port, MODEL_HOST, ended) == 403
        unknown = RunToken(workspace_id, uuid4()).encode()
        assert await _connect(endpoint.port, MODEL_HOST, unknown) == 403
        assert await proxy.authorize(RunToken(workspace_id, running_turn)) is True
        assert await proxy.authorize(RunToken(workspace_id, ended_turn)) is False
    finally:
        await proxy.stop()


async def test_public_internet_rejects_private_addresses_and_ended_turns(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, running_turn, *_ = await _seed_turn(connection)
        ended_workspace, ended_turn, *_ = await _seed_turn(connection, status="done")
    cert, key = await generate_ca()
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = _egress(resolver, ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        running = RunToken(workspace_id, running_turn).encode()
        ended = RunToken(ended_workspace, ended_turn).encode()
        assert await resolver.resolve(RunToken(workspace_id, running_turn)) == (InternetRule(),)
        assert await resolver.resolve(RunToken(workspace_id, uuid4())) == ()
        assert await _connect(endpoint.port, "169.254.169.254", running) == 403
        assert await _connect(endpoint.port, "8.8.8.8", ended) == 403
        assert await _connect(endpoint.port, "example.com", target_port="abc") == 403
        assert await _connect(endpoint.port, "example.com", running, "abc") == 400
        assert await _connect(endpoint.port, "example.com", running, 65536) == 400
        assert await _connect(endpoint.port, "a..b", running) == 403
        assert await _connect(endpoint.port, r"a\999z.com", running) == 403
    finally:
        await proxy.stop()


async def test_agent_internet_policy_is_cached_for_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = _egress(resolver)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    first_run = RunToken(seeded.workspace_id, seeded.turn_id)
    try:
        assert await proxy._rules_for(first_run) == (InternetRule(),)

        second_turn = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(internet_access_allowed=False)
                .where(tables.agent.c.id == seeded.agent_id)
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=second_turn,
                    workspace_id=seeded.workspace_id,
                    conversation_id=seeded.conversation_id,
                    agent_id=seeded.agent_id,
                    seq=2,
                    status="running",
                    inbound="again",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

        second_run = RunToken(seeded.workspace_id, second_turn)
        assert await proxy._rules_for(first_run) == (InternetRule(),)
        assert await proxy._rules_for(second_run) == ()
        assert await _connect(endpoint.port, "8.8.8.8", second_run.encode()) == 403
    finally:
        await proxy.stop()


async def test_public_internet_tunnels_and_meters_a_live_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)

    async def local_public(_host: str, _port: int) -> str:
        return "127.0.0.1"

    stub = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        resolve_public=local_public,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RunToken(workspace_id, turn_id).encode()
    try:
        assert await _connect(endpoint.port, SEARCH_HOST, token, stub_port) == 200
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def _git_ls_remote(
    proxy_port: int, run_token: str, host: str, port: int, git_config: Mapping[str, str]
) -> str:
    """Drive the real `git` binary at `host:port` through the proxy, with the run token in the proxy
    URL exactly as a carrier threads it, and answer what git reported."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "ls-remote",
        f"https://{host}:{port}/owner/repo.git",
        env={
            "PATH": os.environ.get("PATH", ""),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "https_proxy": f"http://{run_token}:@127.0.0.1:{proxy_port}",
            **git_config,
        },
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    return stderr.decode(errors="replace")


async def test_real_git_reaches_the_public_internet_only_with_the_proxy_auth_config(
    db: None,
) -> None:
    """The whole chain a public `git clone` rides, with nothing hand-built: a manifest declaring
    `sandbox_internet` derives the `InternetRule` that admits any globally routable host — no
    per-host ScopeRule exists or is needed — and the turn's `GIT_PROXY_AUTH_ENV` is what lets git
    present the run token that reaches that rule at all.

    git's default `http.proxyAuthMethod=anyauth` waits for a `407` challenge the proxy never sends,
    so an unconfigured CONNECT arrives unattributed and resolves to the base rules — the turn's own
    internet policy is keyed on the token it never sent. Configured, the tunnel opens and meters."""
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)

    async def local_public(_host: str, _port: int) -> str:
        return "127.0.0.1"

    upstream_connections = 0

    async def upstream(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal upstream_connections
        upstream_connections += 1
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    internet = derive_manifest_rules((Manifest(name="repl", version="1", sandbox_internet=True),))
    assert internet == (InternetRule(),)
    resolver = PerAgentRules(base=(), grants=None, internet=internet)
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        resolve_public=local_public,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RunToken(seeded.workspace_id, seeded.turn_id).encode()
    try:
        assert not [rule for rule in await resolver.resolve(None) if isinstance(rule, ScopeRule)]

        unconfigured = await _git_ls_remote(endpoint.port, token, "git.test", stub_port, {})
        assert "403" in unconfigured
        assert upstream_connections == 0

        configured = await _git_ls_remote(
            endpoint.port, token, "git.test", stub_port, GIT_PROXY_AUTH_ENV
        )
        assert "403" not in configured
        assert upstream_connections == 1
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == seeded.turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_public_internet_accepts_only_globally_routable_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _egress(_fixed())
    try:
        assert await proxy._resolve_public_address("8.8.8.8", 443) == "8.8.8.8"
        blocked = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "224.0.0.1",
            "64:ff9b::a9fe:a9fe",
            "::7f00:1",
        )
        for host in blocked:
            with pytest.raises(PermissionError):
                await proxy._resolve_public_address(host, 443)

        async def resolve(host: object, *_args: object, **_kwargs: object) -> tuple[str, ...]:
            text = str(host).rstrip(".")
            addresses = {
                "localhost": ("127.0.0.1",),
                "mixed.test": ("8.8.8.8", "127.0.0.1"),
            }.get(text, ("8.8.8.8",))
            return addresses

        monkeypatch.setattr(dns.asyncresolver, "resolve", resolve)
        with pytest.raises(PermissionError):
            await proxy._resolve_public_address("localhost", 443)
        with pytest.raises(PermissionError):
            await proxy._resolve_public_address("mixed.test", 443)
        assert await proxy._resolve_public_address("example.com", 443) == "8.8.8.8"
    finally:
        await proxy.stop()


async def test_tunnel_meters_a_granted_host(db: None) -> None:
    """A granted host injects nothing (its token is held server-side), so it is tunnelled opaquely —
    yet reaching it must still be metered. A real CONNECT to a MeterRule host writes one `egress`
    ledger row keyed to the turn, metered at CONNECT granularity since the opaque tunnel hides the
    individual requests inside it."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)

    async def upstream(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    granted_host = "127.0.0.1"
    stub_port = stub.sockets[0].getsockname()[1]
    rules = (
        ScopeRule(allowed_hosts=frozenset({granted_host})),
        MeterRule(host=granted_host, dimension="requests"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    run_token = RunToken(workspace_id, turn_id).encode()
    try:
        assert await _connect(endpoint.port, granted_host, run_token, stub_port) == 200
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


def _candidates() -> list[InjectionRule]:
    return [
        InjectionRule(host="h", header="authorization", sentinel="Bearer S1", real="Bearer R1"),
        InjectionRule(host="h", header="authorization", sentinel="Bearer S2", real="Bearer R2"),
    ]


def test_inject_swaps_only_the_matching_sentinel_and_forces_close() -> None:
    out = _inject([b"authorization: Bearer S2\r\n", b"connection: keep-alive\r\n"], _candidates())
    assert b"authorization: Bearer R2\r\n" in out
    assert b"R1" not in out
    assert b"keep-alive" not in out
    assert out.endswith(b"connection: close\r\n")


def test_inject_swaps_every_key_a_provider_takes_on_one_request() -> None:
    """A provider that authenticates with two keys is two InjectionRules on one host, and one
    request carries both: each header is swapped from its own sentinel, so `DD-API-KEY` and
    `DD-APPLICATION-KEY` reach Datadog together while the sandbox held neither."""
    out = _inject(
        [b"DD-API-KEY: SENTINEL_DD_API\r\n", b"DD-APPLICATION-KEY: SENTINEL_DD_APP\r\n"],
        [
            InjectionRule(
                host="api.us5.datadoghq.com",
                header="DD-API-KEY",
                sentinel="SENTINEL_DD_API",
                real="dd-api-real",
            ),
            InjectionRule(
                host="api.us5.datadoghq.com",
                header="DD-APPLICATION-KEY",
                sentinel="SENTINEL_DD_APP",
                real="dd-app-real",
            ),
        ],
    )
    assert b"DD-API-KEY: dd-api-real\r\n" in out
    assert b"DD-APPLICATION-KEY: dd-app-real\r\n" in out
    assert b"SENTINEL" not in out


DATADOG_SITES = HostChoice(
    slot="datadog_api_host",
    description="Datadog site for this org.",
    hosts=("api.datadoghq.com", "api.us5.datadoghq.com"),
    default="api.datadoghq.com",
    env="DD_HOST",
)


async def test_resolve_injects_the_run_tokens_own_workspace_secret(db: None) -> None:
    """The keyed-provider path end to end at the resolver: one shared proxy, two workspaces, one
    declaration. Each turn's rules carry that workspace's own stored secret on the host its own
    companion slot pins, and a workspace that stored nothing gets no keyed egress at all — so
    nothing about workspace A is reachable from a run token for workspace B."""
    async with workspace_tx() as connection:
        first_workspace, first_turn, *_ = await _seed_turn(connection)
        second_workspace, second_turn, *_ = await _seed_turn(connection)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(first_workspace, "datadog_api_key", "first-real-key")
    await store.put(first_workspace, "datadog_api_host", "api.us5.datadoghq.com")
    slot = CredentialSlot(
        name="datadog_api_key",
        description="api key",
        injection=InjectionTarget(
            host=DATADOG_SITES,
            header="DD-API-KEY",
            sentinel="SENTINEL_DD_API",
            env="DD_API_KEY",
            dimension=REQUEST_METER_DIMENSION,
        ),
    )
    resolver = PerAgentRules(base=(), grants=None, credentials=store, slots=(slot,))
    keyed = await resolver.resolve(RunToken(first_workspace, first_turn))
    assert (
        InjectionRule(
            host="api.us5.datadoghq.com",
            header="DD-API-KEY",
            sentinel="SENTINEL_DD_API",
            real="first-real-key",
        )
        in keyed
    )
    assert ScopeRule(allowed_hosts=frozenset({"api.us5.datadoghq.com"})) in keyed
    assert MeterRule(host="api.us5.datadoghq.com", dimension=REQUEST_METER_DIMENSION) in keyed
    unkeyed = await resolver.resolve(RunToken(second_workspace, second_turn))
    assert not [rule for rule in unkeyed if isinstance(rule, InjectionRule)]


def test_inject_passes_a_foreign_sentinel_upstream_untouched() -> None:
    out = _inject([b"authorization: Bearer FOREIGN\r\n"], _candidates())
    assert b"authorization: Bearer FOREIGN\r\n" in out
    assert b"R1" not in out and b"R2" not in out


def test_sse_usage_parses_an_anthropic_stream() -> None:
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_SSE)
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_reassembles_across_chunk_boundaries() -> None:
    accumulator = SseTokenUsage(MODEL_HOST)
    for start in range(0, len(ANTHROPIC_SSE), 7):
        accumulator.feed(ANTHROPIC_SSE[start : start + 7])
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_parses_an_openai_stream() -> None:
    accumulator = SseTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_SSE)
    assert accumulator.usage() == (
        "gpt-5.4",
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )


def test_sse_usage_without_a_usage_event_is_none() -> None:
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(
        b'event: content_block_delta\r\ndata: {"type":"content_block_delta",'
        b'"delta":{"text":"hi"}}\r\n\r\n'
    )
    assert accumulator.usage() is None


def test_json_body_usage_parses_an_anthropic_response() -> None:
    """A non-streaming Anthropic response is one JSON body with a top-level `usage` block, not
    `data:` SSE events; its usage is recovered so a single-JSON in-sandbox completion is metered."""
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_JSON_BODY)
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_json_body_usage_parses_an_openai_response() -> None:
    accumulator = SseTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_JSON_BODY)
    assert accumulator.usage() == (
        "gpt-5.4",
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )


def test_json_body_usage_reassembles_across_chunk_boundaries() -> None:
    accumulator = SseTokenUsage(MODEL_HOST)
    for start in range(0, len(ANTHROPIC_JSON_BODY), 7):
        accumulator.feed(ANTHROPIC_JSON_BODY[start : start + 7])
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


async def _stream_pair() -> tuple[
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
]:
    left, right = socket.socketpair()
    left.setblocking(False)
    right.setblocking(False)
    return await asyncio.open_connection(sock=left), await asyncio.open_connection(sock=right)


async def test_relay_tees_the_full_body_to_the_client_while_metering_usage() -> None:
    """The model host's response streams to the client byte-for-byte unchanged AND is teed to the
    accumulator — the meter reads the stream without holding the client's bytes back."""
    (proxy_client_r, proxy_client_w), (peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (_peer_up_r, peer_up_w) = await _stream_pair()
    accumulator = SseTokenUsage(MODEL_HOST)
    relay = asyncio.create_task(
        _relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w, accumulator.feed)
    )
    peer_up_w.write(ANTHROPIC_SSE)
    await peer_up_w.drain()
    peer_up_w.close()
    received = await peer_client_r.readexactly(len(ANTHROPIC_SSE))
    await relay
    assert received == ANTHROPIC_SSE
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)
    proxy_client_w.close()
    peer_client_w.close()


async def test_relay_cancellation_tears_down_pumps_and_upstream() -> None:
    """stop()'s drain cancels the outer connection task at `_relay`'s await; `asyncio.wait` does
    not cascade to the pump tasks it was waiting on, so the teardown must run on the way out —
    or a live tunnel's pumps and upstream socket outlive the drained signal."""
    (proxy_client_r, proxy_client_w), (_peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (_peer_up_r, peer_up_w) = await _stream_pair()
    relay = asyncio.create_task(_relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w))
    await asyncio.sleep(0)
    relay.cancel()
    with pytest.raises(asyncio.CancelledError):
        await relay
    assert proxy_up_w.is_closing()
    for writer in (proxy_client_w, peer_client_w, peer_up_w):
        writer.close()


async def test_model_host_relay_meters_sandbox_tokens_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_SSE)
    proxy._meter_tokens(_basic(RunToken(workspace_id, turn_id).encode()), accumulator)
    assert len(proxy._meter_tasks) == 1
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd), row.model) == (
        "sandbox_tokens",
        10_000,
        81_500,
        "claude-opus-4-8",
    )


async def test_model_host_relay_meters_a_non_streaming_json_body(db: None) -> None:
    """A non-streaming single-JSON completion is metered through the same path as an SSE stream: the
    teed body's top-level usage is parsed and written under `sandbox_tokens`, so it is not free."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_JSON_BODY)
    proxy._meter_tokens(_basic(RunToken(workspace_id, turn_id).encode()), accumulator)
    assert len(proxy._meter_tasks) == 1
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd), row.model) == (
        "sandbox_tokens",
        10_000,
        81_500,
        "claude-opus-4-8",
    )


async def test_model_host_relay_skips_when_no_usage_is_reported(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = SseTokenUsage(MODEL_HOST)
    accumulator.feed(b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n')
    proxy._meter_tokens(_basic(RunToken(workspace_id, turn_id).encode()), accumulator)
    assert proxy._meter_tasks == set()
    await proxy.stop()
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert count == 0


FORWARD_HOST = "api.hub.test"


@dataclass
class _RecordingForwarder:
    """A real RequestForwarder standing in for the broker dependency: it records the one call the
    proxy makes and answers a canned provider response, so the assertion reads what crossed the
    seam — never a mock's own bookkeeping."""

    calls: list[tuple[str, str, str, dict[str, str], bytes]] = field(default_factory=list)

    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        self.calls.append((account_id, method, url, dict(headers), body))
        return ForwardedResponse(
            status=201,
            headers={"content-type": "application/json", "x-hub": "yes"},
            body=b'{"login":"me"}',
        )


def _forward_rule(forwarder: _RecordingForwarder, account: str = "acct-1") -> ForwardRule:
    return ForwardRule(
        host=FORWARD_HOST,
        header="authorization",
        sentinel=grant_sentinel(account),
        account_id=account,
        forward=forwarder,
    )


async def test_a_sentinel_cli_request_forwards_through_the_broker(db: None) -> None:
    """The wire analog of a connector tool call, end to end over real sockets: the sandbox CONNECTs
    with its live turn's token, the proxy MITMs the granted host, and the request whose auth header
    carries the grant sentinel is executed through the broker under the granted account — the
    provider response streams back, the auth header never leaves the proxy, and the request is
    metered to the turn."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    forwarder = _RecordingForwarder()
    sentinel = grant_sentinel("acct-1")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        MeterRule(host=FORWARD_HOST, dimension="requests"),
        _forward_rule(forwarder),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RunToken(workspace_id, turn_id).encode()
    body = b'{"title":"hi"}'
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {FORWARD_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert b"200" in await reader.readline()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        context = ssl.create_default_context(cadata=cert)
        await writer.start_tls(context, server_hostname=FORWARD_HOST)
        writer.write(
            b"POST /repos/o/r/issues HTTP/1.1\r\n"
            b"host: " + FORWARD_HOST.encode() + b"\r\n"
            b"authorization: token " + sentinel.encode() + b"\r\n"
            b"content-type: application/json\r\n"
            b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
    finally:
        await proxy.stop()
    assert response.startswith(b"HTTP/1.1 201")
    assert b'{"login":"me"}' in response
    assert b"x-hub: yes" in response
    account, method, url, headers, sent_body = forwarder.calls[0]
    assert account == "acct-1"
    assert method == "POST"
    assert url == f"https://{FORWARD_HOST}/repos/o/r/issues"
    assert sent_body == body
    assert "authorization" not in {name.lower() for name in headers}
    assert headers.get("content-type") == "application/json"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_a_forward_host_connect_requires_a_live_turn(db: None) -> None:
    """A ForwardRule draws a real credential broker-side, so its host gates on turn liveness exactly
    as a keyed host does: a token for an ended turn is refused at CONNECT."""
    async with workspace_tx() as connection:
        workspace_id, ended_turn, *_ = await _seed_turn(connection, status="done")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        _forward_rule(_RecordingForwarder()),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        ended = RunToken(workspace_id, ended_turn).encode()
        assert await _connect(endpoint.port, FORWARD_HOST, ended) == 403
    finally:
        await proxy.stop()


def test_forward_match_selects_by_exact_or_scheme_prefixed_sentinel() -> None:
    forwarder = _RecordingForwarder()
    rule = _forward_rule(forwarder)
    sentinel = rule.sentinel.encode()
    assert _forward_match([b"authorization: token " + sentinel + b"\r\n"], [rule]) is rule
    assert _forward_match([b"Authorization: " + sentinel + b"\r\n"], [rule]) is rule
    assert _forward_match([b"authorization: token other\r\n"], [rule]) is None
    assert _forward_match([b"x-api-key: " + sentinel + b"\r\n"], [rule]) is None
    assert _forward_match([b"authorization:\r\n"], [rule]) is None


async def test_read_request_body_refuses_a_negative_content_length() -> None:
    """A negative Content-Length parses as an int and clears the upper bound, but `readexactly` on
    it raises ValueError (not IncompleteReadError) — which would escape uncaught and drop the
    connection with no response. It is refused up front (None), so the forward path answers the
    client instead of dying silently."""
    body = await _read_request_body(asyncio.StreamReader(), [b"content-length: -1\r\n"])
    assert body is None


def test_forward_response_bytes_drops_headers_carrying_crlf() -> None:
    """The broker's response headers come straight from Composio's JSON envelope with no wire
    validation, so a value (or name) carrying an embedded CR/LF would split the response written
    back into the sandbox's TLS stream. Such a header is dropped, never emitted — the safe ones
    still pass, and the framing headers this proxy owns are always present."""
    response = ForwardedResponse(
        status=200,
        headers={
            "x-ok": "fine",
            "x-split": "v\r\nInjected: evil",
            "x-newline": "a\nb",
            "bad\r\nname": "x",
        },
        body=b"{}",
    )
    raw = _forward_response_bytes(response)
    assert b"x-ok: fine\r\n" in raw
    assert b"Injected: evil" not in raw
    assert b"x-newline" not in raw
    assert b"bad" not in raw
    assert b"content-length: 2\r\n" in raw
    assert raw.endswith(b"connection: close\r\n\r\n{}")


async def test_resolve_derives_forward_rules_for_the_acting_member(db: None) -> None:
    """The per-turn resolver joins the turn's acting member to its agent's grants: the speaker's own
    private grant forwards, and a speakerless turn (no on-behalf-of) resolves no private forward —
    the wire-side mirror of `connector_accounts`."""
    async with workspace_tx() as connection:
        spoken = await _seed_turn(connection, speaker=True)
    store = GrantStore()
    await store.record(
        workspace_id=spoken.workspace_id,
        agent_id=spoken.agent_id,
        provider="hub",
        account_id="acct-1",
        host=FORWARD_HOST,
        grantor_member_id=spoken.member_id,
        conversation_id=spoken.conversation_id,
        shared=False,
    )
    cli = CliCredential(env="HUB_TOKEN", header="authorization", forward=_RecordingForwarder())
    resolver = PerAgentRules(base=(), grants=store, clis={"hub": cli})
    rules = await resolver.resolve(RunToken(spoken.workspace_id, spoken.turn_id))
    forward = next(rule for rule in rules if isinstance(rule, ForwardRule))
    assert forward.sentinel == grant_sentinel("acct-1")
    assert forward.account_id == "acct-1"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(speaker_member_id=None)
            .where(tables.turn.c.id == spoken.turn_id)
        )
    resolver = PerAgentRules(base=(), grants=store, clis={"hub": cli})
    silent = await resolver.resolve(RunToken(spoken.workspace_id, spoken.turn_id))
    assert not any(isinstance(rule, ForwardRule) for rule in silent)
