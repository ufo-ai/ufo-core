"""The Metronome shipper's proof: pending usage exports leave as verbatim delta events, unsettled
and egress usage never does, and every failure path re-sends byte-identical events for Metronome's
dedup to absorb — even when the underlying ledger row grew between attempts.

Each test seeds real workspace/turn rows, writes ledger rows through the REAL core writers
(`record_turn_usage`, `record_sandbox_tokens`, ...) and reads through the real usage-export seam,
driving the shipper over an `httpx.MockTransport` that records the emitted requests — the stand-in
for Metronome's API, never the thing asserted. The final test drives the manifest's job through
the real `JobRunner`: candidates find the workspace, the dispatcher binds it, and the handler
ships scoped."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_metronome as metronome

from ufo.accounting import (
    record_egress_request,
    record_sandbox_tokens,
    record_turn_usage,
    record_workspace_usage,
)
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.jobs import JobRunner, bindings_from
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Usage
from ufo.workspace import ws

TOKEN = "sandbox-bearer-0xdecafbad"
MODEL = "claude-opus-4-8"
PAST_MARGIN_SECONDS = 1000


class _Recorder:
    """Records each request the shipper emits and answers with a canned status — the stand-in for
    the Metronome ingest API, never the thing asserted."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json={})


def _shipper(recorder: _Recorder) -> metronome.UsageShipper:
    return metronome.UsageShipper(
        ctx=context_for(metronome.NAME, frozenset()),
        transport=httpx.MockTransport(recorder.handle),
    )


def _events(request: httpx.Request) -> list[dict[str, object]]:
    return json.loads(request.content)


async def _seed() -> tuple[UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="who@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id


async def _turn(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, seq: int = 1) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status="running",
                inbound="hello",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _settle(turn_id: UUID, age_seconds: int) -> None:
    terminal = TerminalFrame(status="done", text="ok").model_dump(mode="json")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(
                status="done",
                terminal=terminal,
                updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            )
        )


async def _ledger_rows() -> list[sa.Row]:
    async with workspace_tx() as connection:
        result = await connection.execute(
            sa.select(tables.ledger).order_by(tables.ledger.c.created_at, tables.ledger.c.id)
        )
        return list(result.all())


async def _acked() -> set[tuple[UUID, int]]:
    async with workspace_tx() as connection:
        result = await connection.execute(
            sa.select(tables.ledger_export.c.ledger_id, tables.ledger_export.c.from_amount).where(
                tables.ledger_export.c.acked_at.isnot(None)
            )
        )
        return {(row.ledger_id, row.from_amount) for row in result.all()}


def test_manifest_declares_one_cron_ship_job() -> None:
    declared = metronome.manifest()
    assert declared.name == "metronome"
    (job,) = declared.jobs
    assert job.name == "usage_shipper"
    assert job.schedule == "0 * * * * *"
    assert job.handler is metronome._ship
    assert not declared.tools
    assert not declared.credentials
    assert not declared.routes


async def test_ships_settled_rows_with_exact_events(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=250))
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()

    (request,) = recorder.requests
    assert str(request.url) == metronome.INGEST_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    rows = {f"{row.id}:0": row for row in await _ledger_rows()}
    events = _events(request)
    assert {event["transaction_id"] for event in events} == set(rows)
    for event in events:
        row = rows[event["transaction_id"]]
        assert row.priced_micro_usd > 0
        assert event["customer_id"] == str(workspace_id)
        assert event["event_type"] == "ufo_usage"
        datetime.fromisoformat(event["timestamp"])
        assert event["properties"] == {
            "dimension": "tokens",
            "model": MODEL,
            "amount": str(row.amount),
            "priced_micro_usd": str(row.priced_micro_usd),
            "price_digest": row.price_digest,
            "turn_id": str(row.turn_id) if row.turn_id else "",
            "byok": "false",
        }
        assert all(isinstance(value, str) for value in event["properties"].values())

    with ws(workspace_id):
        await _shipper(recorder).run()
    assert len(recorder.requests) == 1


async def test_sandbox_rows_wait_for_turn_terminal_and_late_growth_ships_as_top_up(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=100, output_tokens=50)
        )
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=25)
        )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert recorder.requests == []

    await _settle(turn_id, age_seconds=0)
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert recorder.requests == []

    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)
    with ws(workspace_id):
        await _shipper(recorder).run()
    (request,) = recorder.requests
    (event,) = _events(request)
    assert event["properties"]["dimension"] == "sandbox_tokens"
    assert event["properties"]["amount"] == "175"

    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=40)
        )
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)
    with ws(workspace_id):
        await _shipper(recorder).run()
    (top_up,) = _events(recorder.requests[1])
    assert top_up["transaction_id"] == f"{event['transaction_id'].split(':')[0]}:175"
    assert top_up["properties"]["amount"] == "40"


async def test_unacked_delivery_stays_frozen_when_the_row_grows(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=175)
        )
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)

    failing = _Recorder(status=500)
    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _shipper(failing).run()
    assert await _acked() == set()
    (lost,) = _events(failing.requests[0])

    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=40)
        )
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)

    succeeding = _Recorder()
    with ws(workspace_id):
        await _shipper(succeeding).run()
    frozen, top_up = sorted(
        _events(succeeding.requests[0]), key=lambda e: int(e["transaction_id"].split(":")[1])
    )
    assert frozen["transaction_id"] == lost["transaction_id"]
    assert frozen["properties"]["amount"] == lost["properties"]["amount"] == "175"
    assert top_up["transaction_id"].endswith(":175")
    assert top_up["properties"]["amount"] == "40"


async def test_redirect_response_is_a_failed_delivery_not_an_ack(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """httpx does not follow redirects here: a 3xx never ingested anything, so it must raise
    like any failure — acking on it would silently under-bill forever."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(connection, workspace_id, turn_id, MODEL, Usage(input_tokens=10))
    redirecting = _Recorder(status=302)
    with ws(workspace_id), pytest.raises(metronome.MetronomeError, match="302"):
        await _shipper(redirecting).run()
    assert await _acked() == set()


async def test_unsent_rows_never_age_out_and_pre_floor_history_never_ships(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _, _ = await _seed()
    ctx = context_for(metronome.NAME, frozenset())
    floor = datetime.now(UTC) - timedelta(days=30)
    with ws(workspace_id):
        await ctx.store.put(metronome.FLOOR_KEY, floor.isoformat())
    async with workspace_tx() as connection:
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=100))
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=999))
    stalled, pre_floor = await _ledger_rows()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.id == stalled.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=10))
        )
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.id == pre_floor.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    (request,) = recorder.requests
    (event,) = _events(request)
    assert event["transaction_id"] == f"{stalled.id}:0"


async def test_first_run_records_the_floor_and_later_runs_keep_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _, _ = await _seed()
    ctx = context_for(metronome.NAME, frozenset())
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
        first = await ctx.store.get(metronome.FLOOR_KEY)
        await _shipper(recorder).run()
        second = await ctx.store.get(metronome.FLOOR_KEY)
    assert first is not None
    assert datetime.fromisoformat(str(first)) < datetime.now(UTC)
    assert second == first


async def test_batches_cap_at_100(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        for _ in range(150):
            await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=10))
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert [len(_events(request)) for request in recorder.requests] == [100, 50]
    assert len(await _acked()) == 150


async def test_egress_rows_never_ship(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    async with workspace_tx() as connection:
        await record_egress_request(connection, workspace_id, turn_id)
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert recorder.requests == []


async def test_missing_token_fails_loud_even_with_nothing_to_ship(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(metronome.METRONOME_BEARER_TOKEN_ENV, raising=False)
    workspace_id, _, _ = await _seed()
    recorder = _Recorder()
    with ws(workspace_id), pytest.raises(RuntimeError, match="METRONOME_BEARER_TOKEN"):
        await _shipper(recorder).run()
    async with workspace_tx() as connection:
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=100))
    with ws(workspace_id), pytest.raises(RuntimeError, match="METRONOME_BEARER_TOKEN"):
        await _shipper(recorder).run()
    assert recorder.requests == []
    assert await _acked() == set()


async def test_manifest_job_fires_through_job_runner(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    recorder = _Recorder()
    monkeypatch.setattr(metronome, "INGEST_TRANSPORT", httpx.MockTransport(recorder.handle))
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=100))
    runner = JobRunner(bindings=bindings_from((metronome.manifest(),), ()))
    await runner.fire(f"{metronome.NAME}:{metronome.JOB_NAME}")
    (request,) = recorder.requests
    (event,) = _events(request)
    assert event["customer_id"] == str(workspace_id)
    assert await _acked() != set()
