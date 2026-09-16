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

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.harness.models.registry import ModelRegistry, model_registry
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import turn_tools
from ufo.host.kinds.workspace_kind import WORKSPACE_KIND
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.billing.accounting import (
    record_egress_request,
    record_sandbox_tokens,
    record_turn_usage,
    record_workspace_usage,
)
from ufo.runtime.billing.balance import (
    BILLING_SCREEN_FRAGMENT,
    TOPUP_GRACE_MICRO_USD,
    credit,
    debit,
    mark_topup_verified,
    read_balance,
    read_headroom,
    set_auto_topup,
    set_reserve,
)
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.manifest import JobFault
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.objects import AdminRequired
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.tools.context import SpawnResult, SpeakerRequired, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import (
    Agent,
    FiredBy,
    ModelAccountCapability,
    TerminalFrame,
    Turn,
    TurnContext,
    TurnRuntimeConfig,
    Usage,
)
from ufo.sdk.audience import Audience, conversation_audience
from ufo.sdk.http import Request

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

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
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
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
    page_route, card_route = declared.routes
    assert (page_route.method, page_route.path) == ("GET", metronome.BILLING_ROUTE_PATH)
    assert page_route.handler is metronome._billing_projection
    assert (card_route.method, card_route.path) == ("GET", metronome.CARD_ROUTE_PATH)
    assert card_route.handler is metronome._billing_card


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


async def test_a_refused_call_names_itself_and_carries_no_provider_body(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack says the shipper raised at a customer read and never what the read answered, so an
    outage, a revoked token, and a bug all reach `jobs.failed` as one record. The reason this
    raises with is the record's `fault`, so it names the call, its status, and the `message`
    Metronome put in its error body — and nothing else of that body, which echoes the request that
    drew it."""
    monkeypatch.setenv(metronome.METRONOME_BEARER_TOKEN_ENV, TOKEN)
    workspace_id, agent_id, conversation_id = await _seed()
    turn_id = await _turn(workspace_id, conversation_id, agent_id)
    await _settle(turn_id, age_seconds=0)
    async with workspace_tx() as connection:
        await record_turn_usage(connection, workspace_id, turn_id, MODEL, Usage(input_tokens=10))
    refusing = _Recorder()
    refusing.failing.add("/v1/customers")

    with ws(workspace_id), pytest.raises(metronome.MetronomeError) as raised:
        await _shipper(refusing).run()

    assert isinstance(raised.value, JobFault)
    assert raised.value.reason == "metronome customer lookup failed (500): provider is down"
    assert refusing.ingests() == []
    assert await _acked() == set()


async def test_a_refusal_that_names_nothing_still_names_the_call(db: None) -> None:
    """A body the provider did not shape the documented way leaves the call and its status, which
    is what separates an outage from a refused token. The empty rendering carries no colon, so a
    reason never trails one."""
    assert metronome._metronome_fault(
        "ingest", httpx.Response(503, text="<html>gateway</html>")
    ) == ("metronome ingest failed (503)")


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
        context: TurnContext | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
        model_accounts: tuple[ModelAccountCapability, ...] = (),
        fired_by: FiredBy | None = None,
        acting_member_id: UUID | None = None,
    ) -> UUID | None:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key,
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
HOME_SURFACE = "portal"


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
        self.card_brand = "visa"
        self.card_last4 = "4242"
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
        if path.startswith("/v1/payment_methods/") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[-1],
                    "card": {"brand": self.card_brand, "last4": self.card_last4},
                },
            )
        if path == "/v1/payment_intents" and request.method == "POST":
            if self.in_flight:
                return httpx.Response(
                    409,
                    json={
                        "error": {"code": "idempotency_in_progress", "type": "idempotency_error"}
                    },
                )
            form = _form(request)
            self.intents.append(form)
            if form.get("confirm") == "true" and not form.get("payment_method"):
                return httpx.Response(
                    400,
                    json={"error": {"message": "no payment method provided", "type": "card_error"}},
                )
            key = request.headers.get("idempotency-key", f"none-{len(self.replayed)}")
            if key in self.replayed:
                status, answer = self.replayed[key]
                return httpx.Response(status, json=answer)
            if self.decline:
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


def _billing_tool(
    audience: Audience,
    public_base_url: str | None = None,
    home_surface: str | None = HOME_SURFACE,
) -> tuple[ToolDef, ExtensionContext]:
    _declared, _ext_by_tool, verbs = turn_tools(
        (metronome.manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=audience,
        public_base_url=public_base_url,
        home_surface=home_surface,
    )
    bound = verbs.actions[WORKSPACE_KIND][metronome.MANAGE_BILLING_TOOL]
    assert bound.context is not None
    return bound.action, bound.context


async def _manage_billing(
    workspace_id: UUID,
    tmp_path: Path,
    speaker: UUID | None,
    disclosure_member_id: UUID | None,
    action: str,
    public_base_url: str | None = None,
    home_surface: str | None = HOME_SURFACE,
    **extra: object,
) -> dict[str, object]:
    audience = conversation_audience(disclosure_member_id)
    tool, ext = _billing_tool(audience, public_base_url, home_surface)
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
            tool.input_model.model_validate({"operation": action} | extra),
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

    with pytest.raises(AdminRequired):
        await _manage_billing(workspace_id, tmp_path, mate_id, mate_id, "portal")
    with pytest.raises(SpeakerRequired, match="requested_by"):
        await _manage_billing(workspace_id, tmp_path, None, None, "portal")

    assert providers.requests == []
    assert await _stored_record(workspace_id) is None

    result = await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    assert result["portal_url"] == "https://billing.stripe.com/session/1"


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
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(
        RuntimeError,
        match="billing requires STRIPE_SECRET_KEY, STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
    ):
        metronome.BillingConfig.from_env()
    monkeypatch.setenv(metronome.STRIPE_SECRET_KEY_ENV, STRIPE_KEY)
    with pytest.raises(
        RuntimeError,
        match="billing requires STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
    ):
        metronome.BillingConfig.from_env()


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


async def test_the_refill_job_fans_out_only_to_a_workspace_that_reached_its_line(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refill fires every minute for the whole fleet, and one execution per workspace opens a
    workspace transaction before it can learn there is nothing to refill. Naming every workspace
    that holds a member spends that fan-out on the fleet, where the workspaces owing a charge are a
    few: the candidate read carries core's trigger, so a workspace with no refill arranged, and one
    still above its line, is never fired at."""
    _billing_env(monkeypatch)
    due, _owner, _mate, _conversation = await _billing_seed()
    above, _above_owner, _above_mate, _above_conversation = await _billing_seed()
    unarranged, _idle_owner, _idle_mate, _idle_conversation = await _billing_seed()
    for workspace_id, threshold in ((due, 10), (above, 1), (unarranged, None)):
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await credit(connection, workspace_id, 5 * DOLLAR, 0, "opening")
                if threshold is not None:
                    await set_auto_topup(connection, workspace_id, 50 * DOLLAR, threshold * DOLLAR)
    declared = metronome.manifest()
    runner = JobRunner(
        bindings=bindings_from((declared,), ()), manifests=(declared,), registry=_registry()
    )

    named = await runner.candidates(f"{metronome.NAME}:{metronome.TOPUP_JOB_NAME}")

    assert named == (due,)


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


def _billing_request(workspace_id: UUID, email: str | None, path: str) -> Request:
    headers = []
    if email is not None:
        token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
        headers.append((b"cookie", f"{metronome.SESSION_COOKIE}={token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers,
            "query_string": b"",
        }
    )


async def _read_billing(workspace_id: UUID, email: str | None) -> tuple[int, dict[str, object]]:
    ctx = context_for(metronome.NAME, frozenset())
    request = _billing_request(workspace_id, email, "/ext/metronome/billing")
    with ws(workspace_id):
        answer = await metronome._billing_projection(ctx, request)
    return answer.status_code, json.loads(bytes(answer.body))


async def _read_card(workspace_id: UUID, email: str | None) -> tuple[int, dict[str, object]]:
    ctx = context_for(metronome.NAME, frozenset())
    request = _billing_request(workspace_id, email, "/ext/metronome/billing/card")
    with ws(workspace_id):
        answer = await metronome._billing_card(ctx, request)
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


async def test_the_billing_page_never_waits_on_the_payment_provider(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The balance is ours and the card is Stripe's, so the page states what this deploy already
    holds: it asks the provider for nothing and mints no customer for a workspace that has never
    opened the portal."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 40 * DOLLAR, 0, "opening")

    status, body = await _read_billing(workspace_id, "owner@example.com")

    assert status == 200
    assert body["balance_micro_usd"] == 40 * DOLLAR
    assert "card" not in body
    assert providers.requests == []
    assert await _stored_record(workspace_id) is None


async def test_the_card_read_refuses_whoever_the_page_refuses(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card is on its own route now, and a route core mounts has no auth in front of it: a
    teammate and a sessionless request are refused here exactly as they are on the page."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    assert (await _read_card(workspace_id, None))[0] == 401
    assert (await _read_card(workspace_id, "mate@example.com"))[0] == 403
    assert providers.requests == []


async def test_a_workspace_with_no_customer_has_no_card_to_ask_about(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Stripe Customer is minted when an admin opens the portal, which is the act that saves a
    card. Until then there is nothing for the provider to answer, so nothing is asked."""
    _billing_env(monkeypatch)
    workspace_id, _owner, _mate, _conv = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    status, body = await _read_card(workspace_id, "owner@example.com")

    assert (status, body) == (200, {"card": None, "card_unread": False})
    assert providers.requests == []


async def test_the_card_read_names_what_the_provider_holds(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin needs to know which card a refill charges, so the read states the brand and the
    last four the provider shows them."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")

    status, body = await _read_card(workspace_id, "owner@example.com")

    assert status == 200
    assert body == {"card": {"brand": "visa", "last4": "4242"}, "card_unread": False}


async def test_a_provider_that_will_not_answer_leaves_the_card_unknown(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The balance is already on the screen, so a provider that will not answer costs the card and
    nothing else. It reads as unknown rather than as absent, because "no card" invites saving one
    and would be a guess."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    providers.default_payment_method = SAVED_CARD
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)
    await _manage_billing(workspace_id, tmp_path, owner_id, None, "portal")
    providers.failing.add("/v1/customers/cus_1")

    status, body = await _read_card(workspace_id, "owner@example.com")

    assert (status, body) == (200, {"card": None, "card_unread": True})


async def test_the_card_link_sends_the_member_back_to_their_billing_screen(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe leaves a member wherever the session says, and a session with nowhere to return to
    strands them at the provider holding a card this deploy has not seen. The return lands on the
    one screen that reads the card from the provider and answers while the balance refuses turns,
    so the card becomes known at the moment it is saved rather than whenever something next looks.

    The screen sits on the surface the deploy's own manifest marks as the browser home, which core
    resolves and hands the context — a name this deploy does not call "web"."""
    _billing_env(monkeypatch)
    workspace_id, owner_id, _mate, _conv = await _billing_seed()
    providers = _Providers()
    monkeypatch.setattr(metronome, "BILLING_TRANSPORT", providers.transport)

    await _manage_billing(
        workspace_id, tmp_path, owner_id, None, "portal", public_base_url="https://ufo.test/"
    )

    (session,) = _calls(providers, "POST", "/v1/billing_portal/sessions")
    assert _form(session)["return_url"] == (
        f"https://ufo.test/surface/{HOME_SURFACE}{BILLING_SCREEN_FRAGMENT}"
    )


def _customer_reads(providers: _Providers) -> int:
    return len(
        [r for r in providers.requests if r.method == "GET" and "/v1/customers/" in r.url.path]
    )


async def _cardless_mark(workspace_id: UUID) -> object:
    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        return await ctx.store.get(metronome.TOPUP_CARDLESS_KEY)


async def test_an_armed_workspace_whose_card_is_gone_is_not_read_every_minute(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refill is arranged against a card, and nothing holds the card there — an admin can remove
    it. The tick is every minute, so an armed cardless workspace read the provider 1440 times a day
    for work it cannot do, and could not be told to stop: the balance that made it short is the one
    refusing the turn that would clear the rule."""
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
    providers.default_payment_method = None
    before = _customer_reads(providers)

    for _ in range(6):
        await _run_topup(workspace_id, providers)

    assert _customer_reads(providers) - before == 1
    assert await _cardless_mark(workspace_id) is not None


async def test_a_card_saved_again_is_served_without_the_member_asking(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait is minutes, not the day a decline holds, because finding no card stops being true
    the moment one is saved. Nothing a member does releases it — the workspace it applies to is the
    one whose turns are refused — so the lapse has to be short enough to serve them on its own."""
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
    providers.default_payment_method = None
    await _run_topup(workspace_id, providers)
    assert await _balance_of(workspace_id) == 5 * DOLLAR

    ctx = context_for(metronome.NAME, frozenset())
    with ws(workspace_id):
        stamped = await ctx.store.get(metronome.TOPUP_CARDLESS_KEY)
        assert isinstance(stamped, str)
        await ctx.store.put(
            metronome.TOPUP_CARDLESS_KEY,
            (datetime.fromisoformat(stamped) - metronome.TOPUP_CARDLESS_RETRY_AFTER).isoformat(),
        )
    providers.default_payment_method = "pm_1"
    await _run_topup(workspace_id, providers)

    assert await _balance_of(workspace_id) == 25 * DOLLAR
    assert await _cardless_mark(workspace_id) is None
