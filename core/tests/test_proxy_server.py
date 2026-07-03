import asyncio
import base64
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.db import workspace_tx
from selfhost.sandbox.proxy.rules import InjectionRule, MeterRule, ScopeRule
from selfhost.sandbox.proxy.server import EgressProxy, PerAgentRules, _inject, generate_ca
from selfhost.sandbox.session import RunToken
from selfhost.schema import tables

SEARCH_HOST = "api.search.test"
MODEL_HOST = "api.anthropic.com"


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
