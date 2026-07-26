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
from urllib.parse import parse_qsl
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

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
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


def test_manifest_declares_four_cron_jobs_four_tools_two_sections() -> None:
    declared = metronome.manifest()
    assert declared.name == "metronome"
    usage, seats, approvals, billing = declared.jobs
    assert usage.name == "usage_shipper"
    assert usage.schedule == "0 * * * * *"
    assert usage.handler is metronome._ship
    assert seats.name == "seat_shipper"
    assert seats.schedule == "0 0 * * * *"
    assert seats.handler is metronome._ship_seats
    assert approvals.name == "seat_approvals"
    assert approvals.schedule == "30 * * * * *"
    assert approvals.handler is metronome._ask_seat_approvals
    assert billing.name == "billing_activation"
    assert billing.schedule == "45 * * * * *"
    assert billing.handler is metronome._activate_billing
    assert [tool.name for tool in declared.tools] == [
        "grant_seat",
        "revoke_seat",
        "list_seats",
        "manage_billing",
    ]
    assert all(tool.side_effecting for tool in declared.tools[:2])
    assert not declared.tools[2].side_effecting
    assert declared.tools[3].side_effecting
    assert [section.name for section in declared.prompt_sections] == ["seats", "billing"]
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
    for workspace_id in await runner.candidates(f"{metronome.NAME}:{metronome.JOB_NAME}"):
        await runner.fire(f"{metronome.NAME}:{metronome.JOB_NAME}", workspace_id)
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
    workspace_id: UUID,
    ext: ExtensionContext,
    tmp_path: Path,
    member_id: UUID | None,
    audience_member_id: UUID | None,
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
        audience_member_id=audience_member_id,
        artifact_token_secret="",
        ext=ext,
    )


async def _run_tool(
    workspace_id: UUID, tmp_path: Path, member_id: UUID | None, name: str, **args: object
) -> dict[str, object]:
    registry, ext_by_tool = _seat_tools()
    tool = registry[name]
    ctx = _tool_context(workspace_id, ext_by_tool[name], tmp_path, member_id, member_id)
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
    for workspace_id in await runner.candidates(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}"):
        await runner.fire(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}", workspace_id)
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
    for workspace_id in await runner.candidates(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}"):
        await runner.fire(f"{metronome.NAME}:{metronome.SEAT_JOB_NAME}", workspace_id)
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


STRIPE_KEY = "sk_test_0xfeedface"
PORTAL_CONFIGURATION = "bpc_test_config"
PACKAGE_ALIAS = "Base Plan"
SAVED_CARD = "pm_card_visa"


def _billing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(metronome.STRIPE_SECRET_KEY_ENV, STRIPE_KEY)
    monkeypatch.setenv(metronome.STRIPE_PORTAL_CONFIGURATION_ENV, PORTAL_CONFIGURATION)
    monkeypatch.setenv(metronome.METRONOME_PACKAGE_ALIAS_ENV, PACKAGE_ALIAS)


class _Providers:
    """Stripe and Metronome behind one MockTransport: it records every request and answers from
    provider state the test drives — whether a card is on file, whether the ingest alias is already
    taken, which path is failing. The stand-in is never the thing asserted; the assertions read the
    recorded requests, the returned links, and the durable record."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.default_payment_method: str | None = None
        self.stripe_customers: dict[str, str] = {}
        self.metronome_customers: dict[str, str] = {}
        self.contracts: dict[str, list[dict[str, str]]] = {}
        self.uniqueness_keys: set[str] = set()
        self.hidden_aliases: set[str] = set()
        self.hidden_contracts: set[str] = set()
        self.failing: set[str] = set()
        self.sessions = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.failing:
            return httpx.Response(500, json={"message": "provider is down"})
        if request.url.host == "api.stripe.com":
            return self._stripe(request, path)
        return self._metronome(request, path)

    def _stripe(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/v1/customers" and request.method == "POST":
            customer_id = self.stripe_customers.setdefault(
                request.headers["idempotency-key"], f"cus_{len(self.stripe_customers) + 1}"
            )
            return httpx.Response(200, json={"id": customer_id})
        if path.startswith("/v1/customers/") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[-1],
                    "invoice_settings": {"default_payment_method": self.default_payment_method},
                },
            )
        if path == "/v1/billing_portal/sessions" and request.method == "POST":
            self.sessions += 1
            return httpx.Response(
                200, json={"url": f"https://billing.stripe.com/session/{self.sessions}"}
            )
        raise AssertionError(f"unexpected stripe call {request.method} {path}")

    def _metronome(self, request: httpx.Request, path: str) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/customers" and request.method == "GET":
            alias = request.url.params["ingest_alias"]
            found = self.metronome_customers.get(alias)
            hidden = alias in self.hidden_aliases
            self.hidden_aliases.discard(alias)
            data = [] if found is None or hidden else [{"id": found}]
            return httpx.Response(200, json={"data": data})
        if path == "/v1/customers" and request.method == "POST":
            (alias,) = body["ingest_aliases"]
            if alias in self.metronome_customers:
                return httpx.Response(409, json={"message": "ingest alias already in use"})
            customer_id = f"mc_{len(self.metronome_customers) + 1}"
            self.metronome_customers[alias] = customer_id
            return httpx.Response(200, json={"data": {"id": customer_id}})
        if path == metronome.CONTRACTS_LIST_PATH and request.method == "POST":
            customer_id = body["customer_id"]
            listed = [
                contract
                for contract in self.contracts.get(customer_id, ())
                if contract["uniqueness_key"] not in self.hidden_contracts
            ]
            self.hidden_contracts.clear()
            return httpx.Response(200, json={"data": listed})
        if path == "/v1/contracts/create" and request.method == "POST":
            key = body["uniqueness_key"]
            if key in self.uniqueness_keys:
                return httpx.Response(
                    409, json={"message": "This uniqueness key has already been used."}
                )
            contract_id = f"ct_{sum(len(held) for held in self.contracts.values()) + 1}"
            self.hold_contract(body["customer_id"], contract_id, key)
            return httpx.Response(200, json={"data": {"id": contract_id}})
        raise AssertionError(f"unexpected metronome call {request.method} {path}")

    def hold_contract(self, customer_id: str, contract_id: str, uniqueness_key: str) -> None:
        """Record a contract the way Metronome does: on the customer, carrying the uniqueness key
        that created it — the field a later read identifies its owner by. Tests seed pre-existing
        contracts through this too, so a seeded one is indistinguishable from a created one."""
        self.uniqueness_keys.add(uniqueness_key)
        self.contracts.setdefault(customer_id, []).append(
            {"id": contract_id, "uniqueness_key": uniqueness_key}
        )


def _calls(providers: _Providers, method: str, path: str) -> list[httpx.Request]:
    return [r for r in providers.requests if r.method == method and r.url.path == path]


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: value for key, value in parse_qsl(request.content.decode())}


async def _billing_seed() -> tuple[UUID, UUID, UUID, UUID]:
    """A workspace with an owner, a teammate, the default agent, and the owner's own conversation —
    the venue the activation job notifies into."""
    workspace_id, owner_id, mate_id = uuid4(), uuid4(), uuid4()
    agent_id, conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member_id, email, created in (
            (owner_id, "owner@example.com", datetime(2026, 1, 1, tzinfo=UTC)),
            (mate_id, "mate@example.com", datetime(2026, 6, 1, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    seated_at=created,
                    created_at=created,
                    updated_at=created,
                )
            )
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
                surface="ufo",
                queue_key="dm-owner",
                member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, owner_id, mate_id, conversation_id


def _billing_tool() -> tuple[ToolDef, ExtensionContext]:
    declared, ext_by_tool = turn_tools(
        (metronome.manifest(),), CredentialStore(fernet=Fernet(Fernet.generate_key()))
    )
    tool = next(t for t in declared if t.name == metronome.MANAGE_BILLING_TOOL)
    return tool, ext_by_tool[metronome.MANAGE_BILLING_TOOL]


async def _manage_billing(
    workspace_id: UUID,
    tmp_path: Path,
    speaker: UUID | None,
    audience: UUID | None,
    action: str,
) -> dict[str, object]:
    tool, ext = _billing_tool()
    ctx = _tool_context(workspace_id, ext, tmp_path, speaker, audience)
    with ws(workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate({"action": action}))
    return json.loads(result.content[0].text)


async def _stored_record(workspace_id: UUID) -> metronome.BillingRecord | None:
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        stored = await ctx.store.get(metronome.BILLING_KEY)
    return None if stored is None else metronome.BillingRecord.model_validate(stored)


def _activation(workspace_id: UUID, providers: _Providers) -> metronome.BillingActivation:
    return metronome.BillingActivation(
        ctx=context_for(metronome.NAME, frozenset(), invoker=_RecordingInvoker(workspace_id)),
        transport=providers.transport,
    )


async def _owner_turns(conversation_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.conversation_id == conversation_id
                )
            )
        ).all()
    return [row.inbound for row in rows]


async def test_setup_returns_the_payment_method_portal_link_and_records_pending_work(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole owner-facing act: one Stripe Customer under a workspace-deterministic idempotency
    key, one portal session narrowed to the payment-method flow, and a durable pending record the
    activation job owns — all before the owner is handed the link."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    payload = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    (customer_call,) = _calls(providers, "POST", "/v1/customers")
    assert customer_call.url.host == "api.stripe.com"
    assert customer_call.headers["authorization"] == f"Bearer {STRIPE_KEY}"
    assert customer_call.headers["idempotency-key"] == f"ufo-stripe-customer:{workspace_id}"
    assert _form(customer_call)["metadata[workspace_id]"] == str(workspace_id)

    (session_call,) = _calls(providers, "POST", "/v1/billing_portal/sessions")
    assert _form(session_call) == {
        "customer": "cus_1",
        "configuration": PORTAL_CONFIGURATION,
        "flow_data[type]": "payment_method_update",
    }
    assert payload == {
        "portal_url": "https://billing.stripe.com/session/1",
        "stripe_customer_id": "cus_1",
        "package": PACKAGE_ALIAS,
    }

    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.stripe_customer_id == "cus_1"
    assert record.package_alias == PACKAGE_ALIAS
    assert record.metronome_customer_id is None
    assert record.metronome_contract_id is None
    assert record.activated_at is None
    assert not [r for r in providers.requests if "subscription" in r.url.path]


async def test_billing_is_refused_off_the_owners_private_conversation(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every gate runs before any provider call: a teammate, a speakerless turn, and the owner
    speaking to a shared audience are all refused with nothing sent and nothing recorded."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with pytest.raises(ValueError, match="only the workspace owner"):
        await _manage_billing(workspace_id, tmp_path, mate_id, mate_id, "setup")
    with pytest.raises(ValueError, match="speaking member"):
        await _manage_billing(workspace_id, tmp_path, None, None, "setup")
    with pytest.raises(ValueError, match="private conversation"):
        await _manage_billing(workspace_id, tmp_path, owner_id, None, "setup")
    with pytest.raises(ValueError, match="private conversation"):
        await _manage_billing(workspace_id, tmp_path, owner_id, mate_id, "portal")

    assert providers.requests == []
    assert await _stored_record(workspace_id) is None


async def test_the_job_waits_for_a_saved_card_then_provisions_customer_and_contract(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing happens on Metronome until Stripe reports a default payment method; once it does, the
    customer carries the workspace UUID as its ingest alias with the Stripe automatic-collection
    configuration, the contract comes from the configured package under a stable uniqueness key, and
    the owner hears once."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    assert [r.url.host for r in providers.requests if r.url.host == "api.metronome.com"] == []
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_customer_id is None
    assert await _owner_turns(conversation_id) == []

    providers.default_payment_method = SAVED_CARD
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    (created,) = _calls(providers, "POST", "/v1/customers")[1:]
    body = json.loads(created.content)
    assert created.headers["idempotency-key"] == f"ufo-metronome-customer:{workspace_id}"
    assert body["ingest_aliases"] == [str(workspace_id)]
    assert body["customer_billing_provider_configurations"] == [
        {
            "billing_provider": "stripe",
            "delivery_method": "direct_to_billing_provider",
            "configuration": {
                "stripe_customer_id": "cus_1",
                "stripe_collection_method": "charge_automatically",
            },
        }
    ]
    (contract,) = _calls(providers, "POST", "/v1/contracts/create")
    contract_body = json.loads(contract.content)
    assert contract_body["customer_id"] == "mc_1"
    assert contract_body["package_alias"] == PACKAGE_ALIAS
    assert contract_body["uniqueness_key"] == f"ufo-contract:{workspace_id}"
    assert "package_id" not in contract_body

    record = await _stored_record(workspace_id)
    assert record is not None
    assert contract_body["starting_at"] == record.contract_starting_at.isoformat()
    assert record.metronome_customer_id == "mc_1"
    assert record.metronome_contract_id == "ct_1"
    assert record.activated_at is not None
    (told,) = await _owner_turns(conversation_id)
    assert "billing activated" in told
    assert PACKAGE_ALIAS in told


async def test_repeated_setups_and_ticks_never_duplicate_a_customer_or_contract(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idempotence proof: two setups and four ticks leave exactly one Stripe customer, one
    Metronome customer, one contract, and one notification."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
        await _activation(workspace_id, providers).run()
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
        await _activation(workspace_id, providers).run()

    stripe_creates = [
        r for r in _calls(providers, "POST", "/v1/customers") if r.url.host == "api.stripe.com"
    ]
    metronome_creates = [
        r for r in _calls(providers, "POST", "/v1/customers") if r.url.host == "api.metronome.com"
    ]
    assert len(stripe_creates) == 1
    assert stripe_creates[0].headers["idempotency-key"] == f"ufo-stripe-customer:{workspace_id}"
    assert providers.stripe_customers == {f"ufo-stripe-customer:{workspace_id}": "cus_1"}
    assert len(metronome_creates) == 1
    assert len(_calls(providers, "POST", "/v1/contracts/create")) == 1
    assert len(_calls(providers, "POST", "/v1/billing_portal/sessions")) == 2
    assert len(await _owner_turns(conversation_id)) == 1
    record = await _stored_record(workspace_id)
    assert record is not None
    assert (record.metronome_customer_id, record.metronome_contract_id) == ("mc_1", "ct_1")


async def test_a_conflicting_create_reconciles_to_the_object_that_already_exists(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing tick already holds the ingest alias: the create 409s and the job settles on the
    existing customer, under the same key it first tried — a key is never rotated past a
    conflict."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.metronome_customers[str(workspace_id)] = "mc_racer"
    providers.hidden_aliases.add(str(workspace_id))
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    metronome_creates = [
        r for r in _calls(providers, "POST", "/v1/customers") if r.url.host == "api.metronome.com"
    ]
    assert len(metronome_creates) == 1
    assert metronome_creates[0].headers["idempotency-key"] == (
        f"ufo-metronome-customer:{workspace_id}"
    )
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_customer_id == "mc_racer"
    assert record.metronome_contract_id == "ct_1"


async def test_an_existing_contract_is_adopted_rather_than_created_twice(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace whose record was lost after provisioning: both provider objects already exist, so
    a tick adopts them and creates nothing."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.metronome_customers[str(workspace_id)] = "mc_old"
    providers.hold_contract("mc_old", "ct_old", f"ufo-contract:{workspace_id}")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    assert [
        r for r in _calls(providers, "POST", "/v1/customers") if r.url.host == "api.metronome.com"
    ] == []
    assert _calls(providers, "POST", "/v1/contracts/create") == []
    record = await _stored_record(workspace_id)
    assert record is not None
    assert (record.metronome_customer_id, record.metronome_contract_id) == ("mc_old", "ct_old")


async def test_a_failed_provider_call_leaves_pending_work_for_the_next_tick(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract creation fails: the customer already recorded survives, activation does not happen,
    the owner is not told, and the next healthy tick finishes from exactly there."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.failing.add("/v1/contracts/create")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _activation(workspace_id, providers).run()
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_customer_id == "mc_1"
    assert record.metronome_contract_id is None
    assert record.activated_at is None
    assert await _owner_turns(conversation_id) == []

    providers.failing.clear()
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_contract_id == "ct_1"
    assert record.activated_at is not None
    assert len(await _owner_turns(conversation_id)) == 1
    assert (
        len(
            [
                r
                for r in _calls(providers, "POST", "/v1/customers")
                if r.url.host == "api.metronome.com"
            ]
        )
        == 1
    )


async def test_activation_waits_for_an_owner_conversation_before_marking_active(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No venue to speak into yet: the providers are provisioned but the record stays pending, so
    the owner is told on a later tick instead of never."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(member_id=None, updated_at=sa.func.now())
            .where(tables.conversation.c.workspace_id == workspace_id)
        )

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_contract_id == "ct_1"
    assert record.activated_at is None


async def test_status_reads_provider_truth_and_portal_opens_a_full_session(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`status` answers from the providers, not from our record — an unconfigured workspace, then a
    card-less one, then a live plan — and `portal` mints a fresh unnarrowed session."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    assert await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status") == {
        "configured": False
    }
    with pytest.raises(ValueError, match="not set up"):
        await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")

    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    pending = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")
    assert pending == {
        "configured": True,
        "stripe_customer_id": "cus_1",
        "payment_method_on_file": False,
        "package": PACKAGE_ALIAS,
        "metronome_customer_id": None,
        "metronome_contract_id": None,
        "plan_active": False,
    }

    providers.default_payment_method = SAVED_CARD
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    live = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")
    assert live == {
        "configured": True,
        "stripe_customer_id": "cus_1",
        "payment_method_on_file": True,
        "package": PACKAGE_ALIAS,
        "metronome_customer_id": "mc_1",
        "metronome_contract_id": "ct_1",
        "plan_active": True,
    }

    portal = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")
    session_calls = _calls(providers, "POST", "/v1/billing_portal/sessions")
    assert _form(session_calls[-1]) == {
        "customer": "cus_1",
        "configuration": PORTAL_CONFIGURATION,
    }
    assert portal == {"portal_url": f"https://billing.stripe.com/session/{providers.sessions}"}


async def test_billing_job_fires_through_job_runner(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest's job over the real JobRunner: candidates find the workspace, the dispatcher
    binds it, and a workspace with no billing record is a silent no-op."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    unconfigured, _, _ = await _seed()

    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    runner = JobRunner(
        bindings=bindings_from((metronome.manifest(),), ()),
        registry=_registry(),
        invoker_factory=_RecordingInvoker,
    )
    job = f"{metronome.NAME}:{metronome.BILLING_JOB_NAME}"
    candidates = await runner.candidates(job)
    assert {workspace_id, unconfigured} <= set(candidates)
    for candidate in candidates:
        await runner.fire(job, candidate)

    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_contract_id == "ct_1"
    assert len(await _owner_turns(conversation_id)) == 1
    assert await _stored_record(unconfigured) is None


async def test_a_lost_activation_mark_re_notifies_into_the_same_turn(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notify-once claim under the failure it is built for: the invoke landed but the mark that
    records it did not. The next tick re-invokes under the same idempotency key, so real admission
    collapses it onto the turn the owner already has instead of telling them twice."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    told = await _owner_turns(conversation_id)
    assert len(told) == 1

    activated = await _stored_record(workspace_id)
    assert activated is not None
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        await ctx.store.put(
            metronome.BILLING_KEY,
            activated.model_copy(update={"activated_at": None}).model_dump(mode="json"),
        )
        await _activation(workspace_id, providers).run()

    assert await _owner_turns(conversation_id) == told
    remarked = await _stored_record(workspace_id)
    assert remarked is not None
    assert remarked.activated_at is not None
    assert len(_calls(providers, "POST", "/v1/contracts/create")) == 1


async def test_a_foreign_contract_on_the_customer_is_never_adopted_as_the_plan(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterexample to "one customer, one contract": a Metronome customer can already carry a
    contract this workspace never asked for — an operator-provisioned trial, a hand-built plan.
    Adopting it by position would record a plan the workspace does not have AND suppress the create
    that would give it one, so the owner is told billing is live while nothing was bought. The
    workspace's own contract is the one carrying our uniqueness key, and nothing else is."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.metronome_customers[str(workspace_id)] = "mc_shared"
    providers.hold_contract("mc_shared", "ct_operator_trial", "operator:hand-built-trial")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    (created,) = _calls(providers, "POST", "/v1/contracts/create")
    assert json.loads(created.content)["uniqueness_key"] == f"ufo-contract:{workspace_id}"
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_contract_id != "ct_operator_trial"
    assert record.metronome_contract_id == "ct_2"
    assert record.activated_at is not None
    assert len(await _owner_turns(conversation_id)) == 1

    status = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")
    assert status["metronome_contract_id"] == "ct_2"
    assert status["plan_active"] is True

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    assert len(_calls(providers, "POST", "/v1/contracts/create")) == 1


async def test_status_reports_no_plan_while_only_a_foreign_contract_exists(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read half of the same counterexample: a foreign contract must never make `status` claim
    the workspace has a plan."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.metronome_customers[str(workspace_id)] = "mc_shared"
    providers.hold_contract("mc_shared", "ct_operator_trial", "operator:hand-built-trial")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    ctx = context_for(metronome.NAME, frozenset())
    stored = await _stored_record(workspace_id)
    assert stored is not None
    with ws(workspace_id):
        await ctx.store.put(
            metronome.BILLING_KEY,
            stored.model_copy(update={"metronome_customer_id": "mc_shared"}).model_dump(
                mode="json"
            ),
        )

    status = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")
    assert status["metronome_contract_id"] is None
    assert status["plan_active"] is False


async def test_a_conflicting_contract_create_reconciles_to_our_own_contract(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing tick already created our contract but the list read has not caught up: the create
    409s on our permanent uniqueness key and the retry read resolves to that same contract, beside a
    foreign one it must not confuse it with. The key is never rotated to get past the conflict."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    providers.metronome_customers[str(workspace_id)] = "mc_shared"
    providers.hold_contract("mc_shared", "ct_operator_trial", "operator:hand-built-trial")
    providers.hold_contract("mc_shared", "ct_ours", f"ufo-contract:{workspace_id}")
    providers.hidden_contracts.add(f"ufo-contract:{workspace_id}")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    (attempt,) = _calls(providers, "POST", "/v1/contracts/create")
    assert json.loads(attempt.content)["uniqueness_key"] == f"ufo-contract:{workspace_id}"
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.metronome_contract_id == "ct_ours"
    assert record.activated_at is not None


async def test_a_failed_stripe_customer_create_records_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of durable-pending-work: when Stripe itself fails there is nothing to resume
    from, so setup must leave no record at all rather than a half-built one the job would act on."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.failing.add("/v1/customers")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with pytest.raises(metronome.StripeError, match="500"):
        await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    assert await _stored_record(workspace_id) is None
    assert _calls(providers, "POST", "/v1/billing_portal/sessions") == []

    providers.failing.clear()
    payload = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    assert payload["stripe_customer_id"] == "cus_1"
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.stripe_customer_id == "cus_1"


def test_billing_config_names_every_missing_setting_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-configured deploy learns all four names in one error, not one per attempt."""
    for name in (
        metronome.STRIPE_SECRET_KEY_ENV,
        metronome.STRIPE_PORTAL_CONFIGURATION_ENV,
        metronome.METRONOME_BEARER_TOKEN_ENV,
        metronome.METRONOME_PACKAGE_ALIAS_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(
        RuntimeError,
        match=(
            "billing requires STRIPE_SECRET_KEY, STRIPE_BILLING_PORTAL_CONFIGURATION_ID, "
            "METRONOME_BEARER_TOKEN, METRONOME_PACKAGE_ALIAS"
        ),
    ):
        metronome.BillingConfig.from_env()
    monkeypatch.setenv(metronome.STRIPE_SECRET_KEY_ENV, STRIPE_KEY)
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    with pytest.raises(
        RuntimeError,
        match="billing requires STRIPE_BILLING_PORTAL_CONFIGURATION_ID, METRONOME_PACKAGE_ALIAS",
    ):
        metronome.BillingConfig.from_env()


async def test_a_half_configured_deploy_creates_no_provider_object(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuration is validated before the first provider call, so a deploy missing only the
    portal configuration cannot strand a Stripe Customer it will never hand a link for."""
    _billing_env(monkeypatch)
    monkeypatch.delenv(metronome.STRIPE_PORTAL_CONFIGURATION_ENV)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with pytest.raises(RuntimeError, match="STRIPE_BILLING_PORTAL_CONFIGURATION_ID"):
        await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    assert providers.requests == []
    assert await _stored_record(workspace_id) is None


async def test_an_unconfigured_deploy_leaves_the_billing_job_silent(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deploy that meters usage without selling a plan has no billing record, so the job returns
    before it ever reads a billing setting — no alarm every minute for a fleet that never opted in.
    The usage shipper keeps working on the bearer token alone."""
    for name in (
        metronome.STRIPE_SECRET_KEY_ENV,
        metronome.STRIPE_PORTAL_CONFIGURATION_ENV,
        metronome.METRONOME_PACKAGE_ALIAS_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    assert providers.requests == []


async def test_every_stripe_call_pins_the_api_version(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stripe-side default-version bump can never reshape a response under us."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")

    stripe_calls = [r for r in providers.requests if r.url.host == "api.stripe.com"]
    assert stripe_calls
    assert {r.headers["stripe-version"] for r in stripe_calls} == {metronome.STRIPE_API_VERSION}
    assert not any(
        "stripe-version" in r.headers for r in providers.requests if r not in stripe_calls
    )


async def test_the_contract_start_is_fixed_at_setup_and_replayed_verbatim(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract start is captured once, at setup, so a provisioning attempt days later sends the
    same `starting_at` the first attempt would have — a retry can never trip the uniqueness key on
    mismatched parameters, however far apart the attempts fall. It is captured already truncated
    because both constraints are provider-enforced — a package contract must begin on an hour
    boundary and Metronome rejects microsecond precision — so what is stored and what is sent are
    one string; the live provider smoke is what proved those two rules."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    record = await _stored_record(workspace_id)
    assert record is not None
    fixed = record.contract_starting_at
    assert (fixed.minute, fixed.second, fixed.microsecond) == (0, 0, 0)
    assert fixed.isoformat().endswith(":00:00+00:00")
    ctx = context_for(metronome.NAME, frozenset())
    long_ago = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
    with ws(workspace_id):
        await ctx.store.put(
            metronome.BILLING_KEY,
            record.model_copy(update={"contract_starting_at": long_ago}).model_dump(mode="json"),
        )
    assert fixed >= datetime(2026, 1, 1, tzinfo=UTC)

    providers.default_payment_method = SAVED_CARD
    with ws(workspace_id):
        await _activation(workspace_id, providers).run()

    (created,) = _calls(providers, "POST", "/v1/contracts/create")
    assert json.loads(created.content)["starting_at"] == long_ago.isoformat()

    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")
    replayed = await _stored_record(workspace_id)
    assert replayed is not None
    assert replayed.contract_starting_at == long_ago


async def test_a_setup_overlapping_the_job_never_reverts_provisioned_ids(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race a read-modify-write invites: the activation job's write lands in the window between
    setup reading the record and setup writing it back, so writing back would revert provider ids
    setup never saw. Setup writes only when creating the record, so the window holds nothing to
    revert — the ids and the activation mark survive, and the owner still gets a fresh link."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    provisioned = await _stored_record(workspace_id)
    assert provisioned is not None
    landed = provisioned.model_copy(
        update={
            "metronome_customer_id": "mc_job",
            "metronome_contract_id": "ct_job",
            "activated_at": datetime(2026, 7, 25, 23, 30, tzinfo=UTC),
        }
    )
    ctx = context_for(metronome.NAME, frozenset())
    read_record = metronome._billing_record

    async def _job_lands_after_the_read(
        ext: ExtensionContext,
    ) -> metronome.BillingRecord | None:
        record = await read_record(ext)
        if record is not None and record.metronome_customer_id is None:
            await ctx.store.put(metronome.BILLING_KEY, landed.model_dump(mode="json"))
        return record

    monkeypatch.setattr(metronome, "_billing_record", _job_lands_after_the_read)
    payload = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "setup")

    assert payload["stripe_customer_id"] == provisioned.stripe_customer_id
    monkeypatch.setattr(metronome, "_billing_record", read_record)
    after = await _stored_record(workspace_id)
    assert after is not None
    assert after.metronome_customer_id == "mc_job"
    assert after.metronome_contract_id == "ct_job"
    assert after.activated_at is not None
