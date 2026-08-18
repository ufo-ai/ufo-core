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
from dataclasses import dataclass, replace
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
from ufo.balance import (
    TOPUP_GRACE_MICRO_USD,
    credit,
    debit,
    mark_topup_verified,
    read_balance,
    read_headroom,
    set_reserve,
)
from ufo.bearer import UFO_TOKEN_SECRET_ENV, mint_token
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
from ufo.sdk.audience import Audience, conversation_audience
from ufo.sdk.http import Request
from ufo.surfaces.admission import Admission
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import init_workspace_credentials, ws

DOLLAR = 1_000_000
TOOL_NARRATION = "checking their billing"

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
        self.customers: dict[str, str] = {}
        self.hidden_once: set[str] = set()
        self.hidden_always: set[str] = set()
        self.forbid_customers = False
        self.failing: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/customers":
            if request.url.path in self.failing:
                return httpx.Response(500, json={"message": "provider is down"})
            if self.forbid_customers:
                return httpx.Response(403, json={"message": "token cannot manage customers"})
            return self._customer(request)
        return httpx.Response(self._status, json={})

    def _customer(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            alias = request.url.params["ingest_alias"]
            if alias in self.hidden_always:
                return httpx.Response(200, json={"data": []})
            if alias in self.hidden_once:
                self.hidden_once.discard(alias)
                return httpx.Response(200, json={"data": []})
            found = self.customers.get(alias)
            return httpx.Response(200, json={"data": [] if found is None else [{"id": found}]})
        (alias,) = json.loads(request.content)["ingest_aliases"]
        if alias in self.customers:
            return httpx.Response(409, json={"message": "ingest alias already in use"})
        customer_id = self.customers.setdefault(alias, f"mc_{len(self.customers) + 1}")
        return httpx.Response(200, json={"data": {"id": customer_id}})

    def ingests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/v1/ingest"]


def _registry() -> ModelRegistry:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite://"),
        blob=BlobConfig(backend="filesystem", root=Path()),
    )
    return model_registry(config, (bedrock.manifest(),))


def _shipper(recorder: _Recorder) -> metronome.UsageShipper:
    return metronome.UsageShipper(
        ctx=context_for(
            metronome.NAME,
            frozenset((metronome.ANTHROPIC_KEY_SLOT,)),
            model_resolver=_registry(),
            model_job=f"{metronome.NAME}:{metronome.JOB_NAME}",
        ),
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
                agent_id=agent_id,
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


def test_manifest_declares_two_cron_jobs_one_tool_one_section() -> None:
    declared = metronome.manifest()
    assert declared.name == "metronome"
    usage, topup = declared.jobs
    assert usage.name == "usage_shipper"
    assert usage.schedule == "0 * * * * *"
    assert usage.handler is metronome._ship
    assert topup.name == "balance_topup"
    assert topup.schedule == "0 * * * * *"
    assert topup.handler is metronome._top_up
    assert [tool.name for tool in declared.tools] == ["manage_billing"]
    assert all(tool.side_effecting for tool in declared.tools)
    assert [section.name for section in declared.prompt_sections] == ["billing"]
    (slot,) = declared.credentials
    assert slot.name == "anthropic_api_key"
    assert slot.injection is None
    (billing_route,) = declared.routes
    assert (billing_route.method, billing_route.path) == ("GET", metronome.BILLING_ROUTE_PATH)
    assert billing_route.handler is metronome._billing_projection


async def test_ships_settled_rows_with_exact_events(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            MODEL,
            Usage(input_tokens=1000, output_tokens=500),
        )
        await record_workspace_usage(connection, workspace_id, MODEL, Usage(input_tokens=250))
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()

    (request,) = recorder.ingests()
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
    assert len(recorder.ingests()) == 1


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
    assert recorder.ingests() == []

    await _settle(turn_id, age_seconds=0)
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert recorder.ingests() == []

    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)
    with ws(workspace_id):
        await _shipper(recorder).run()
    (request,) = recorder.ingests()
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
    (top_up,) = _events(recorder.ingests()[1])
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
    (lost,) = _events(failing.ingests()[0])

    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=40)
        )
    await _settle(turn_id, age_seconds=PAST_MARGIN_SECONDS)

    succeeding = _Recorder()
    with ws(workspace_id):
        await _shipper(succeeding).run()
    frozen, top_up = sorted(
        _events(succeeding.ingests()[0]), key=lambda e: int(e["transaction_id"].split(":")[1])
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
    (request,) = recorder.ingests()
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
    assert [len(_events(request)) for request in recorder.ingests()] == [100, 50]
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
    assert recorder.ingests() == []


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
    assert recorder.ingests() == []
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
    (request,) = recorder.ingests()
    (event,) = _events(request)
    assert event["customer_id"] == str(workspace_id)
    assert await _acked() != set()


def _tool_context(
    workspace_id: UUID,
    ext: ExtensionContext,
    tmp_path: Path,
    member_id: UUID | None,
    audience: Audience,
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
            inbound="set up billing",
            created_at=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model=MODEL),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=audience,
        artifact_token_secret="",
        ext=ext,
    )


async def test_byok_label_flips_with_the_stored_key_and_stays_per_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id, agent_id, conversation_id = await _seed()
    other_workspace, other_agent, other_conversation = await _seed()
    await store.put(workspace_id, metronome.ANTHROPIC_KEY_SLOT, "sk-ant-workspace-own")
    for ws_id, conv_id, ag_id in (
        (workspace_id, conversation_id, agent_id),
        (other_workspace, other_conversation, other_agent),
    ):
        turn_id = await _turn(ws_id, conv_id, ag_id)
        await _settle(turn_id, age_seconds=0)
        async with workspace_tx() as connection:
            await record_turn_usage(
                connection,
                ws_id,
                turn_id,
                MODEL,
                Usage(input_tokens=1000, output_tokens=500),
                byok=ws_id == workspace_id,
            )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    with ws(other_workspace):
        await _shipper(recorder).run()
    byok_events = _events(recorder.ingests()[0])
    passthrough_events = _events(recorder.ingests()[1])
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
            connection, workspace_id, second_turn, MODEL, Usage(input_tokens=50), byok=True
        )
    with ws(workspace_id):
        await _shipper(recorder).run()
    (first,) = _events(recorder.ingests()[0])
    (second,) = _events(recorder.ingests()[1])
    assert first["properties"]["byok"] == "false"
    assert second["properties"]["byok"] == "true"


async def test_the_declared_slot_opens_the_chat_seal_and_byok_resolution(db: None) -> None:
    """The whole BYOK path over real parts: the manifest's declared union lets the private
    handoff seal exactly this slot (and refuses it when metronome is absent), the fulfilled
    secret lands in the store, and model-key resolution prefers it over the platform env."""
    workspace_id, _, _ = await _seed()
    fernet = Fernet(Fernet.generate_key())
    declared = frozenset(slot.name for slot in metronome.manifest().credentials)
    requests = CredentialRequests(fernet=fernet, declared=declared, fillable=declared)
    member_id = uuid4()
    sealed = requests.seal(workspace_id, member_id, (metronome.ANTHROPIC_KEY_SLOT,))
    assert sealed
    without_metronome = CredentialRequests(
        fernet=fernet, declared=frozenset(), fillable=frozenset()
    )
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
            connection,
            workspace_id,
            turn_id,
            MODEL,
            Usage(input_tokens=1000, output_tokens=500),
            byok=True,
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
        for event in _events(recorder.ingests()[0])
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
    (failed_event,) = _events(failing.ingests()[0])
    (event,) = _events(recorder.ingests()[0])
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
            connection,
            workspace_id,
            turn_id,
            "anthropic.claude-opus-4-8",
            Usage(input_tokens=10),
        )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    (event,) = _events(recorder.ingests()[0])
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
            byok=True,
        )
    with ws(workspace_id):
        await _shipper(recorder).run()
    (bedrock_event,) = _events(recorder.ingests()[1])
    assert bedrock_event["properties"]["byok"] == "true"


class _RecordingInvoker:
    """A TurnInvoker riding the REAL Admission producer: the activation turn lands as a durable
    turn row asserted below — the recorder only binds the workspace the way serve's
    invoker_factory does."""

    def __init__(self, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset())

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        on_behalf_of_member_id: UUID | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
    ) -> UUID | None:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key,
            on_behalf_of_member_id=on_behalf_of_member_id,
            holds_work_already_done=holds_work_already_done,
            as_scheduled=as_scheduled,
            unless_member_since=unless_member_since,
            unless_member_arrival_since=unless_member_arrival_since,
        )


@dataclass
class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        return None


STRIPE_KEY = "sk_test_0xfeedface"
PORTAL_CONFIGURATION = "bpc_test_config"
SAVED_CARD = "pm_card_visa"


TOKEN_SECRET = "billing-page-secret"


def _billing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, TOKEN_SECRET)
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(metronome.STRIPE_SECRET_KEY_ENV, STRIPE_KEY)
    monkeypatch.setenv(metronome.STRIPE_PORTAL_CONFIGURATION_ENV, PORTAL_CONFIGURATION)


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
        self.hidden_aliases: set[str] = set()
        self.intents: list[dict[str, str]] = []
        self.charges: dict[str, str] = {}
        self.replayed: dict[str, tuple[int, dict[str, object]]] = {}
        self.decline = False
        self.in_flight = False
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
        if path == "/v1/payment_intents" and request.method == "POST":
            # A key whose earlier request has not answered yet is a conflict, not a decline:
            # the first request is still the one that will move the money.
            if self.in_flight:
                return httpx.Response(
                    409,
                    json={
                        "error": {"code": "idempotency_in_progress", "type": "idempotency_error"}
                    },
                )
            form = _form(request)
            self.intents.append(form)
            # A confirm reads `payment_method` from the request; it never falls back to the
            # customer's invoice default, so an omitted card has nothing to charge.
            if form.get("confirm") == "true" and not form.get("payment_method"):
                return httpx.Response(
                    400,
                    json={"error": {"message": "no payment method provided", "type": "card_error"}},
                )
            # Stripe collapses a repeat only when the caller supplies the key; without one it
            # takes the money again, which is what makes the key load-bearing here. A reused key
            # replays whatever the first answer was, a decline included, so a retry under the same
            # key can never come back a success.
            key = request.headers.get("idempotency-key", f"none-{len(self.replayed)}")
            if key in self.replayed:
                status, answer = self.replayed[key]
                return httpx.Response(status, json=answer)
            if self.decline:
                # A refused off-session charge is an HTTP 402 carrying card_declined, not a 2xx
                # with a soft status, so the caller sees an error rather than a returned intent.
                status, answer = 402, {"error": {"code": "card_declined", "type": "card_error"}}
            else:
                status, answer = (
                    200,
                    {
                        "id": self.charges.setdefault(key, f"pi_{len(self.charges) + 1}"),
                        "status": "succeeded",
                    },
                )
            self.replayed[key] = (status, answer)
            return httpx.Response(status, json=answer)
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
        raise AssertionError(f"unexpected metronome call {request.method} {path}")


def _calls(providers: _Providers, method: str, path: str) -> list[httpx.Request]:
    return [r for r in providers.requests if r.method == method and r.url.path == path]


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: value for key, value in parse_qsl(request.content.decode())}


async def _billing_seed() -> tuple[UUID, UUID, UUID, UUID]:
    """A workspace with an admin, a teammate, the default agent, and the admin's own
    conversation."""
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
                    is_admin=member_id == owner_id,
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
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="ufo",
                queue_key="dm-owner",
                member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, owner_id, mate_id, conversation_id


def _billing_tool(audience: Audience) -> tuple[ToolDef, ExtensionContext]:
    declared, ext_by_tool = turn_tools(
        (metronome.manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=audience,
    )
    tool = next(t for t in declared if t.name == metronome.MANAGE_BILLING_TOOL)
    return tool, ext_by_tool[metronome.MANAGE_BILLING_TOOL]


async def _manage_billing(
    workspace_id: UUID,
    tmp_path: Path,
    speaker: UUID | None,
    disclosure_member_id: UUID | None,
    action: str,
    **extra: object,
) -> dict[str, object]:
    audience = conversation_audience(disclosure_member_id)
    tool, ext = _billing_tool(audience)
    ctx = _tool_context(workspace_id, ext, tmp_path, speaker, audience)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(tables.conversation.c.id, tables.conversation.c.agent_id)
                    .where(tables.conversation.c.workspace_id == workspace_id)
                    .order_by(tables.conversation.c.created_at, tables.conversation.c.id)
                    .limit(1)
                )
            ).one()
        ctx = replace(
            ctx,
            turn=ctx.turn.model_copy(
                update={
                    "conversation_id": conversation.id,
                    "agent_id": conversation.agent_id,
                }
            ),
        )
        result = await tool.handler(
            ctx,
            tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "action": action} | extra
            ),
        )
    return json.loads(result.content[0].text)


async def _stored_record(workspace_id: UUID) -> metronome.BillingRecord | None:
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        stored = await ctx.store.get(metronome.BILLING_KEY)
    return None if stored is None else metronome.BillingRecord.model_validate(stored)


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


async def test_billing_requires_a_speaking_admin(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A teammate and a speakerless turn are refused before any provider call."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with pytest.raises(ValueError, match="only a workspace admin"):
        await _manage_billing(workspace_id, tmp_path, mate_id, mate_id, "portal")
    with pytest.raises(ValueError, match="speaking member"):
        await _manage_billing(workspace_id, tmp_path, None, None, "portal")

    assert providers.requests == []
    assert await _stored_record(workspace_id) is None

    result = await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    assert result["portal_url"] == "https://billing.stripe.com/session/1"


async def test_status_reports_the_card_and_the_balance(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`status` answers what the workspace can spend and whether a card is on file — the card from
    Stripe rather than from our record, and the balance from core. `portal` resolves the customer
    itself, so no prior setup act is required to reach it."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    assert await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status") == {
        "payment_method_on_file": False,
        "balance_micro_usd": None,
        "reserve_micro_usd": None,
        "granted_micro_usd": None,
        "charged_micro_usd": None,
    }

    portal = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")
    session_calls = _calls(providers, "POST", "/v1/billing_portal/sessions")
    assert _form(session_calls[-1]) == {
        "customer": "cus_1",
        "configuration": PORTAL_CONFIGURATION,
    }
    assert portal["portal_url"] == f"https://billing.stripe.com/session/{providers.sessions}"
    assert portal["stripe_customer_id"] == "cus_1"

    providers.default_payment_method = SAVED_CARD
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 150_000_000, 100_000_000, "grant")
    funded = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")
    assert funded == {
        "payment_method_on_file": True,
        "balance_micro_usd": 150_000_000,
        "reserve_micro_usd": 0,
        "granted_micro_usd": 150_000_000,
        "charged_micro_usd": 100_000_000,
    }


async def test_a_failed_stripe_customer_create_records_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Stripe itself fails there is nothing to resume from, so the portal act must leave no
    record at all rather than a half-built one a later read would trust."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.failing.add("/v1/customers")
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with pytest.raises(metronome.StripeError, match="500"):
        await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")
    assert await _stored_record(workspace_id) is None
    assert _calls(providers, "POST", "/v1/billing_portal/sessions") == []

    providers.failing.clear()
    payload = await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")
    assert payload["stripe_customer_id"] == "cus_1"
    record = await _stored_record(workspace_id)
    assert record is not None
    assert record.stripe_customer_id == "cus_1"


def test_billing_config_names_every_missing_setting_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-configured deploy learns every missing name in one error, not one per attempt."""
    for name in (
        metronome.STRIPE_SECRET_KEY_ENV,
        metronome.STRIPE_PORTAL_CONFIGURATION_ENV,
        metronome.METRONOME_BEARER_TOKEN_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(
        RuntimeError,
        match=(
            "billing requires STRIPE_SECRET_KEY, STRIPE_BILLING_PORTAL_CONFIGURATION_ID, "
            "METRONOME_BEARER_TOKEN"
        ),
    ):
        metronome.BillingConfig.from_env()
    monkeypatch.setenv(metronome.STRIPE_SECRET_KEY_ENV, STRIPE_KEY)
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    with pytest.raises(
        RuntimeError,
        match="billing requires STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
    ):
        metronome.BillingConfig.from_env()


async def test_every_stripe_call_pins_the_api_version(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stripe-side default-version bump can never reshape a response under us."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate_id, _conversation_id = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "portal")
    await _manage_billing(workspace_id, tmp_path, owner_id, owner_id, "status")

    stripe_calls = [r for r in providers.requests if r.url.host == "api.stripe.com"]
    assert stripe_calls
    assert {r.headers["stripe-version"] for r in stripe_calls} == {metronome.STRIPE_API_VERSION}
    assert not any(
        "stripe-version" in r.headers for r in providers.requests if r not in stripe_calls
    )


async def test_the_shipper_creates_the_customer_that_resolves_its_ingest_alias(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every event is stamped with the workspace uuid, and Metronome resolves that through a
    customer's ingest alias. With no customer holding it the events are attributed to nobody and
    metering silently stops, so the shipper makes sure one exists before it posts — and a later run
    adopts the alias rather than creating a second holder."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()

    created = [r for r in recorder.requests if r.url.path == "/v1/customers" and r.method == "POST"]
    assert [json.loads(r.content)["ingest_aliases"] for r in created] == [[str(workspace_id)]]
    (event,) = _events(recorder.ingests()[0])
    assert event["customer_id"] == str(workspace_id)
    assert recorder.customers[str(workspace_id)]

    with ws(workspace_id):
        await _shipper(recorder).run()
    created_again = [
        r for r in recorder.requests if r.url.path == "/v1/customers" and r.method == "POST"
    ]
    assert len(created_again) == 1


async def test_an_existing_alias_is_adopted_rather_than_created_twice(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch every workspace set up under the old code takes: the alias already resolves, so
    nothing is created and the events ship against it."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    recorder = _Recorder()
    recorder.customers[str(workspace_id)] = "mc_existing"
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert [
        r for r in recorder.requests if r.method == "POST" and r.url.path == "/v1/customers"
    ] == []
    assert len(recorder.ingests()) == 1


async def test_a_conflicting_create_reconciles_to_the_alias_holder(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archived customer keeps the alias while the alias filter excludes it, so the create
    conflicts. Raising there would fail every tick forever with no in-product way out."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    recorder = _Recorder()
    recorder.hidden_once.add(str(workspace_id))
    recorder.customers[str(workspace_id)] = "mc_archived"
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert len(recorder.ingests()) == 1


async def test_usage_waits_when_the_token_cannot_confirm_the_customer(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that cannot read customers cannot confirm the alias, and ingest answers 2xx either
    way — so shipping under it and acking the exports destroys that usage instead of delaying it.
    The backlog waits for a token that can confirm rather than draining into nowhere."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    recorder = _Recorder()
    recorder.forbid_customers = True
    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _shipper(recorder).run()
    assert recorder.ingests() == []

    recorder.forbid_customers = False
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert len(recorder.ingests()) == 1


async def test_usage_waits_when_the_customer_cannot_be_confirmed(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingest answers 2xx whether or not the alias resolves, so shipping past an unconfirmed alias
    and acking the exports would destroy that usage rather than delay it. Every way of failing to
    confirm leaves the backlog for the next tick."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    recorder = _Recorder()
    recorder.failing.add("/v1/customers")
    with ws(workspace_id):
        with pytest.raises(metronome.MetronomeError):
            await _shipper(recorder).run()
    assert recorder.ingests() == []

    recorder.failing.clear()
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert len(recorder.ingests()) == 1


async def test_an_idle_workspace_spends_no_metronome_call(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This job ticks once a minute for every workspace. Reconciling the alias ahead of the batch
    read would spend a customer API call per workspace per minute on a fleet that is mostly idle,
    and the throttling that earns raises here — holding the usage of the workspaces that do have
    some. A pass with nothing to send touches Metronome not at all."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, _agent_id, _conversation_id = await _seed()
    recorder = _Recorder()
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert recorder.requests == []


async def test_an_alias_held_by_a_customer_this_token_cannot_read_holds_the_usage(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict means something already holds the alias. If a re-read finds it, the other shipper
    created it in the gap and this tick lost a harmless race. If the re-read still finds nothing,
    the holder is archived or otherwise invisible, and every event stamped with that alias is
    dropped — so the usage waits rather than shipping into nowhere."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
    alias = str(workspace_id)
    recorder = _Recorder()
    recorder.customers[alias] = "mc_archived"
    recorder.hidden_always.add(alias)
    with ws(workspace_id), pytest.raises(metronome.MetronomeError):
        await _shipper(recorder).run()
    assert recorder.ingests() == []

    recorder.hidden_always.discard(alias)
    with ws(workspace_id):
        await _shipper(recorder).run()
    assert len(recorder.ingests()) == 1


async def test_usage_held_past_the_backdating_window_is_reported(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding a batch delays the usage rather than destroying it, but the provider backdates only
    BACKFILL_WINDOW_DAYS — held longer, the usage becomes unbillable and the hold turns into the
    loss it was meant to prevent. Nothing recovers it; it must at least not be silent."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(
            connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1000, output_tokens=500)
        )
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.turn_id == turn_id)
            .values(
                updated_at=datetime.now(UTC) - timedelta(days=metronome.BACKFILL_WINDOW_DAYS + 3)
            )
        )
    recorder = _Recorder()
    warned: list[str] = []
    monkeypatch.setattr(metronome, "warn", lambda event, **fields: warned.append(event))
    shipper = _shipper(recorder)
    with ws(workspace_id):
        await shipper.ctx.store.put(
            metronome.FLOOR_KEY,
            (datetime.now(UTC) - timedelta(days=metronome.BACKFILL_WINDOW_DAYS + 30)).isoformat(),
        )
        await shipper.run()
    assert "metronome.usage_past_backdating_window" in warned


async def _balance_of(workspace_id: UUID) -> int:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            current = await read_balance(connection, workspace_id)
    assert current is not None
    return current.balance_micro_usd


async def _run_topup(workspace_id: UUID, providers: "_Providers") -> None:
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        await metronome.BalanceTopup(ctx=ctx, transport=providers.transport).run()


async def test_autopay_refills_the_balance_from_the_card_on_file(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of arranging a refill is that it happens with nobody present, so the charge
    is off-session against the card already saved. Core decides the workspace is short; this
    pays."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    arranged = await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=50,
        autopay_below_dollars=10,
    )
    assert arranged["autopay_micro_usd"] == 50 * DOLLAR

    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 55 * DOLLAR
    (charge,) = providers.intents
    assert charge["amount"] == "5000"
    assert charge["off_session"] == "true"
    assert charge["customer"].startswith("cus_")


async def test_autopay_leaves_a_balance_above_its_line_alone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trigger is core's, and a workspace still above its line is not short. A tick that
    charged anyway would bill a card on a schedule rather than on need."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 80 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=50,
        autopay_below_dollars=10,
    )

    await _run_topup(workspace_id, providers)
    assert providers.intents == []
    assert await _balance_of(workspace_id) == 80 * DOLLAR


async def test_a_second_tick_after_a_refill_charges_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job ticks every few minutes. Once a refill lands the workspace is no longer short, so
    the next tick asks core, is told nothing is needed, and never reaches the card — the schedule
    bills on need, not on its own cadence.

    A redelivery that repeats a charge is a different guard: the intent carries an idempotency key
    so Stripe collapses it, and the credit is keyed on the intent so the balance records it once."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=50,
        autopay_below_dollars=10,
    )

    await _run_topup(workspace_id, providers)
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 55 * DOLLAR
    assert len(providers.charges) == 1


async def test_a_declined_card_leaves_the_balance_alone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decline is the issuer's answer, not our fault. Crediting anyway would hand out money the
    workspace never paid, so the balance stays where it was and the workspace stays refused."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=50,
        autopay_below_dollars=10,
    )
    providers.decline = True

    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 5 * DOLLAR


async def test_autopay_refuses_to_promise_a_refill_without_a_card(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refill runs with nobody present, so the card has to be there when it is arranged rather
    than at the moment it is needed."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    with pytest.raises(ValueError, match="save a payment method"):
        await _manage_billing(
            workspace_id,
            tmp_path,
            owner_id,
            None,
            "autopay",
            autopay_dollars=50,
            autopay_below_dollars=10,
        )


async def test_a_second_refill_the_workspace_needs_is_not_replayed(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe holds an idempotency key for a day. Keyed on the refill amount, the second refill a
    workspace genuinely needed would replay the first intent, credit nothing against a reference
    already spent, and leave it unable to refill again until the key aged out. The key carries what
    the workspace has been charged to date, which moves with each settled refill."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )

    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 25 * DOLLAR

    # spend it back under the line, so a second refill is genuinely due
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await debit(connection, workspace_id, 20 * DOLLAR)
    await _run_topup(workspace_id, providers)

    assert await _balance_of(workspace_id) == 25 * DOLLAR
    assert len(providers.charges) == 2


async def test_autopay_recovers_once_a_refused_card_is_fixed(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe holds a key for a day, so a retry after a decline must not replay it. Arranging the
    refill again is what restarts a stood-down card, and it advances the key's counter, so the next
    charge is a new one rather than a replay of the refusal."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )

    providers.decline = True
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 5 * DOLLAR

    providers.decline = False
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 25 * DOLLAR


async def test_a_card_that_keeps_refusing_is_left_alone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick is every few minutes, so retrying an issuer's refusal on that schedule is an
    unbounded run of authorizations against a card that already said no — it earns nothing and is
    what card networks penalise. The job waits a day, and arranging autopay again releases it
    sooner, which is also the act that follows fixing the card."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )

    providers.decline = True
    for _ in range(6):
        await _run_topup(workspace_id, providers)
    assert len(providers.intents) == 1

    # the admin fixes the card and arranges the refill again
    providers.decline = False
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 25 * DOLLAR


async def _backdate_refusal(workspace_id: UUID, days: float) -> None:
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        stamped = await ctx.store.get(metronome.TOPUP_REFUSED_AT_KEY)
        assert isinstance(stamped, str)
        await ctx.store.put(
            metronome.TOPUP_REFUSED_AT_KEY,
            (datetime.fromisoformat(stamped) - timedelta(days=days)).isoformat(),
        )


async def test_a_refused_refill_asks_again_once_the_wait_is_up(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace short enough to need a refill is one the balance gate is about to refuse every
    turn of, including the turn that would arrange autopay again. A stand-down that only a member
    act could clear would strand the workspace with no way back, so the wait lapses on its own and
    the next attempt carries a key Stripe has not already answered."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )

    providers.decline = True
    await _run_topup(workspace_id, providers)
    await _run_topup(workspace_id, providers)
    assert len(providers.intents) == 1

    await _backdate_refusal(workspace_id, days=1)
    providers.decline = False
    await _run_topup(workspace_id, providers)

    assert await _balance_of(workspace_id) == 25 * DOLLAR
    keys = [r.headers["idempotency-key"] for r in _calls(providers, "POST", "/v1/payment_intents")]
    assert len(keys) == len(set(keys)) == 2


def test_the_billing_tool_names_every_action_it_accepts() -> None:
    """The description is what the model reads before choosing the tool, and the prompt section is
    what it reads on every turn. An action missing from either is an action the agent never calls,
    however well the code behind it works — the admin is told the thing cannot be done."""
    (tool,) = metronome.manifest().tools
    for action in ("status", "portal", "autopay"):
        assert action in tool.description, action
        assert action in metronome.BILLING_SECTION_BODY, action


async def _grace(workspace_id: UUID) -> int:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            headroom = await read_headroom(connection, workspace_id)
    assert headroom is not None
    return headroom.grace_micro_usd


async def test_a_settled_charge_earns_the_workspace_its_grace(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overdraft core allows is earned by paying, and this is the act that proves it. A card on
    file proves nothing — an issuer decides at the charge — so the flag is set from the one place
    that has watched money move."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )
    assert await _grace(workspace_id) == 0

    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 25 * DOLLAR
    assert await _grace(workspace_id) == TOPUP_GRACE_MICRO_USD


async def test_a_charge_still_in_flight_does_not_stand_the_refill_down(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick is shorter than an authorization can take, so a refill still being decided is asked
    again under the same key and told so. Counting that as a refusal would park the refill for a
    day over a card in the middle of paying, and the balance it was about to fund would run out."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = "pm_1"
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    await _manage_billing(
        workspace_id,
        tmp_path,
        owner_id,
        None,
        "autopay",
        autopay_dollars=20,
        autopay_below_dollars=10,
    )

    providers.in_flight = True
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 5 * DOLLAR
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        assert await ctx.store.get(metronome.TOPUP_REFUSED_AT_KEY) is None

    providers.in_flight = False
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 25 * DOLLAR


def _billing_request(workspace_id: UUID, email: str | None) -> Request:
    headers = []
    if email is not None:
        token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
        headers.append((b"cookie", f"{metronome.SESSION_COOKIE}={token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/ext/metronome/billing",
            "headers": headers,
            "query_string": b"",
        }
    )


async def _read_billing(workspace_id: UUID, email: str | None) -> tuple[int, dict[str, object]]:
    ctx = context_for(metronome.NAME, frozenset())
    request = _billing_request(workspace_id, email)
    with ws(workspace_id):
        answer = await metronome._billing_projection(ctx, request)
    return answer.status_code, json.loads(bytes(answer.body))


async def test_the_billing_page_refuses_a_request_with_no_session(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core mounts an extension route with no auth in front of it, so the handler is the only thing
    between this page and the open internet."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    status, _ = await _read_billing(workspace_id, None)
    assert status == 401


async def test_the_billing_page_refuses_a_member_who_is_not_an_admin(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace holds people from outside the company. The page names what the company has spent
    and whether its card is on file, so a seat is not enough to read it."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    status, body = await _read_billing(workspace_id, "mate@example.com")
    assert status == 403
    assert "admin" in str(body["error"])


async def test_an_admin_elsewhere_is_not_an_admin_here(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session proves an address, and an address is a member somewhere, not everywhere. The same
    person is an admin of one workspace and an ordinary seat in another, so the address must be
    resolved to a member of *this* workspace before the admin flag on that row means anything. An
    unscoped lookup would find the other row and read this workspace's billing to a guest."""
    _billing_env(monkeypatch)
    workspace_id, _owner, mate_id, _conv = await _billing_seed()
    elsewhere_id, _o2, elsewhere_mate, _c2 = await _billing_seed()
    with ws(elsewhere_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == elsewhere_mate)
                .values(is_admin=True)
            )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            here = (
                await connection.execute(
                    sa.select(tables.member.c.is_admin).where(tables.member.c.id == mate_id)
                )
            ).scalar_one()
    assert here is False
    status, _ = await _read_billing(workspace_id, "mate@example.com")
    assert status == 403


async def test_the_billing_page_answers_an_admin_what_stops_the_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number an admin needs is the line turns are refused at, not the balance: a workspace
    whose card has paid keeps working below zero, and one that never paid stops at its reserve."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 40 * DOLLAR, 0, "opening")
            await set_reserve(connection, workspace_id, 5 * DOLLAR)
    status, body = await _read_billing(workspace_id, "owner@example.com")
    assert status == 200
    assert body["balance_micro_usd"] == 40 * DOLLAR
    assert body["refused_below_micro_usd"] == 5 * DOLLAR
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await mark_topup_verified(connection, workspace_id)
    _status, paid = await _read_billing(workspace_id, "owner@example.com")
    assert paid["grace_micro_usd"] == TOPUP_GRACE_MICRO_USD
    assert paid["refused_below_micro_usd"] == 5 * DOLLAR - TOPUP_GRACE_MICRO_USD
