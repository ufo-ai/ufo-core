import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)
from ufo_ext_sample.spend import CHARGE_TABLE, SampleGate, allow

from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.catalog import ANTHROPIC_KEY_ENV, CORE_PRICING, OPENAI_KEY_ENV
from ufo.harness.models.interface import (
    PROVIDER_ANTHROPIC,
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolUseBlock,
)
from ufo.harness.models.pricing import ModelPrice, Pricing
from ufo.harness.o11y import BACKGROUND_PROFILE
from ufo.harness.sandbox import terminal
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.exec_env import CONVERSATION_ID_ENV, ProbeEnv
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    PROXY_SESSION_ENV_NAMES,
    ProbeTokenCodec,
    SandboxHandle,
    SandboxSpec,
)
from ufo.harness.sandbox.terminal import TerminalGone
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.vault import SecretValue, VaultReads
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.billing.accounting import (
    EGRESS_DIMENSION,
    UNGATED_LEDGER,
    Ledger,
    OffTurnSpendRefused,
    ServiceTotal,
    SpendRollup,
    SpendTotals,
)
from ufo.runtime.billing.spend import NO_SPEND_GATES, PARK, GateDeploy, SpendDecision, SpendGates
from ufo.runtime.ext.context import (
    CORE_EXTENSION,
    PROBE_TIMEOUT_MAX_SECONDS,
    ConversationFacts,
    ConversationFiles,
    ConversationProbes,
    CredentialAccess,
    ScopedStore,
    TurnOutcome,
    UndeclaredCredentialSlot,
    context_for,
    spend_refusal_notice_key,
)
from ufo.runtime.ext.manifest import CredentialSlot, InjectionTarget
from ufo.runtime.ext.surface import (
    AddressClaimState,
    UndeclaredSurface,
)
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import (
    KEY_FUNDED,
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    Funding,
    ResolvedModelClient,
    init_workspace_credentials,
    ws,
)
from ufo.schema import tables
from ufo.schema.records import SUBAGENT_SURFACE, Usage

MODEL = "claude-opus-4-8"
JOB = "memory:memory_consolidate"


async def _workspace() -> UUID:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


@dataclass(frozen=True)
class ReasoningModel:
    with_tool: bool

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield RedactedThinkingBlock(data="ZW5jcnlwdGVk")
        yield ThinkingBlock(thinking="", signature="sig-1")
        yield ReasoningItemBlock(id="rs_1", encrypted_content="Z3B0LWVuY3J5cHRlZA")
        yield TextDelta(text="checking")
        if self.with_tool:
            yield ToolCallStart(id="c1", name="bash")
            yield ToolCallDelta(id="c1", partial_json='{"command": "ls"}')
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass
class RecordingModel:
    """Records the request the seam actually sent, so a test reads what the seam fixed onto it
    rather than what the caller wrote."""

    sent: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.sent.append(request)
        yield TextDelta(text="checking")
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class FailingModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="half an answer")
        raise TimeoutError("provider went away")


@dataclass(frozen=True)
class InvalidToolModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json="{")
        yield Usage(input_tokens=5, output_tokens=2)


@dataclass(frozen=True)
class CachingModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="summarized")
        yield Usage(
            input_tokens=11,
            output_tokens=3,
            cache_read_tokens=40,
            cache_write_5m_tokens=5,
            cache_write_1h_tokens=2,
        )


SAMPLE_SPEND = SpendGates(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))


@dataclass(frozen=True)
class StubResolver:
    """The model registry as `ModelAccess` reads it, wired to one scripted client."""

    client: ModelClient
    funding: Funding = PLATFORM_FUNDED
    payer: str = PLATFORM_PAYER

    @property
    def auto_model(self) -> str:
        return MODEL

    @property
    def pricing(self) -> Pricing:
        return CORE_PRICING

    async def client_for(self, model: str) -> ResolvedModelClient:
        return ResolvedModelClient(self.client, self.funding, self.payer)

    key_slot: str | None = None

    def key_slot_for(self, model: str) -> str | None:
        return self.key_slot

    def provider_for(self, model: str) -> str:
        return PROVIDER_ANTHROPIC


def _metric_capture(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    monkeypatch.setattr(o11y, "_up_down_counters", {})
    return reader


def _exported_metrics(
    reader: InMemoryMetricReader,
) -> dict[str, Sequence[HistogramDataPoint | NumberDataPoint]]:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        metric.name: metric.data.data_points
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


async def _turn(model: ModelClient, job: str = JOB) -> Message:
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(model),
        model_job=job,
        spend=NO_SPEND_GATES,
        ledger=UNGATED_LEDGER,
    )
    assert context.model is not None
    with ws(await _workspace()):
        return await context.model.turn(
            ModelRequest(
                model="auto",
                system="be terse",
                messages=(Message(role="user", content="hi"),),
                max_tokens=64,
                conversation_cache_ttl="5m",
            )
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_model_turn_opens_a_tool_calling_message_with_its_reasoning_blocks(
    db: None,
) -> None:
    """The seam re-sends this message when a handler feeds the tool result back, so it carries the
    round's reasoning ahead of the tool calls the signature authenticates."""
    assert (await _turn(ReasoningModel(with_tool=True))).content == (
        RedactedThinkingBlock(data="ZW5jcnlwdGVk"),
        ThinkingBlock(thinking="", signature="sig-1"),
        ReasoningItemBlock(id="rs_1", encrypted_content="Z3B0LWVuY3J5cHRlZA"),
        TextBlock(text="checking"),
        ToolUseBlock(id="c1", name="bash", input={"command": "ls"}),
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_a_gate_holds_cannot_run_a_background_model_call(db: None) -> None:
    workspace_id = await _workspace()
    model = RecordingModel()
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(model),
        model_job=JOB,
        spend=SAMPLE_SPEND,
        ledger=UNGATED_LEDGER,
    )
    assert context.model is not None
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await allow(connection, workspace_id, 0, "reject")
        with pytest.raises(OffTurnSpendRefused, match="allowance is spent at off_turn") as refused:
            await context.model.complete(
                ModelRequest(
                    model="auto",
                    system="be terse",
                    messages=(Message(role="user", content="hi"),),
                    max_tokens=64,
                    conversation_cache_ttl="5m",
                )
            )
    assert (refused.value.outcome, refused.value.model) == ("reject", MODEL)
    assert model.sent == []


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_allowed_background_call_forgets_the_spend_refusal_mark(db: None) -> None:
    workspace_id = await _workspace()
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(RecordingModel()),
        model_job=JOB,
        spend=SAMPLE_SPEND,
        ledger=UNGATED_LEDGER,
    )
    assert context.model is not None
    request = ModelRequest(
        model="auto",
        system="be terse",
        messages=(Message(role="user", content="hi"),),
        max_tokens=64,
        conversation_cache_ttl="5m",
    )
    store = ScopedStore(extension=CORE_EXTENSION)
    mark = spend_refusal_notice_key(MODEL)
    held = spend_refusal_notice_key("gpt-5.6-luna")
    with ws(workspace_id):
        await store.put(mark, PARK)
        await store.put(held, PARK)
        async with workspace_tx() as connection:
            await allow(connection, workspace_id, 0, "park")
        with pytest.raises(OffTurnSpendRefused, match="allowance is spent"):
            await context.model.complete(request)
        assert await store.get(mark) == PARK

        async with workspace_tx() as connection:
            await allow(connection, workspace_id, 1_000_000, "park")
        await context.model.complete(request)
        assert await store.get(mark) is None
        assert await store.get(held) == PARK


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_cap_blocks_a_background_model_call(db: None) -> None:
    workspace_id = await _workspace()
    model = RecordingModel()
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(model),
        model_job=JOB,
        spend=NO_SPEND_GATES,
        ledger=UNGATED_LEDGER,
    )
    assert context.model is not None
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await UNGATED_LEDGER.record_workspace_usage(
                connection, workspace_id, MODEL, Usage(input_tokens=1_000)
            )
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope="workspace",
                    subject_id=None,
                    window_seconds=3_600,
                    limit_micro_usd=1,
                    on_breach="reject",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with pytest.raises(RuntimeError, match="workspace spend cap"):
            await context.model.complete(
                ModelRequest(
                    model="auto",
                    system="be terse",
                    messages=(Message(role="user", content="hi"),),
                    max_tokens=64,
                    conversation_cache_ttl="5m",
                )
            )
    assert model.sent == []


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_background_call_meters_its_tokens_and_latency_under_its_job(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = _metric_capture(monkeypatch)
    assert (await _turn(CachingModel())).content == "summarized"
    points = _exported_metrics(reader)
    assert {
        (point.attributes["kind"], point.value) for point in points["ufo.model_round_tokens_total"]
    } == {("input", 11), ("output", 3), ("cache_read", 40), ("cache_write", 7)}
    assert {
        (
            point.attributes["model"],
            point.attributes["provider"],
            point.attributes["profile"],
            point.attributes["job"],
        )
        for point in points["ufo.model_round_tokens_total"]
    } == {(MODEL, PROVIDER_ANTHROPIC, BACKGROUND_PROFILE, JOB)}
    assert [(point.count, dict(point.attributes)) for point in points["ufo.model_round_ms"]] == [
        (
            1,
            {
                "model": MODEL,
                "provider": PROVIDER_ANTHROPIC,
                "profile": BACKGROUND_PROFILE,
                "job": JOB,
            },
        )
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_failed_background_call_meters_its_latency_with_the_error_class(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that raises books no spend, so the ledger keeps no trace of it at all."""
    reader = _metric_capture(monkeypatch)
    with pytest.raises(TimeoutError, match="provider went away"):
        await _turn(FailingModel())
    points = _exported_metrics(reader)
    assert [dict(point.attributes) for point in points["ufo.model_round_ms"]] == [
        {
            "model": MODEL,
            "provider": PROVIDER_ANTHROPIC,
            "profile": BACKGROUND_PROFILE,
            "job": JOB,
            "error_class": "TimeoutError",
        }
    ]
    assert "ufo.model_round_tokens_total" not in points


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_invalid_background_output_keeps_reported_provider_usage(db: None) -> None:
    workspace_id = await _workspace()
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(InvalidToolModel()),
        model_job=JOB,
        spend=NO_SPEND_GATES,
        ledger=UNGATED_LEDGER,
    )
    assert context.model is not None
    with ws(workspace_id), pytest.raises(ValueError):
        await context.model.turn(
            ModelRequest(
                model="auto",
                system="be terse",
                messages=(Message(role="user", content="hi"),),
                max_tokens=64,
                conversation_cache_ttl="5m",
            )
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.amount, tables.ledger.c.input_tokens).where(
                    tables.ledger.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert tuple(row) == (7, 5)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_stream_with_no_usage_meters_the_failure_it_raises(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unpriceable stream is this seam's own refusal rather than the provider's, and it is
    metered the same way — a job whose model answers without usage is invisible in the ledger."""
    reader = _metric_capture(monkeypatch)

    @dataclass(frozen=True)
    class NoUsage:
        async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            yield TextDelta(text="unpriceable")

    with pytest.raises(RuntimeError, match="produced no usage"):
        await _turn(NoUsage())
    points = _exported_metrics(reader)
    assert [point.attributes["error_class"] for point in points["ufo.model_round_ms"]] == [
        "RuntimeError"
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_cancelled_background_call_meters_the_cancellation(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job dropped mid-call at shutdown is cancelled, not failed, and a cancellation that went
    unlabelled would read back as a round that answered in no tokens at all."""
    reader = _metric_capture(monkeypatch)

    @dataclass(frozen=True)
    class Cancelled:
        async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            yield TextDelta(text="half")
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _turn(Cancelled())
    points = _exported_metrics(reader)
    assert [point.attributes["error_class"] for point in points["ufo.model_round_ms"]] == [
        "CancelledError"
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_scoped_store_round_trips_json_values(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("obj", {"a": 1, "nested": [True, None]})
        await store.put("scalar", "hello")
        assert await store.get("obj") == {"a": 1, "nested": [True, None]}
        assert await store.get("scalar") == "hello"
        assert await store.get("absent") is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_scoped_store_put_survives_a_concurrent_first_write(db: None) -> None:
    """Two callers writing one key that is not there yet. Each finds nothing to update, so a probe
    followed by a separate insert leaves both to insert and one to fail on the primary key."""
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        writers = [store.put("contested", f"writer-{n}") for n in range(8)]
        await asyncio.gather(*writers)
        assert await store.get("contested") in {f"writer-{n}" for n in range(8)}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_get_many_returns_exactly_the_named_keys_within_its_scopes(db: None) -> None:
    """One query for exactly `keys`: absent keys are omitted, another extension's and another
    workspace's rows under the same keys stay invisible, and an empty key set answers empty."""
    first, second = await _workspace(), await _workspace()
    with ws(first):
        sample = ScopedStore(extension="sample")
        zeta = ScopedStore(extension="zeta")
        await sample.put("chat/a", {"title": "one"})
        await sample.put("chat/b", {"title": "two"})
        await sample.put("chat/c", {"title": "three"})
        await zeta.put("chat/a", {"title": "foreign extension"})
    with ws(second):
        await ScopedStore(extension="sample").put("chat/a", {"title": "foreign workspace"})
    with ws(first):
        found = await ScopedStore(extension="sample").get_many(["chat/a", "chat/b", "chat/x"])
        assert found == {"chat/a": {"title": "one"}, "chat/b": {"title": "two"}}
        assert await ScopedStore(extension="sample").get_many([]) == {}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_scoped_store_scopes_to_the_bound_workspace(db: None) -> None:
    """The store reads the ambient workspace, so rebinding to another workspace never sees the
    first's rows — the isolation is the `with ws(...)` scope, not a field the caller passes."""
    first, second = await _workspace(), await _workspace()
    with ws(first):
        await ScopedStore(extension="sample").put("k", "first-value")
    with ws(second):
        assert await ScopedStore(extension="sample").get("k") is None


def _resolves(slots: frozenset[str]) -> Callable[[], Awaitable[frozenset[str]]]:
    async def read() -> frozenset[str]:
        return slots

    return read


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_access_reads_declared_and_rejects_undeclared(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "sk-real")
    init_workspace_credentials(store)
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(workspace_id):
        assert await access.get("sample_api") == "sk-real"
        with pytest.raises(UndeclaredCredentialSlot, match="undeclared_slot"):
            await access.get("undeclared_slot")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_access_gates_a_slot_the_extension_resolves_per_workspace(
    db: None,
) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "acme_api_key", "sk-workspace")
    init_workspace_credentials(store)
    access = CredentialAccess(declared=frozenset(), resolved=_resolves(frozenset({"acme_api_key"})))
    with ws(workspace_id):
        assert await access.get("acme_api_key") == "sk-workspace"
        assert await access.stored("acme_api_key")
        assert await access.stored_slots() == frozenset({"acme_api_key"})
        for verb in (access.get, access.stored, access.clear):
            with pytest.raises(UndeclaredCredentialSlot, match="undeclared_slot"):
                await verb("undeclared_slot")
        await access.clear("acme_api_key")
        assert await store.stored_slots(workspace_id) == frozenset()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_core_context_builds_and_is_usable(db: None) -> None:
    with ws(await _workspace()):
        context = context_for("core", frozenset())
        assert context.store.extension == "core"
        assert context.installations.declared == frozenset()
        await context.store.put("tick", {"count": 1})
        assert await context.store.get("tick") == {"count": 1}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_installation_access_rejects_operations_for_an_undeclared_surface(
    db: None,
) -> None:
    context = context_for("slack", frozenset())
    with ws(await _workspace()):
        with pytest.raises(UndeclaredSurface, match="slack"):
            await context.installations.installation("slack")
        with pytest.raises(UndeclaredSurface, match="slack"):
            await context.installations.bind("slack", "team-a")
        with pytest.raises(UndeclaredSurface, match="slack"):
            await context.installations.reserve_address(
                "slack", "+14155550123", uuid4(), datetime.now(UTC) + timedelta(minutes=30)
            )


PHONE_SURFACE = "imessage"
SURFACES = frozenset({PHONE_SURFACE})


async def _member(workspace_id: UUID) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id}@example.com",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_reserve_address_holds_one_phone_for_one_member_across_the_fleet(db: None) -> None:
    first, second = await _workspace(), await _workspace()
    mine, theirs, elsewhere = (
        await _member(first),
        await _member(first),
        await _member(second),
    )
    context = context_for("imessage", frozenset(), surfaces=SURFACES, addressed_surfaces=SURFACES)
    phone = "+14155550123"
    live = datetime.now(UTC) + timedelta(minutes=30)
    with ws(first):
        assert await context.installations.reserve_address(PHONE_SURFACE, phone, mine, live) == (
            AddressClaimState.RESERVED
        )
        assert await context.installations.reserve_address(PHONE_SURFACE, phone, mine, live) == (
            AddressClaimState.RESERVED
        )
        assert await context.installations.reserve_address(PHONE_SURFACE, phone, theirs, live) == (
            AddressClaimState.TAKEN
        )
    with ws(second):
        assert (
            await context.installations.reserve_address(PHONE_SURFACE, phone, elsewhere, live)
            == AddressClaimState.TAKEN
        )
    lapsed = datetime.now(UTC) - timedelta(seconds=1)
    with ws(first):
        await context.installations.reserve_address(PHONE_SURFACE, phone, mine, lapsed)
    with ws(second):
        assert (
            await context.installations.reserve_address(PHONE_SURFACE, phone, elsewhere, live)
            == AddressClaimState.RESERVED
        )


async def _conversation(workspace_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=conversation_id.hex,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _seed_turn(
    workspace_id: UUID,
    conversation_id: UUID,
    seq: int,
    status: str,
    terminal: dict | None,
    source: str = "internal",
) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="hi",
                admission_source=source,
                terminal=terminal,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


DONE = {"status": "done"}


async def _opened_at(conversation_id: UUID, moment: datetime) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(created_at=moment)
            .where(tables.conversation.c.id == conversation_id)
        )


async def _named(conversation_id: UUID) -> tuple[str | None, bool]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.title, tables.conversation.c.title_summarized
                ).where(tables.conversation.c.id == conversation_id)
            )
        ).one()
    return row.title, bool(row.title_summarized)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_facts_answers_a_page_in_one_read(db: None) -> None:
    """A member-facing listing decides visibility from the audience of the conversation each row
    reports into, and names its origin from that conversation's surface label."""
    workspace_id = await _workspace()
    member_id = uuid4()
    with ws(workspace_id):
        shared, private = await _conversation(workspace_id), await _conversation(workspace_id)
        spawned = await _conversation(workspace_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.conversation)
                .values(surface=SUBAGENT_SURFACE)
                .where(tables.conversation.c.id == spawned)
            )
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email="listing@x.test",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.update(tables.conversation)
                .values(
                    audience=member_subject(member_id),
                    member_id=member_id,
                    surface_label="#eng",
                )
                .where(tables.conversation.c.id == private)
            )
        facts = await context_for("sample", frozenset()).conversation_facts(
            (shared, private, spawned)
        )

    assert facts[shared] == ConversationFacts(
        audience=SHARED_AUDIENCE, surface_label=None, surface="cli"
    )
    assert facts[private] == ConversationFacts(
        audience=member_subject(member_id), surface_label="#eng", surface="cli"
    )
    assert facts[spawned].surface == SUBAGENT_SURFACE


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_facts_omits_what_it_cannot_vouch_for(db: None) -> None:
    """An unknown id and another tenant's id are both absent from the mapping rather than
    defaulted."""
    workspace_id, other = await _workspace(), await _workspace()
    with ws(other):
        foreign = await _conversation(other)
    with ws(workspace_id):
        mine = await _conversation(workspace_id)
        context = context_for("sample", frozenset())
        facts = await context.conversation_facts((mine, foreign, uuid4()))
        assert await context.conversation_facts(()) == {}

    assert set(facts) == {mine}


async def _arrival(
    workspace_id: UUID, conversation_id: UUID, seq: int, source: str, turn_id: UUID
) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=seq,
                body="hi",
                admission_source=source,
                admitted_turn_id=turn_id,
                created_at=sa.func.now(),
            )
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_turn_outcomes_reads_status_and_terminal_text(db: None) -> None:
    """The last-run line of a status rendering: what the turn's status is, and what its terminal
    frame said."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        spoke = await _seed_turn(
            workspace_id, conversation_id, 1, "done", {"status": "done", "text": "ok"}
        )
        silent = await _seed_turn(workspace_id, conversation_id, 2, "done", {"status": "done"})
        running = await _seed_turn(workspace_id, conversation_id, 3, "running", None)
        outcomes = await context_for("sample", frozenset()).turn_outcomes((spoke, silent, running))

    assert outcomes[spoke] == TurnOutcome(status="done", text="ok")
    assert outcomes[silent] == TurnOutcome(status="done", text=None)
    assert outcomes[running] == TurnOutcome(status="running", text=None)


@dataclass(frozen=True)
class _RecordingProbeCarrier(LocalCarrier):
    specs: list[SandboxSpec] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.specs.append(spec)
        return await super().create(spec)


def _sandboxes(root: Path, carrier: LocalCarrier | None = None) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier() if carrier is None else carrier,
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        workspace_root=root,
    )


def _probes(sandboxes: ConversationSandbox) -> ConversationProbes:
    return ConversationProbes(
        sandboxes, ProbeTokenCodec(b"probe-token-test-secret"), ProbeEnv().exports
    )


def _files(sandboxes: ConversationSandbox) -> ConversationFiles:
    context = context_for("sample", frozenset(), sandboxes=sandboxes)
    assert context.files is not None
    return context.files


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_probe_runs_in_the_conversations_own_sandbox(db: None, tmp_path: Path) -> None:
    """A probe reads what the conversation's workspace holds — the same `/workspace` the agent's
    file tools see — and hands back the command's own stdout and exit code."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        sandboxes = _sandboxes(root)
        await _files(sandboxes).write(conversation_id, "ci/status.txt", b"queued\n")
        result = await _probes(sandboxes).run(conversation_id, "cat /workspace/ci/status.txt")

    assert (result.stdout, result.exit_code) == ("queued\n", 0)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_probes_acting_member_reaches_its_environment(db: None, tmp_path: Path) -> None:
    asked: list[tuple[UUID, UUID | None]] = []

    async def env(conversation_id: UUID, probe_id: UUID, member_id: UUID | None) -> dict[str, str]:
        asked.append((probe_id, member_id))
        return {"PROBE_ID": str(probe_id)}

    workspace_id = await _workspace()
    carrier = _RecordingProbeCarrier()
    member_id = uuid4()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        probes = ConversationProbes(
            _sandboxes(tmp_path / "workspaces", carrier),
            ProbeTokenCodec(b"probe-token-test-secret"),
            env,
        )
        await probes.run(
            conversation_id,
            "true",
            acting_member_id=member_id,
            internet_access=False,
        )
        await probes.run(conversation_id, "true")

    assert [member for _, member in asked] == [member_id, None]
    assert len({probe_id for probe_id, _ in asked}) == 2
    assert [spec.env for spec in carrier.specs] == [
        {"PROBE_ID": str(probe_id)} for probe_id, _ in asked
    ]
    assert all(PROXY_SESSION_ENV_NAMES.isdisjoint(spec.env) for spec in carrier.specs)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_probe_environment_exports_keyed_connectors_but_never_a_model_key(
    db: None,
) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-real")
    slots = (
        CredentialSlot(
            name="datadog_api_key",
            description="datadog key",
            injection=InjectionTarget(
                host="api.datadoghq.com",
                header="dd-api-key",
                sentinel="UFO_SENTINEL_DATADOG_API_KEY",
                env="DD_API_KEY",
            ),
        ),
    )
    with ws(workspace_id):
        exports = await ProbeEnv(credentials=store, slots=WorkspaceSlots(deploy=slots)).exports(
            uuid4(), uuid4()
        )
        bare = await ProbeEnv().exports(uuid4(), uuid4())

    assert exports["DD_API_KEY"] == "UFO_SENTINEL_DATADOG_API_KEY"
    assert "dd-real" not in exports.values()
    assert {ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV}.isdisjoint(exports)
    assert set(bare) == {
        CONVERSATION_ID_ENV,
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_probe_refuses_another_workspaces_conversation(db: None, tmp_path: Path) -> None:
    """The conversation is resolved through `workspace_tx` before anything is opened, so a handler
    holding another tenant's conversation id runs no command in it."""
    root = tmp_path / "workspaces"
    other = await _workspace()
    with ws(other):
        foreign = await _conversation(other)
    with ws(await _workspace()):
        with pytest.raises(ValueError, match="not in this workspace"):
            await _probes(_sandboxes(root)).run(foreign, "echo reached")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_probe_refuses_a_timeout_over_the_ceiling(db: None, tmp_path: Path) -> None:
    """The token's deadline is the timeout, so an unbounded one would be an off-turn exec holding
    egress for as long as it liked."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        probes = _probes(_sandboxes(tmp_path / "workspaces"))
        with pytest.raises(ValueError, match="outside"):
            await probes.run(
                conversation_id,
                "true",
                timeout_s=PROBE_TIMEOUT_MAX_SECONDS + 1,
            )
        with pytest.raises(ValueError, match="outside"):
            await probes.run(conversation_id, "true", timeout_s=0)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_probe_says_so_when_the_bound_terminal_is_gone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", 0.05)
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.conversation)
                .values(sandbox_handle="client:/Users/member/proj")
                .where(tables.conversation.c.id == conversation_id)
            )
        with pytest.raises(TerminalGone):
            await _probes(_sandboxes(tmp_path / "workspaces")).run(conversation_id, "echo hi")


def test_a_context_wired_without_probe_deps_has_no_probes() -> None:
    """Every role builds its context through `context_for`, and only the deploy sites holding the
    probe seam pass it — so a tool or in-turn hook context carries None, not a probe it can run."""
    assert context_for("sample", frozenset()).probes is None
    assert context_for("sample", frozenset(), sandboxes=_sandboxes(Path("/tmp"))).probes is None


@pytest.mark.parametrize("rel", ["../messages.json.lz4", "/etc/passwd", "a/../../escape"])
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_files_refuse_a_path_outside_the_workspace(
    db: None, tmp_path: Path, rel: str
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        with pytest.raises(ValueError):
            await _files(_sandboxes(tmp_path / "workspaces")).write(conversation_id, rel, b"x")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_files_refuse_another_workspaces_conversation(
    db: None, tmp_path: Path
) -> None:
    """The conversation is resolved through `workspace_tx`, so one tenant's handler cannot write a
    file into another tenant's agent workspace even holding its id."""
    root = tmp_path / "workspaces"
    other = await _workspace()
    with ws(other):
        foreign = await _conversation(other)
    with ws(await _workspace()):
        with pytest.raises(ValueError):
            await _files(_sandboxes(root)).write(foreign, "note.txt", b"x")
    assert not (root / str(foreign) / "note.txt").exists()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_files_prune_keeps_the_newest(db: None, tmp_path: Path) -> None:
    """An unattended writer is bounded: prune keeps the newest `keep` entries under the prefix and
    drops the rest, and never reaches a sibling directory."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        files = _files(_sandboxes(root))
        for minute in range(5):
            await files.write(conversation_id, f"log/2026-07-26T00:0{minute}.jsonl", b"{}\n")
        await files.write(conversation_id, "log-sibling/keep.jsonl", b"{}\n")
        await files.prune(conversation_id, "log", keep=2)

    log_dir = root / str(conversation_id) / "log"
    assert sorted(entry.name for entry in log_dir.iterdir()) == [
        "2026-07-26T00:03.jsonl",
        "2026-07-26T00:04.jsonl",
    ]
    assert (root / str(conversation_id) / "log-sibling/keep.jsonl").exists()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_open_conversation_is_the_agents_own_and_keyed_by_its_trigger(db: None) -> None:
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    with ws(workspace_id):
        ext = context_for("coding", frozenset())
        opened = await ext.open_conversation(agent_id, "code-review:abc")
        replayed = await ext.open_conversation(agent_id, "code-review:abc")
        other = await ext.open_conversation(agent_id, "code-review:def")
    assert replayed == opened
    assert other != opened
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.agent_id,
                    tables.conversation.c.member_id,
                    tables.conversation.c.surface,
                    tables.conversation.c.queue_key,
                    tables.conversation.c.audience,
                    tables.conversation.c.sandbox_conversation_id,
                ).where(tables.conversation.c.id == opened)
            )
        ).one()
    assert row.agent_id == agent_id
    assert row.member_id is None
    assert row.surface == "coding"
    assert row.queue_key == "code-review:abc"
    assert row.audience == str(SHARED_AUDIENCE)
    assert row.sandbox_conversation_id is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_open_conversation_binds_a_member_when_named(db: None) -> None:
    """A member-bound trigger room is that member's own — their id and their private audience —
    so its contents stay inside a room that member already reads."""
    workspace_id = await _workspace()
    member_id = uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="bound@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        ext = context_for("coding", frozenset())
        opened = await ext.open_conversation(agent_id, f"brief:{member_id}", member_id=member_id)
        replayed = await ext.open_conversation(agent_id, f"brief:{member_id}", member_id=member_id)
    assert replayed == opened
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id, tables.conversation.c.audience).where(
                    tables.conversation.c.id == opened
                )
            )
        ).one()
    assert row.member_id == member_id
    assert row.audience == str(conversation_audience(member_id))


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_open_conversation_refuses_an_agent_of_another_workspace(db: None) -> None:
    workspace_id = await _workspace()
    other_workspace = await _workspace()
    async with workspace_tx() as connection:
        stranger = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == other_workspace)
            )
        ).scalar_one()
    with ws(workspace_id), pytest.raises(ValueError, match="not an agent of this workspace"):
        await context_for("coding", frozenset()).open_conversation(stranger, "code-review:abc")


@dataclass(frozen=True)
class _CostlyModel:
    """One round whose burn actually prices above zero, so a debit of nothing is a decision rather
    than a rounding result."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="done")
        yield Usage(input_tokens=2_000_000, output_tokens=2_000_000)


async def _charges(workspace_id: UUID) -> list[tuple[int, bool]]:
    async with workspace_tx() as connection:
        return [
            (row.delta_micro_usd, row.platform_paid)
            for row in await connection.execute(
                sa.select(CHARGE_TABLE.c.delta_micro_usd, CHARGE_TABLE.c.platform_paid).where(
                    CHARGE_TABLE.c.workspace_id == workspace_id
                )
            )
        ]


async def _priced(workspace_id: UUID) -> int:
    async with workspace_tx() as connection:
        return int(
            (
                await connection.execute(
                    sa.select(sa.func.sum(tables.ledger.c.priced_micro_usd)).where(
                        tables.ledger.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_background_job_on_the_workspaces_own_key_charges_it_as_self_paid(
    db: None,
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    await store.put(workspace_id, "anthropic_api_key", "workspace-key")
    context = context_for(
        "core",
        frozenset(),
        model_resolver=StubResolver(
            _CostlyModel(),
            funding=KEY_FUNDED,
            payer="anthropic_api_key",
            key_slot="anthropic_api_key",
        ),
        model_job="billing_probe",
        ledger=Ledger(gates=SAMPLE_SPEND.gates),
        spend=NO_SPEND_GATES,
    )
    assert context.model is not None
    with ws(workspace_id):
        await context.model.turn(
            ModelRequest(
                model="auto",
                system="be terse",
                messages=(Message(role="user", content="hi"),),
                max_tokens=64,
                conversation_cache_ttl="5m",
            )
        )
    priced = await _priced(workspace_id)
    assert priced > 0
    assert await _charges(workspace_id) == [(priced, False)]


async def test_a_background_call_bills_the_key_that_built_its_client(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    slot = "anthropic_api_key"
    await store.put(workspace_id, slot, "workspace-key")

    @dataclass(frozen=True)
    class RemovingResolver(StubResolver):
        async def client_for(self, model: str) -> ResolvedModelClient:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.delete(tables.credential).where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )
            return ResolvedModelClient(self.client, self.funding, self.payer)

    context = context_for(
        "core",
        frozenset(),
        model_resolver=RemovingResolver(
            _CostlyModel(), funding=KEY_FUNDED, payer=slot, key_slot=slot
        ),
        model_job="billing_probe",
        ledger=Ledger(gates=SAMPLE_SPEND.gates),
        spend=NO_SPEND_GATES,
    )
    assert context.model is not None
    with ws(workspace_id):
        await context.model.complete(
            ModelRequest(
                model="auto",
                system="be terse",
                messages=(Message(role="user", content="hi"),),
                max_tokens=64,
                conversation_cache_ttl="5m",
            )
        )
    assert await _charges(workspace_id) == [(await _priced(workspace_id), False)]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_extension_reads_what_the_gates_alone_decide_now(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    spend = SpendGates(
        gates=SAMPLE_SPEND.gates,
        key_slot_for=lambda model: "anthropic_api_key" if model == MODEL else None,
        own_key_slots=("anthropic_api_key",),
    )
    context = context_for("core", frozenset(), spend=spend)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await UNGATED_LEDGER.record_workspace_usage(
                connection, workspace_id, MODEL, Usage(input_tokens=1_000)
            )
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope="workspace",
                    subject_id=None,
                    window_seconds=3_600,
                    limit_micro_usd=1,
                    on_breach="reject",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        assert (await context.spend_admitted()).outcome == "allow"
        async with workspace_tx() as connection:
            await allow(connection, workspace_id, 0, "reject")
        refused = SpendDecision(
            outcome="reject", message="The sample allowance is spent at status."
        )
        assert await context.spend_admitted() == refused
        assert await context.spend_admitted(MODEL) == refused
        await store.put(workspace_id, "anthropic_api_key", "workspace-key")
        assert (await context.spend_admitted()).outcome == "allow"
        assert (await context.spend_admitted(MODEL)).outcome == "allow"
        assert await context.spend_admitted("gpt-5.6-luna") == refused


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_context_wired_with_no_spend_refuses_to_read_or_meter_it(db: None) -> None:
    context = context_for("core", frozenset())
    with ws(await _workspace()):
        with pytest.raises(RuntimeError, match="spend gates; none are wired"):
            await context.spend_admitted()
        with pytest.raises(RuntimeError, match="ledger; none is wired"):
            await context.meter_tokens(
                uuid4(), "provider", Usage(input_tokens=10), ModelPrice(1, 0, 0, 0, 0), byok=False
            )
    with pytest.raises(ValueError, match="spend gates and ledger"):
        context_for(
            "core",
            frozenset(),
            model_resolver=StubResolver(RecordingModel()),
            model_job=JOB,
            ledger=UNGATED_LEDGER,
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_only_a_vault_read_context_wired_with_the_vault_resolves_a_secret(
    db: None,
) -> None:
    workspace_id = await _workspace()
    store = _store()
    slot = CredentialSlot(
        name="acme_api_key",
        description="Acme's API key.",
        injection=InjectionTarget(
            host="api.acmekeys.com",
            header="x-api-key",
            sentinel="UFO_SENTINEL_ACME_API_KEY",
            env="ACME_KEY",
        ),
    )
    await store.put(workspace_id, slot.name, "sk-acme")
    vault = VaultReads(store, WorkspaceSlots(deploy=(slot,)), {}, {})

    with ws(workspace_id):
        resolved = await context_for(
            "core", frozenset(), vault_read=True, vault=vault
        ).resolve_secret(slot.name, "api.acmekeys.com")
        with pytest.raises(PermissionError, match="cannot resolve secrets"):
            await context_for("core", frozenset(), vault=vault).resolve_secret(
                slot.name, "api.acmekeys.com"
            )
        with pytest.raises(RuntimeError, match="vault; none is wired"):
            await context_for("core", frozenset(), vault_read=True).resolve_secret(
                slot.name, "api.acmekeys.com"
            )

    assert resolved == SecretValue(value="sk-acme", expires_at=None)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_rollup_reads_the_bound_workspaces_totals_naming_no_member_or_agent(
    db: None,
) -> None:
    workspace_id, neighbor = await _workspace(), await _workspace()
    member_id = await _member(workspace_id)
    turn_id = await _seed_turn(workspace_id, await _conversation(workspace_id), 1, "done", DONE)
    context = context_for("core", frozenset(), ledger=UNGATED_LEDGER)
    price = ModelPrice(42_000, 0, 0, 0, 0)
    with ws(neighbor):
        await context.meter_tokens(uuid4(), MODEL, Usage(input_tokens=1_000), price, byok=False)
    with ws(workspace_id):
        await context.meter_tokens(uuid4(), MODEL, Usage(input_tokens=2_000), price, byok=False)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == turn_id)
                .values(speaker_member_id=member_id)
            )
            await UNGATED_LEDGER.record_turn_usage(
                connection, workspace_id, turn_id, MODEL, Usage(input_tokens=1_000)
            )
            await connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    turn_id=None,
                    dimension=EGRESS_DIMENSION,
                    amount=1,
                    priced_micro_usd=0,
                    model="",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        totals = await context.spend_rollup(None)
        async with workspace_tx() as connection:
            report = await SpendRollup(workspace_id).read(connection, None)
    named = {total.subject_id for total in (*report.by_member, *report.by_agent)} - {None}
    assert member_id in named
    assert len(named) == 2
    assert not any(str(subject) in repr(totals) for subject in named)
    assert totals == SpendTotals(
        None, report.total_micro_usd, report.by_dimension, report.by_service, report.usage
    )
    assert totals.by_service == (ServiceTotal("models", 5_084), ServiceTotal("proxy", 0))
