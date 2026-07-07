"""Egress-proxy live-leg validation (phase #17, T4): a real HTTP client egresses through the REAL
`EgressProxy` over its bound socket and the whole live leg runs for real — CONNECT, a minted leaf
cert, TLS termination, sentinel→real-key injection, TLS re-origination upstream, the SSE response
tee, and a real `sandbox_tokens` ledger row written to the real database. This end-to-end proves the
in-sandbox metering that the unit tests exercise piecewise.

The only stand-in is the paid model host (api.anthropic.com): a local TLS server presents a cert for
that name (signed by a throwaway CA the proxy's upstream dial is pointed at) and emits a canned
Anthropic SSE body. It is the external paid dependency, doubled at the network boundary — never the
thing asserted. Every assertion reads the real upstream's received headers and the real ledger row.

The client is a real `httpx` client configured exactly as an in-sandbox HTTP client is: it trusts
the proxy's CA and routes through the proxy with the run token as proxy-auth. It stands in for the
sandbox's HTTP client — the proxy code under test is identical whichever process the client runs
in; a containerised client would add only network-namespace realism, and live-container egress
through the proxy is a separately-tracked (deferred) proof. Marked `serial`: it binds a port and
patches the process-global upstream dial."""

import asyncio
import base64
import datetime as dt
import ssl
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, InjectionRule, MeterRule, ScopeRule
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import RunToken
from ufo.schema import tables

pytestmark = [pytest.mark.integration, pytest.mark.serial]

MODEL_HOST = ANTHROPIC_HOST
SENTINEL_KEY = "sk-sentinel-DO-NOT-LEAK"
REAL_KEY = "sk-real-upstream-secret"
CLIENT_TIMEOUT_SECONDS = 15.0

# Anthropic reports input/cache on message_start and the cumulative output on message_delta; this
# shape prices to 81_500 micro-USD for claude-opus-4-8 (see test_accounting / test_proxy_server).
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
SSE_RESPONSE = (
    b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"
    + ANTHROPIC_SSE
)


async def _seed_turn(connection: AsyncConnection, status: str = "running") -> tuple[UUID, UUID]:
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
            terminal=terminal,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


def _upstream_tls(host: str, workdir: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """A throwaway CA and a leaf for `host`, standing in for the paid model host's TLS. Returns the
    server context (the stub presents the leaf) and a client context trusting the CA (the proxy's
    upstream dial is pointed at the stub with this context)."""
    now = dt.datetime.now(dt.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-upstream-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = workdir / "upstream_ca.crt"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    leaf_crt = workdir / "leaf.crt"
    leaf_key_path = workdir / "leaf.key"
    leaf_crt.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    leaf_key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(leaf_crt), str(leaf_key_path))
    client_ctx = ssl.create_default_context(cafile=str(ca_path))
    return server_ctx, client_ctx


async def test_sandbox_egress_injects_the_real_key_and_meters_sandbox_tokens(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)

    server_ctx, client_ctx = _upstream_tls(MODEL_HOST, tmp_path)
    received: dict[str, str] = {}

    async def stub(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = await reader.read(4096)
            if not chunk:
                break
            head += chunk
        received["head"] = head.decode(errors="replace")
        writer.write(SSE_RESPONSE)
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(stub, "127.0.0.1", 0, ssl=server_ctx)
    stub_port = upstream.sockets[0].getsockname()[1]

    real_open = asyncio.open_connection

    async def redirect_upstream(host=None, port=None, *args, **kwargs):
        """Point only the proxy's TLS dial to the paid model host at the local stub; everything else
        (and every non-TLS dial) hits the real connector unchanged."""
        if host == MODEL_HOST and kwargs.get("ssl"):
            return await real_open("127.0.0.1", stub_port, ssl=client_ctx, server_hostname=host)
        return await real_open(host, port, *args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", redirect_upstream)

    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel=SENTINEL_KEY, real=REAL_KEY),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    ca_cert, ca_key = await generate_ca()
    resolver = PerAgentRules(base=rules, grants=None)
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=ca_cert,
        ca_key=ca_key,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    ca_path = tmp_path / "proxy_ca.crt"
    ca_path.write_text(endpoint.ca_cert)
    run_token = RunToken(workspace_id, turn_id).encode()
    client_verify = ssl.create_default_context(cafile=str(ca_path))

    try:
        async with httpx.AsyncClient(
            proxy=f"http://{run_token}:@127.0.0.1:{endpoint.port}",
            verify=client_verify,
            timeout=CLIENT_TIMEOUT_SECONDS,
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    f"https://{MODEL_HOST}/v1/messages",
                    headers={"x-api-key": SENTINEL_KEY},
                    content=b"{}",
                ),
                timeout=CLIENT_TIMEOUT_SECONDS,
            )
        assert response.status_code == 200
        assert b"message_start" in response.content
    finally:
        await proxy.stop()  # drains the off-relay metering task
        upstream.close()
        await upstream.wait_closed()

    # The upstream received the injected REAL key; the sentinel never left the proxy.
    assert "x-api-key: sk-real-upstream-secret" in received["head"]
    assert SENTINEL_KEY not in received["head"]

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert row.dimension == "sandbox_tokens"
    assert int(row.amount) == 10_000
    assert int(row.priced_micro_usd) == 81_500
    assert row.model == "claude-opus-4-8"
    assert row.price_digest is not None and row.price_digest.startswith("sha256:")


async def test_a_terminal_turn_is_denied_before_any_upstream_dial(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn-liveness gate at the network boundary: a CONNECT to the paid model host carrying a
    token for a turn that has ended is refused at 403 before `_mitm` runs, so the proxy never opens
    its upstream TLS dial to the real host — the real key never reaches the wire — and no ledger row
    is written. The live leg (injection + metering) is proven by the sibling test; this proves the
    key is withheld the moment the turn is not running."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection, status="done")

    dialed: list[str] = []
    real_open = asyncio.open_connection

    async def recording_open(host=None, port=None, *args, **kwargs):
        if kwargs.get("ssl"):
            dialed.append(str(host))
        return await real_open(host, port, *args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", recording_open)

    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel=SENTINEL_KEY, real=REAL_KEY),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    ca_cert, ca_key = await generate_ca()
    resolver = PerAgentRules(base=rules, grants=None)
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=ca_cert,
        ca_key=ca_key,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    run_token = RunToken(workspace_id, turn_id).encode()
    auth = base64.b64encode(f"{run_token}:".encode()).decode()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\nHost: {MODEL_HOST}\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
        )
        await writer.drain()
        status_line = await reader.readline()
        await reader.read()
        writer.close()
    finally:
        await proxy.stop()

    assert int(status_line.split()[1]) == 403
    assert dialed == []
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert count == 0
