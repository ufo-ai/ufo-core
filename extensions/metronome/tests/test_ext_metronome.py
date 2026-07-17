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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_bedrock as bedrock
import ufo_ext_metronome as metronome
from cryptography.fernet import Fernet

from ufo.accounting import (
    record_egress_request,
    record_sandbox_tokens,
    record_turn_usage,
    record_workspace_usage,
)
from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.credentials import CredentialRequests, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import turn_tools
from ufo.jobs import JobRunner, bindings_from
from ufo.models.registry import ModelRegistry, model_registry
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, TerminalFrame, Turn, Usage
from ufo.seats import OwnerSeatRevocation, SeatLimitReached
from ufo.surfaces.admission import Admission
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import init_workspace_credentials, ws

TOKEN = "sandbox-bearer-0xdecafbad"
MODEL = "claude-opus-4-8"
PAST_MARGIN_SECONDS = 1000


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("a seat tool must not touch the sandbox")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        raise AssertionError("a seat tool must not touch the sandbox")

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError("a seat tool must not touch the sandbox")


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("a seat tool must not spawn a subagent")


class _Recorder:
    """Records each request the shipper emits and answers with a canned status — the stand-in for
    the Metronome ingest API, never the thing asserted."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json={})


def _registry() -> ModelRegistry:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite://"),
        blob=BlobConfig(backend="filesystem", root=Path()),
    )
    return model_registry(config, (bedrock.manifest(),))


def _shipper_context() -> ExtensionContext:
    return context_for(
        metronome.NAME,
        frozenset((metronome.ANTHROPIC_KEY_SLOT,)),
        model_resolver=_registry(),
    )


def _shipper(recorder: _Recorder) -> metronome.UsageShipper:
    return metronome.UsageShipper(
        ctx=_shipper_context(),
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


def test_manifest_declares_three_cron_jobs_three_tools_one_section() -> None:
    declared = metronome.manifest()
    assert declared.name == "metronome"
    usage, seats, approvals = declared.jobs
    assert usage.name == "usage_shipper"
    assert usage.schedule == "0 * * * * *"
    assert usage.handler is metronome._ship
    assert seats.name == "seat_shipper"
    assert seats.schedule == "0 0 * * * *"
    assert seats.handler is metronome._ship_seats
    assert approvals.name == "seat_approvals"
    assert approvals.schedule == "30 * * * * *"
    assert approvals.handler is metronome._ask_seat_approvals
    assert [tool.name for tool in declared.tools] == ["grant_seat", "revoke_seat", "list_seats"]
    assert all(tool.side_effecting for tool in declared.tools[:2])
    assert not declared.tools[2].side_effecting
    (section,) = declared.prompt_sections
    assert section.name == "seats"
    (slot,) = declared.credentials
    assert slot.name == "anthropic_api_key"
    assert slot.injection is None
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
    runner = JobRunner(bindings=bindings_from((metronome.manifest(),), ()), registry=_registry())
    await runner.fire(f"{metronome.NAME}:{metronome.JOB_NAME}")
    (request,) = recorder.requests
    (event,) = _events(request)
    assert event["customer_id"] == str(workspace_id)
    assert await _acked() != set()


async def _seat_seed(limit: int | None = 2) -> tuple[UUID, UUID, UUID]:
    """A workspace under a seat limit whose owner (earliest member) is seated and whose later
    joiner is not — both sides of the owner gate and the seat count in one seed."""
    workspace_id, owner_id, joiner_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                seat_limit=limit,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for member_id, email, seated, created in (
            (owner_id, "owner@example.com", True, datetime(2026, 1, 1, tzinfo=UTC)),
            (joiner_id, "late@example.com", False, datetime(2026, 6, 1, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    seated_at=created if seated else None,
                    created_at=created,
                    updated_at=created,
                )
            )
    return workspace_id, owner_id, joiner_id


def _seat_tools() -> tuple[dict[str, ToolDef], dict[str, ExtensionContext]]:
    declared, ext_by_tool = turn_tools(
        (metronome.manifest(),), CredentialStore(fernet=Fernet(Fernet.generate_key()))
    )
    return {tool.name: tool for tool in declared}, ext_by_tool


def _tool_context(
    workspace_id: UUID, ext: ExtensionContext, tmp_path: Path, member_id: UUID | None
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="manage seats",
            created_at=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model=MODEL),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience_member_id=member_id,
        artifact_token_secret="",
        ext=ext,
    )


async def _run_tool(
    workspace_id: UUID, tmp_path: Path, member_id: UUID | None, name: str, **args: object
) -> dict[str, object]:
    registry, ext_by_tool = _seat_tools()
    tool = registry[name]
    ctx = _tool_context(workspace_id, ext_by_tool[name], tmp_path, member_id)
    with ws(workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate(args))
    return json.loads(result.content[0].text)


async def _seated(member_id: UUID) -> bool:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.seated_at).where(tables.member.c.id == member_id)
            )
        ).scalar_one() is not None


async def test_owner_grants_and_revokes_a_seat_through_real_dispatch(
    db: None, tmp_path: Path
) -> None:
    workspace_id, owner_id, joiner_id = await _seat_seed()
    payload = await _run_tool(
        workspace_id, tmp_path, owner_id, metronome.GRANT_SEAT_TOOL, email="late@example.com"
    )
    assert await _seated(joiner_id)
    assert payload["seat_limit"] == 2
    assert payload["included_seats"] is None
    assert payload["billed_overage_seats"] == 0
    assert payload["seated"] == 2
    assert payload["members"] == [
        {"email": "owner@example.com", "seated": True, "owner": True},
        {"email": "late@example.com", "seated": True, "owner": False},
    ]
    payload = await _run_tool(
        workspace_id, tmp_path, owner_id, metronome.REVOKE_SEAT_TOOL, email="late@example.com"
    )
    assert not await _seated(joiner_id)
    assert payload["seated"] == 1


async def test_non_owner_and_speakerless_seat_changes_are_refused(db: None, tmp_path: Path) -> None:
    workspace_id, _, joiner_id = await _seat_seed()
    with pytest.raises(ValueError, match="only the workspace owner"):
        await _run_tool(
            workspace_id, tmp_path, joiner_id, metronome.GRANT_SEAT_TOOL, email="late@example.com"
        )
    with pytest.raises(ValueError, match="speaking member"):
        await _run_tool(
            workspace_id, tmp_path, None, metronome.REVOKE_SEAT_TOOL, email="late@example.com"
        )
    assert not await _seated(joiner_id)


async def test_grant_at_the_limit_surfaces_the_seat_error(db: None, tmp_path: Path) -> None:
    workspace_id, owner_id, _ = await _seat_seed(limit=1)
    with pytest.raises(SeatLimitReached, match="all 1 seats"):
        await _run_tool(
            workspace_id, tmp_path, owner_id, metronome.GRANT_SEAT_TOOL, email="late@example.com"
        )


async def test_revoking_the_owner_is_refused(db: None, tmp_path: Path) -> None:
    workspace_id, owner_id, _ = await _seat_seed()
    with pytest.raises(OwnerSeatRevocation):
        await _run_tool(
            workspace_id, tmp_path, owner_id, metronome.REVOKE_SEAT_TOOL, email="owner@example.com"
        )


async def test_any_member_lists_seats(db: None, tmp_path: Path) -> None:
    workspace_id, _, joiner_id = await _seat_seed()
    payload = await _run_tool(workspace_id, tmp_path, joiner_id, metronome.LIST_SEATS_TOOL)
    assert payload["seat_limit"] == 2
    assert [entry["email"] for entry in payload["members"]] == [
        "owner@example.com",
        "late@example.com",
    ]


def _seat_shipper(recorder: _Recorder) -> metronome.SeatShipper:
    return metronome.SeatShipper(
        ctx=_shipper_context(),
        transport=httpx.MockTransport(recorder.handle),
    )


async def _workspace_limit(workspace_id: UUID) -> int | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.workspace.c.seat_limit).where(
                    tables.workspace.c.id == workspace_id
                )
            )
        ).scalar_one()


async def test_seat_job_establishes_the_limit_and_ships_one_daily_snapshot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    recorder = _Recorder()
    monkeypatch.setattr(metronome, "INGEST_TRANSPORT", httpx.MockTransport(recorder.handle))
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.workspace_id == workspace_id)
        )
    runner = JobRunner(bindings=bindings_from((metronome.manifest(),), ()), registry=_registry())
    await runner.fire(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}")
    assert await _workspace_limit(workspace_id) == metronome.SEAT_LIMIT_DEFAULT
    async with workspace_tx() as connection:
        included = (
            await connection.execute(
                sa.select(tables.workspace.c.included_seats).where(
                    tables.workspace.c.id == workspace_id
                )
            )
        ).scalar_one()
    assert included == metronome.INCLUDED_SEATS_DEFAULT
    (request,) = recorder.requests
    (event,) = _events(request)
    today = datetime.now(UTC).date().isoformat()
    assert event["transaction_id"] == f"seats:{workspace_id}:{today}"
    assert event["customer_id"] == str(workspace_id)
    assert event["event_type"] == "ufo_seats"
    assert event["properties"] == {
        "seat_count": "1",
        "seat_limit": str(metronome.SEAT_LIMIT_DEFAULT),
    }
    assert all(isinstance(value, str) for value in event["properties"].values())
    await runner.fire(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}")
    assert len(recorder.requests) == 1


async def test_seat_job_reships_after_a_stale_mark_and_never_overwrites_a_limit(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=3, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
    recorder = _Recorder()
    shipper = _seat_shipper(recorder)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    with ws(workspace_id):
        await shipper.ctx.store.put(metronome.SEAT_SHIPPED_KEY, yesterday)
        await shipper.run()
    (event,) = _events(recorder.requests[0])
    today = datetime.now(UTC).date().isoformat()
    assert event["transaction_id"] == f"seats:{workspace_id}:{today}"
    assert event["properties"]["seat_limit"] == "3"
    assert await _workspace_limit(workspace_id) == 3


async def test_seat_job_failed_post_leaves_no_mark_then_reships_the_same_id(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _, _ = await _seed()
    failing = _Recorder(status=500)
    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _seat_shipper(failing).run()
    recorder = _Recorder()
    with ws(workspace_id):
        await _seat_shipper(recorder).run()
    (failed_event,) = _events(failing.requests[0])
    (event,) = _events(recorder.requests[0])
    assert event["transaction_id"] == failed_event["transaction_id"]
    with ws(workspace_id):
        await _seat_shipper(recorder).run()
    assert len(recorder.requests) == 1


async def test_seat_job_missing_token_fails_loud(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(metronome.METRONOME_BEARER_TOKEN_ENV, raising=False)
    workspace_id, _, _ = await _seed()
    recorder = _Recorder()
    with ws(workspace_id), pytest.raises(RuntimeError, match="METRONOME_BEARER_TOKEN"):
        await _seat_shipper(recorder).run()
    assert recorder.requests == []
    assert await _workspace_limit(workspace_id) is None


async def test_byok_label_flips_with_the_stored_key_and_stays_per_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    other_workspace, other_agent, other_conversation = await _seed()
    for ws_id, conv_id, ag_id in (
        (workspace_id, conversation_id, agent_id),
        (other_workspace, other_conversation, other_agent),
    ):
        turn_id = await _turn(ws_id, conv_id, ag_id)
        await _settle(turn_id, age_seconds=0)
        async with workspace_tx() as connection:
            await record_turn_usage(
                connection, ws_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
            )
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-workspace-own")
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    with ws(other_workspace):
        await _shipper(recorder).run()
    byok_events = _events(recorder.requests[0])
    passthrough_events = _events(recorder.requests[1])
    assert {event["properties"]["byok"] for event in byok_events} == {"true"}
    assert {event["properties"]["byok"] for event in passthrough_events} == {"false"}


async def test_byok_label_reflects_a_key_added_between_ticks(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(connection, workspace_id, turn_id, MODEL, Usage(input_tokens=100))
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-late")
    second_turn = await _turn(workspace_id, conversation_id, agent_id, seq=2)
    await _settle(second_turn, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, second_turn, MODEL, Usage(input_tokens=50)
        )
    with ws(workspace_id):
        await _shipper(recorder).run()
    (first,) = _events(recorder.requests[0])
    (second,) = _events(recorder.requests[1])
    assert first["properties"]["byok"] == "false"
    assert second["properties"]["byok"] == "true"


async def test_the_declared_slot_opens_the_chat_seal_and_byok_resolution(db: None) -> None:
    """The whole BYOK path over real parts: the manifest's declared union lets the private
    handoff seal exactly this slot (and refuses it when metronome is absent), the fulfilled
    secret lands in the store, and model-key resolution prefers it over the platform env."""
    workspace_id, _, _ = await _seed()
    fernet = Fernet(Fernet.generate_key())
    declared = frozenset(slot.name for slot in metronome.manifest().credentials)
    requests = CredentialRequests(fernet=fernet, declared=declared)
    member_id = uuid4()
    sealed = requests.seal(workspace_id, member_id, (metronome.ANTHROPIC_KEY_SLOT,))
    assert sealed
    without_metronome = CredentialRequests(fernet=fernet, declared=frozenset())
    with pytest.raises(ValueError, match="anthropic_api_key"):
        without_metronome.seal(workspace_id, member_id, (metronome.ANTHROPIC_KEY_SLOT,))
    store = CredentialStore(fernet=fernet)
    init_workspace_credentials(store)
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-byok")
    with ws(workspace_id) as scope:
        assert await scope.credential(metronome.ANTHROPIC_KEY_SLOT, "ANTHROPIC_API_KEY") == (
            "sk-ant-byok"
        )


async def test_byok_labels_only_anthropic_served_host_tokens(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-workspace-own")
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=200)
        )
        await record_workspace_usage(connection, workspace_id, "gpt-5", Usage(input_tokens=300))
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    labels = {
        (event["properties"]["dimension"], event["properties"]["model"]): event["properties"][
            "byok"
        ]
        for event in _events(recorder.requests[0])
    }
    assert labels == {
        ("tokens", MODEL): "true",
        ("sandbox_tokens", MODEL): "false",
        ("tokens", "gpt-5"): "false",
    }


async def test_byok_label_is_frozen_at_mint_across_a_key_change(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intent minted before the key was stored re-sends byte-identical after it: the label is
    the key state that served the usage, never the drain-time state."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(connection, workspace_id, turn_id, MODEL, Usage(input_tokens=100))
    failing = _Recorder(status=500)
    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _shipper(failing).run()
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-added-mid-outage")
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    (failed_event,) = _events(failing.requests[0])
    (event,) = _events(recorder.requests[0])
    assert event == failed_event
    assert event["properties"]["byok"] == "false"


async def test_byok_follows_the_serving_providers_stored_key(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Bedrock-served model labels from the bedrock slot, never the anthropic one: storing
    `anthropic_api_key` does not make Bedrock usage BYOK, and storing `bedrock_api_key` does —
    the label follows whichever key `client_for` would actually resolve for the model."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-own")
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, "anthropic.claude-opus-4-8", Usage(input_tokens=10)
        )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    (event,) = _events(recorder.requests[0])
    assert event["properties"]["model"] == "anthropic.claude-opus-4-8"
    assert event["properties"]["byok"] == "false"
    await store.put(workspace_id, bedrock.BEDROCK_KEY_SLOT, "bedrock-bearer-own")
    second_turn = await _turn(workspace_id, conversation_id, agent_id, seq=2)
    await _settle(second_turn, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection,
            workspace_id,
            second_turn,
            "anthropic.claude-opus-4-8",
            Usage(input_tokens=10),
        )
    with ws(workspace_id):
        await _shipper(recorder).run()
    (bedrock_event,) = _events(recorder.requests[1])
    assert bedrock_event["properties"]["byok"] == "true"


class _RecordingInvoker:
    """A TurnInvoker riding the REAL Admission producer: the approval turn lands as a durable
    turn row asserted below — the recorder only binds the workspace the way serve's
    invoker_factory does."""

    def __init__(self, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.admission = Admission(dbos=_ApprovalStubDbos(), durable_surfaces=frozenset())

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID:
        return await self.admission.invoke(
            self.workspace_id, conversation_id, agent_id, message, idempotency_key
        )


@dataclass
class _ApprovalStubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        return None


async def test_seat_approvals_ask_the_owner_once_per_unseated_member(db: None) -> None:
    workspace_id, owner_id, _joiner_id = await _seat_seed(limit=25)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=1, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
    agent_id, conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="slack",
                queue_key="dm-owner",
                member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = context_for(metronome.NAME, frozenset(), invoker=_RecordingInvoker(workspace_id))
    with ws(workspace_id):
        await metronome.SeatApprovals(ctx=context).run()
        await metronome.SeatApprovals(ctx=context).run()
    async with workspace_tx() as connection:
        asks = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.conversation_id == conversation_id
                )
            )
        ).all()
    assert len(asks) == 1
    assert "late@example.com" in asks[0].inbound
    assert "ask_user" in asks[0].inbound
    assert "grant_seat" in asks[0].inbound
    assert "overage" in asks[0].inbound


async def test_seat_approvals_wait_for_an_owner_conversation(db: None) -> None:
    workspace_id, _owner_id, _joiner_id = await _seat_seed(limit=25)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=1, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
    context = context_for(metronome.NAME, frozenset(), invoker=_RecordingInvoker(workspace_id))
    with ws(workspace_id):
        await metronome.SeatApprovals(ctx=context).run()
    marker = f"{metronome.SEAT_APPROVAL_KEY_PREFIX}late@example.com"
    with ws(workspace_id):
        assert await context.store.get(marker) is None


async def test_seat_approvals_stay_silent_while_an_included_seat_is_open(db: None) -> None:
    workspace_id, owner_id, _joiner_id = await _seat_seed(limit=25)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=5, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=uuid4(),
                workspace_id=workspace_id,
                surface="slack",
                queue_key="dm-owner-open",
                member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = context_for(metronome.NAME, frozenset(), invoker=_RecordingInvoker(workspace_id))
    with ws(workspace_id):
        await metronome.SeatApprovals(ctx=context).run()
        assert (
            await context.store.get(f"{metronome.SEAT_APPROVAL_KEY_PREFIX}late@example.com") is None
        )


async def test_revoke_marks_the_member_as_decided_for_the_approval_job(
    db: None, tmp_path: Path
) -> None:
    workspace_id, owner_id, joiner_id = await _seat_seed(limit=25)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=2, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.id == joiner_id)
        )
    await _run_tool(
        workspace_id, tmp_path, owner_id, metronome.REVOKE_SEAT_TOOL, email="late@example.com"
    )
    _registry_tools, ext_by_tool = _seat_tools()
    ext = ext_by_tool[metronome.REVOKE_SEAT_TOOL]
    with ws(workspace_id):
        marker = await ext.store.get(f"{metronome.SEAT_APPROVAL_KEY_PREFIX}late@example.com")
    assert marker is not None
