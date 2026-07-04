import asyncio
import base64
import socket
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.db import workspace_tx
from selfhost.sandbox.proxy.rules import OPENAI_HOST, InjectionRule, MeterRule, ScopeRule
from selfhost.sandbox.proxy.server import (
    EgressProxy,
    PerAgentRules,
    SseTokenUsage,
    _inject,
    _relay,
    generate_ca,
)
from selfhost.sandbox.session import RunToken
from selfhost.schema import tables
from selfhost.schema.records import Usage

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


def _fixed(rules: tuple = ()) -> PerAgentRules:
    """The real resolver with no grant store: every run resolves to this fixed base — a genuine
    (degenerate) resolution, not a fake, standing in where a test drives paths other than grants."""
    return PerAgentRules(base=rules, grants=None)


async def _proxy() -> EgressProxy:
    cert, key = await generate_ca()
    proxy = EgressProxy(resolve=_fixed().resolve, ca_cert=cert, ca_key=key)
    await proxy.start(bind_host="127.0.0.1")
    return proxy


def _basic(run_token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_token}:".encode()).decode()


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

    proxy = EgressProxy(resolve=flaky, ca_cert="", ca_key="")
    run = RunToken(uuid4(), uuid4())
    assert await proxy._rules_for(run) == base
    assert await proxy._rules_for(run) == granted


async def _seed_turn(connection: AsyncConnection) -> tuple[UUID, UUID]:
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
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


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


async def test_egress_write_attributes_a_row_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
    proxy = EgressProxy(resolve=_fixed().resolve, ca_cert="x", ca_key="x")
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
        workspace_id, turn_id = await _seed_turn(connection)
    rules = (
        MeterRule(host=SEARCH_HOST, dimension="search"),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    proxy = EgressProxy(resolve=_fixed(rules).resolve, ca_cert="x", ca_key="x")
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
                    sa.select(tables.ledger.c.dimension).where(
                        tables.ledger.c.turn_id == turn_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(dimensions) == ["egress"]


async def test_egress_write_without_attribution_writes_nothing(db: None) -> None:
    async with workspace_tx() as connection:
        await _seed_turn(connection)
    proxy = EgressProxy(resolve=_fixed().resolve, ca_cert="x", ca_key="x")
    await proxy._write_egress(SEARCH_HOST, "")
    async with workspace_tx() as connection:
        count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.ledger))
        ).scalar_one()
    assert count == 0


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


async def test_model_host_relay_meters_sandbox_tokens_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
    proxy = EgressProxy(resolve=_fixed().resolve, ca_cert="x", ca_key="x")
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


async def test_model_host_relay_skips_when_no_usage_is_reported(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
    proxy = EgressProxy(resolve=_fixed().resolve, ca_cert="x", ca_key="x")
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
