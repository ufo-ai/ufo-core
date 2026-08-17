import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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

from ufo import o11y
from ufo.agent_scope import agent
from ufo.audience import SHARED_AUDIENCE
from ufo.blob import FilesystemBlobStore
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import (
    PROBE_TIMEOUT_MAX_SECONDS,
    ConversationFacts,
    ConversationFiles,
    ConversationProbes,
    CredentialAccess,
    ScopedStore,
    TurnOutcome,
    UndeclaredCredentialSlot,
    context_for,
    conversation_agent_id,
)
from ufo.ext.manifest import CredentialSlot, InjectionTarget
from ufo.ext.surface import (
    SurfaceInstallationConflict,
    UndeclaredSurface,
)
from ufo.grants import GrantStore, grant_sentinel
from ufo.models.catalog import CORE_PRICING
from ufo.models.interface import (
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
from ufo.models.pricing import Pricing
from ufo.o11y import BACKGROUND_PROFILE
from ufo.sandbox import terminal
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.exec_env import CONVERSATION_ID_ENV, ProbeEnv
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import SENTINEL_MODEL_KEY, ProbeTokenCodec, ProxyEndpoint
from ufo.sandbox.terminal import TerminalGone
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.sources.sync import CorePageFeed
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import WorkspaceUnbound, init_workspace_credentials, ws

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
    """Streams one reasoning round — an encrypted block, a thinking block, an OpenAI reasoning item,
    text — and calls a tool only when `with_tool` is set, so a test can drive both shapes `turn`
    assembles."""

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


@dataclass(frozen=True)
class FailingModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="half an answer")
        raise TimeoutError("provider went away")


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


@dataclass(frozen=True)
class StubResolver:
    """The model registry as `ModelAccess` reads it, wired to one scripted client."""

    client: ModelClient

    @property
    def auto_model(self) -> str:
        return MODEL

    @property
    def pricing(self) -> Pricing:
        return CORE_PRICING

    async def client_for(self, model: str) -> ModelClient:
        return self.client

    def key_slot_for(self, model: str) -> str | None:
        return None

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
    context = context_for("core", frozenset(), model_resolver=StubResolver(model), model_job=job)
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


async def test_model_turn_without_tool_calls_stays_plain_text(db: None) -> None:
    """A round with nothing to authenticate returns its text: there is no tool call coming back, so
    the reasoning has no continuation to ride and never becomes a blocks tuple."""
    assert (await _turn(ReasoningModel(with_tool=False))).content == "checking"


async def test_a_background_call_meters_its_tokens_and_latency_under_its_job(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam a job's `ctx.model` reaches feeds the turn engine's own model series, so one query
    reads member turns and background work: `profile` separates them and `job` says which background
    one spent it. Cache classes are counted apart, and the two write TTLs fold into one
    `cache_write` exactly as a round's do."""
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


async def test_two_jobs_on_one_model_meter_as_two_series(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every background job runs on the one configured model, so the model alone cannot say which
    work spent the tokens — the whole point of the `job` dimension. Two jobs calling the same model
    keep their own series."""
    reader = _metric_capture(monkeypatch)
    await _turn(CachingModel(), job=JOB)
    await _turn(CachingModel(), job="web:chat_titles")
    points = _exported_metrics(reader)
    assert {
        (point.attributes["job"], point.attributes["kind"], point.value)
        for point in points["ufo.model_round_tokens_total"]
    } >= {(JOB, "input", 11), ("web:chat_titles", "input", 11)}


async def test_a_failed_background_call_meters_its_latency_with_the_error_class(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that raises books no spend, so the ledger keeps no trace of it at all. The latency
    observation carrying `error_class` is the only record that the job reached the model, and it
    counts no tokens — nothing was billed."""
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


async def test_a_wired_model_seam_without_its_job_fails_at_the_wiring(db: None) -> None:
    """The label is what keeps the series attributable, so a caller that wires the seam and omits it
    fails where the context is built — never at the first call, mid-job, with an unlabelled series
    already minted."""
    with pytest.raises(ValueError, match="model_job"):
        context_for("core", frozenset(), model_resolver=StubResolver(CachingModel()))


async def test_scoped_store_round_trips_json_values(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("obj", {"a": 1, "nested": [True, None]})
        await store.put("scalar", "hello")
        assert await store.get("obj") == {"a": 1, "nested": [True, None]}
        assert await store.get("scalar") == "hello"
        assert await store.get("absent") is None


async def test_scoped_store_upserts(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("k", "one")
        await store.put("k", "two")
        assert await store.get("k") == "two"


async def test_scoped_store_delete_removes_only_its_key(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("watch:a", 1)
        await store.put("watch:b", 2)
        await store.delete("watch:a")
        assert await store.get("watch:a") is None
        assert await store.list("watch:") == (("watch:b", 2),)


async def test_scoped_store_lists_by_prefix_within_its_extension(db: None) -> None:
    with ws(await _workspace()):
        sample = ScopedStore(extension="sample")
        other = ScopedStore(extension="other")
        await sample.put("run:2", 2)
        await sample.put("run:1", 1)
        await sample.put("cursor", "x")
        await other.put("run:9", 9)
        assert await sample.list("run:") == (("run:1", 1), ("run:2", 2))
        assert await other.list() == (("run:9", 9),)


async def test_scoped_store_isolates_extensions(db: None) -> None:
    with ws(await _workspace()):
        sample = ScopedStore(extension="sample")
        other = ScopedStore(extension="other")
        await sample.put("shared_key", "sample-value")
        assert await other.get("shared_key") is None


async def test_get_many_returns_exactly_the_named_keys_within_its_scopes(db: None) -> None:
    """One query for exactly `keys`: absent keys are omitted, another extension's and another
    workspace's rows under the same keys stay invisible, and an empty key set answers empty. The
    foreign extension sorts after `sample`, so a query missing its extension predicate would
    return the foreign row last and overwrite the right one rather than hide beneath it."""
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


async def test_scoped_store_scopes_to_the_bound_workspace(db: None) -> None:
    """The store reads the ambient workspace, so rebinding to another workspace never sees the
    first's rows — the isolation is the `with ws(...)` scope, not a field the caller passes."""
    first, second = await _workspace(), await _workspace()
    with ws(first):
        await ScopedStore(extension="sample").put("k", "first-value")
    with ws(second):
        assert await ScopedStore(extension="sample").get("k") is None


async def test_scoped_store_outside_a_workspace_scope_fails_loud(db: None) -> None:
    """No ambient workspace → the store raises rather than reading a NULL or wrong workspace."""
    with pytest.raises(WorkspaceUnbound):
        await ScopedStore(extension="sample").get("k")


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


@dataclass(frozen=True)
class MintedCredential:
    value: str | None

    async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
        return self.value

    async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
        return self.value is not None


async def test_credential_access_resolves_manifest_source(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    access = CredentialAccess(
        declared=frozenset({"sample_api"}),
        _sources=(("sample_api", MintedCredential("minted")),),
        _store=store,
    )
    with ws(workspace_id):
        assert await access.resolve("sample_api") == "minted"


async def test_credential_access_resolve_falls_back_to_stored_value(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "stored")
    init_workspace_credentials(store)
    access = CredentialAccess(
        declared=frozenset({"sample_api"}),
        _sources=(("sample_api", MintedCredential(None)),),
        _store=store,
    )
    with ws(workspace_id):
        assert await access.resolve("sample_api") == "stored"


async def test_credential_access_resolve_falls_back_to_platform_value(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAMPLE_API", "platform")
    store = _store()
    init_workspace_credentials(store)
    access = CredentialAccess(
        declared=frozenset({"sample_api"}),
        _sources=(("sample_api", MintedCredential(None)),),
        _store=store,
    )
    with ws(await _workspace()):
        assert await access.resolve("sample_api") == "platform"


async def test_credential_access_resolve_requires_source_store(db: None) -> None:
    access = CredentialAccess(
        declared=frozenset({"sample_api"}),
        _sources=(("sample_api", MintedCredential("minted")),),
    )
    with ws(await _workspace()), pytest.raises(RuntimeError, match="no credential store"):
        await access.resolve("sample_api")


async def test_credential_access_falls_back_to_platform_env(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared slot with no stored BYOK resolves the platform default from env — the same read a
    turn and a job share, so rotating the deploy value reaches every workspace."""
    monkeypatch.setenv("SAMPLE_API", "sk-platform")
    init_workspace_credentials(_store())
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(await _workspace()):
        assert await access.get("sample_api") == "sk-platform"


async def test_empty_platform_credential_is_unset(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAMPLE_API", "")
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(await _workspace()), pytest.raises(CredentialSlotUnset):
        await access.get("sample_api")


async def test_credential_access_outside_a_workspace_scope_fails_loud(db: None) -> None:
    init_workspace_credentials(_store())
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with pytest.raises(WorkspaceUnbound):
        await access.get("sample_api")


async def test_core_context_builds_and_is_usable(db: None) -> None:
    with ws(await _workspace()):
        context = context_for("core", frozenset())
        assert context.store.extension == "core"
        assert context.installations.declared == frozenset()
        await context.store.put("tick", {"count": 1})
        assert await context.store.get("tick") == {"count": 1}


async def test_installation_access_rejects_a_surface_the_manifest_did_not_declare(
    db: None,
) -> None:
    context = context_for("slack", frozenset())
    with ws(await _workspace()), pytest.raises(UndeclaredSurface, match="slack"):
        await context.installations.bind("slack", "team-a")


async def test_installation_access_preserves_fleet_wide_uniqueness(db: None) -> None:
    first, second = await _workspace(), await _workspace()
    context = context_for("slack", frozenset(), surfaces=frozenset({"slack"}))
    with ws(first):
        await context.installations.bind("slack", "team-a")
        await context.installations.bind("slack", "team-a")
    with ws(second), pytest.raises(SurfaceInstallationConflict, match="slack"):
        await context.installations.bind("slack", "team-a")


async def test_set_source_subject_flips_the_row_and_restamps_live_pages(
    db: None, tmp_path: Path
) -> None:
    """The flip assigns every live page a fresh revision so a consumer at the prior high-water
    replays it; a tombstoned page's chunks are already gone, so it is left untouched."""
    workspace_id = await _workspace()
    member_id = uuid4()
    old = datetime(2026, 7, 1, tzinfo=UTC)
    much_older = datetime(2026, 6, 1, tzinfo=UTC)
    source_id, live_id, tombstoned_id = uuid4(), uuid4(), uuid4()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("pages/live", b"live body")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@x.test",
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={"root": "/x"},
                subject=member_subject(member_id),
                owner_member_id=member_id,
                cursor=None,
                next_sync_at=old,
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.page),
            [
                {
                    "id": live_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
                    "digest": "sha256:live",
                    "body_ref": "pages/live",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": tombstoned_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
                    "digest": "sha256:gone",
                    "body_ref": "pages/gone",
                    "subject": member_subject(member_id),
                    "tombstone": True,
                    "created_at": much_older,
                    "updated_at": much_older,
                },
            ],
        )
    async with workspace_tx() as connection:
        high_water = (
            await connection.execute(
                sa.select(tables.page.c.revision, tables.page.c.id)
                .order_by(tables.page.c.revision.desc(), tables.page.c.id.desc())
                .limit(1)
            )
        ).one()
    feed = CorePageFeed(blob=blob)
    cursor = f"{high_water.revision}|{high_water.id}"
    with ws(workspace_id):
        stale = await feed.pages_changed_since(cursor, 10)
        assert stale.changes == ()

        context = context_for("core", frozenset())
        await context.set_source_subject((source_id,), SHARED_SUBJECT)

        with pytest.raises(ValueError):
            await context.set_source_subject((uuid4(),), SHARED_SUBJECT)

        replayed = await feed.pages_changed_since(cursor, 10)
    assert [change.page_id for change in replayed.changes] == [live_id]
    assert replayed.changes[0].subject == SHARED_SUBJECT

    async with workspace_tx() as connection:
        source_row = (
            (
                await connection.execute(
                    sa.select(tables.source).where(tables.source.c.id == source_id)
                )
            )
            .mappings()
            .one()
        )
        pages = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.page).where(tables.page.c.source_id == source_id)
                )
            )
            .mappings()
            .all()
        }

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    assert source_row["subject"] == SHARED_SUBJECT
    assert pages[live_id]["subject"] == SHARED_SUBJECT
    assert _aware(pages[live_id]["updated_at"]) > old
    assert pages[tombstoned_id]["subject"] == member_subject(member_id)
    assert _aware(pages[tombstoned_id]["updated_at"]) == much_older


async def test_set_source_subject_flips_every_stream_of_a_binding_in_one_transaction(
    db: None,
) -> None:
    """A binding's streams are distinct source rows; one call flips every row and restamps every
    live page across them in a single transaction, so a multi-stream share can never tear across
    per-stream commits. A tombstoned page is left untouched."""
    workspace_id = await _workspace()
    member_id = uuid4()
    old = datetime(2026, 7, 1, tzinfo=UTC)
    much_older = datetime(2026, 6, 1, tzinfo=UTC)
    first_id, second_id = uuid4(), uuid4()
    live_a, live_b, tombstoned_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@x.test",
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.source),
            [
                {
                    "id": source_id,
                    "workspace_id": workspace_id,
                    "backend": "greenhouse",
                    "config": {"account": "default", "stream": stream, "base_url": None},
                    "subject": member_subject(member_id),
                    "owner_member_id": member_id,
                    "cursor": None,
                    "next_sync_at": old,
                    "created_at": old,
                    "updated_at": old,
                }
                for source_id, stream in ((first_id, "jobs"), (second_id, "candidates"))
            ],
        )
        await connection.execute(
            sa.insert(tables.page),
            [
                {
                    "id": live_a,
                    "workspace_id": workspace_id,
                    "source_id": first_id,
                    "digest": "sha256:a",
                    "body_ref": "pages/a",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": live_b,
                    "workspace_id": workspace_id,
                    "source_id": second_id,
                    "digest": "sha256:b",
                    "body_ref": "pages/b",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": tombstoned_id,
                    "workspace_id": workspace_id,
                    "source_id": second_id,
                    "digest": "sha256:gone",
                    "body_ref": "pages/gone",
                    "subject": member_subject(member_id),
                    "tombstone": True,
                    "created_at": much_older,
                    "updated_at": much_older,
                },
            ],
        )
    context = context_for("core", frozenset())
    with ws(workspace_id):
        await context.set_source_subject((first_id, second_id), SHARED_SUBJECT)

    async with workspace_tx() as connection:
        sources = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.source).where(tables.source.c.id.in_((first_id, second_id)))
                )
            )
            .mappings()
            .all()
        }
        pages = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.page).where(tables.page.c.source_id.in_((first_id, second_id)))
                )
            )
            .mappings()
            .all()
        }

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    assert {row["subject"] for row in sources.values()} == {SHARED_SUBJECT}
    assert pages[live_a]["subject"] == SHARED_SUBJECT
    assert pages[live_b]["subject"] == SHARED_SUBJECT
    assert _aware(pages[live_a]["updated_at"]) > old
    assert _aware(pages[live_b]["updated_at"]) > old
    assert pages[tombstoned_id]["subject"] == member_subject(member_id)
    assert _aware(pages[tombstoned_id]["updated_at"]) == much_older


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
    workspace_id: UUID, conversation_id: UUID, seq: int, status: str, terminal: dict | None
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
                terminal=terminal,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_conversation_facts_answers_a_page_in_one_read(db: None) -> None:
    """A member-facing listing decides visibility from the audience of the conversation each row
    reports into, and names its origin from that conversation's surface label. Both come back for a
    whole page at once, and both are read live: an audience never moves, but a renamed channel would
    leave a snapshotted label describing a place that no longer answers to it."""
    workspace_id = await _workspace()
    member_id = uuid4()
    with ws(workspace_id):
        shared, private = await _conversation(workspace_id), await _conversation(workspace_id)
        async with workspace_tx() as connection:
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
        facts = await context_for("sample", frozenset()).conversation_facts((shared, private))

    assert facts[shared] == ConversationFacts(audience=SHARED_AUDIENCE, surface_label=None)
    assert facts[private] == ConversationFacts(
        audience=member_subject(member_id), surface_label="#eng"
    )


async def test_conversation_facts_omits_what_it_cannot_vouch_for(db: None) -> None:
    """An unknown id and another tenant's id are both absent from the mapping rather than defaulted.
    A caller deciding disclosure has to read that absence as "not visible" — standing in a shared
    audience would publish a row nothing vouched for, and it is the same absence either way, so a
    borrowed id leaks nothing about whether it exists elsewhere."""
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


async def test_conversation_arrival_seq_watermarks_member_arrivals_only(db: None) -> None:
    """The watermark work compares against to tell "a member spoke before I armed" from "a member
    spoke after". It is the highest member `seq`, so it must ignore three things that would each
    corrupt that comparison: an internal arrival (work the system posted to itself — counting it
    would let a watcher be woken by its own effects), another conversation's arrivals, and another
    workspace's. Zero is a real answer meaning no member has spoken."""
    workspace_id, other_workspace = await _workspace(), await _workspace()
    with ws(other_workspace):
        elsewhere = await _conversation(other_workspace)
        await _arrival(
            other_workspace,
            elsewhere,
            99,
            "member",
            await _seed_turn(other_workspace, elsewhere, 1, "running", None),
        )
    with ws(workspace_id):
        watched = await _conversation(workspace_id)
        neighbour = await _conversation(workspace_id)
        context = context_for("sample", frozenset())
        assert await context.conversation_arrival_seq(watched) == 0

        turn_id = await _seed_turn(workspace_id, watched, 1, "running", None)
        await _arrival(workspace_id, watched, 1, "member", turn_id)
        await _arrival(workspace_id, watched, 2, "member", turn_id)
        assert await context.conversation_arrival_seq(watched) == 2

        await _arrival(workspace_id, watched, 3, "internal", turn_id)
        assert await context.conversation_arrival_seq(watched) == 2

        await _arrival(
            workspace_id,
            neighbour,
            50,
            "member",
            await _seed_turn(workspace_id, neighbour, 1, "running", None),
        )
        assert await context.conversation_arrival_seq(watched) == 2
        assert await context.conversation_arrival_seq(neighbour) == 50


async def test_turn_outcomes_reads_status_and_terminal_text(db: None) -> None:
    """The last-run line of a status rendering: what the turn's status is, and what its terminal
    frame said. A running turn and a turn that ended saying nothing both answer None for the text,
    so a renderer has one absence to handle rather than two."""
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


async def test_turn_outcomes_omits_a_turn_that_is_no_longer_there(db: None) -> None:
    """A row can point at a turn that has since gone. That reads as an absent key, never a raise:
    the status line renders it exactly as it renders a row that has not fired yet, which is what the
    outer join it replaces did."""
    workspace_id, other = await _workspace(), await _workspace()
    with ws(other):
        foreign = await _seed_turn(other, await _conversation(other), 1, "done", {"text": "theirs"})
    with ws(workspace_id):
        context = context_for("sample", frozenset())
        outcomes = await context.turn_outcomes((foreign, uuid4()))
        assert await context.turn_outcomes(()) == {}

    assert outcomes == {}


@dataclass(frozen=True)
class _UnusedForwarder:
    """A real `RequestForwarder` the export path never calls — forwarding happens at the proxy, not
    when the sentinel is written into the environment. It raises rather than recording, so a probe
    that somehow reached the broker fails loudly instead of passing quietly."""

    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        raise AssertionError("the probe environment must not forward through the broker")


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
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


async def test_conversation_files_write_lands_in_the_conversation_workspace(
    db: None, tmp_path: Path
) -> None:
    """What an off-turn handler writes is what the agent's next turn sees: the returned path is the
    container path, and the bytes land in the conversation's workspace directory the carrier
    serves."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        path = await _files(_sandboxes(root)).write(
            conversation_id, ".sources/acme/now.jsonl", b"{}\n"
        )

    assert path == "/workspace/.sources/acme/now.jsonl"
    assert (root / str(conversation_id) / ".sources/acme/now.jsonl").read_bytes() == b"{}\n"


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


async def test_a_probes_nonzero_exit_is_a_result_not_a_raise(db: None, tmp_path: Path) -> None:
    """Reading what a command reports is the point, so a failing command answers its exit code and
    stderr rather than raising: the caller decides whether a failure is news."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        result = await _probes(_sandboxes(tmp_path / "workspaces")).run(
            conversation_id, "echo nope >&2; exit 3"
        )

    assert (result.exit_code, result.stdout, result.stderr.strip()) == (3, "", "nope")


async def test_a_probe_names_its_conversation_in_the_environment(db: None, tmp_path: Path) -> None:
    """Work a probe leaves outside the workspace joins back to the record that produced it through
    the same variable a turn states it in."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        result = await _probes(_sandboxes(tmp_path / "workspaces")).run(
            conversation_id, f"printenv {CONVERSATION_ID_ENV}"
        )

    assert result.stdout.strip() == str(conversation_id)


async def test_a_probe_runs_under_the_jobs_role_binding_production_provides(
    db: None, tmp_path: Path
) -> None:
    """A job binds a workspace and no agent — nothing has an agent to bind, since a probe answers to
    no turn — yet the connector-CLI export reads the bound agent's grants. Every deploy whose pack
    declares a CLI credential therefore takes that branch on every probe, so this binds exactly what
    `JobRunner.fire` binds and nothing more: `ws()` alone, a real `GrantStore`, and a non-empty
    connector CLI map. The probe must run and its environment must carry that grant's sentinel."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        agent_id = await conversation_agent_id(workspace_id, conversation_id)
        assert agent_id is not None
        member_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email="armer@x.test",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with agent(agent_id):
            await GrantStore().record(
                provider="hub",
                account_id="acct-1",
                host="api.hub.test",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=True,
            )
        probes = ConversationProbes(
            _sandboxes(root),
            ProbeTokenCodec(b"probe-token-test-secret"),
            ProbeEnv(
                grants=GrantStore(),
                clis={
                    "hub": CliCredential(
                        env="HUB_TOKEN", header="authorization", forward=_UnusedForwarder()
                    )
                },
            ).exports,
        )
        result = await probes.run(conversation_id, "printenv HUB_TOKEN")

    assert result.exit_code == 0
    assert result.stdout.strip() == grant_sentinel("acct-1")


async def test_a_probes_acting_member_reaches_the_environment_and_the_token(
    db: None, tmp_path: Path
) -> None:
    """The member a probe acts as has to reach both ends or it buys nothing: the signed token, so
    the proxy derives that member's forwards, and the environment, so the CLI inside the sandbox has
    a sentinel to send. This pins the second — the first is the proxy's own test — by recording what
    the env derivation was asked for."""
    asked: list[tuple[UUID, UUID | None]] = []

    async def env(
        conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None = None
    ) -> dict[str, str]:
        asked.append((probe_id, acting_member_id))
        return {}

    workspace_id = await _workspace()
    member_id = uuid4()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        probes = ConversationProbes(
            _sandboxes(tmp_path / "workspaces"), ProbeTokenCodec(b"probe-token-test-secret"), env
        )
        await probes.run(conversation_id, "true", acting_member_id=member_id)
        await probes.run(conversation_id, "true")

    assert [member for _, member in asked] == [member_id, None]
    assert len({probe_id for probe_id, _ in asked}) == 2


async def test_the_probe_environment_exports_keyed_connectors_but_never_a_model_key(
    db: None,
) -> None:
    """A watch on a keyed provider needs that provider's sentinel — the proxy holds the matching
    injection rule either way, so withholding the variable would leave the rule inert and 401 every
    probe. The deployment's own model key is the one thing withheld, and it is withheld where it
    lives: the platform sentinel rides the carrier's environment and the proxy declines its rule, so
    nothing here has to name it. A BYOK model key declares no injection target at all, so this
    derivation never sees one."""
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
        exports = await ProbeEnv(credentials=store, slots=slots).exports(uuid4(), uuid4())
        bare = await ProbeEnv().exports(uuid4(), uuid4())

    assert exports["DD_API_KEY"] == "UFO_SENTINEL_DATADOG_API_KEY"
    assert "dd-real" not in exports.values()
    assert SENTINEL_MODEL_KEY not in exports.values()
    assert SENTINEL_MODEL_KEY not in bare.values()
    assert set(bare) == {
        CONVERSATION_ID_ENV,
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }


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


async def test_a_probe_refuses_a_timeout_over_the_ceiling(db: None, tmp_path: Path) -> None:
    """The token's deadline is the timeout, so an unbounded one would be an off-turn exec holding
    egress for as long as it liked."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        probes = _probes(_sandboxes(tmp_path / "workspaces"))
        with pytest.raises(ValueError, match="outside"):
            await probes.run(conversation_id, "true", timeout_s=PROBE_TIMEOUT_MAX_SECONDS + 1)
        with pytest.raises(ValueError, match="outside"):
            await probes.run(conversation_id, "true", timeout_s=0)


async def test_a_probe_says_so_when_the_bound_terminal_is_gone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversation whose workspace is a member's own terminal has nowhere to run a probe while
    that terminal is disconnected, and the refusal reaches the caller rather than reading as an
    empty result: a tick that could not probe is not a tick that found nothing, and only the caller
    knows which of those is worth reporting."""
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
async def test_conversation_files_refuse_a_path_outside_the_workspace(
    db: None, tmp_path: Path, rel: str
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        with pytest.raises(ValueError):
            await _files(_sandboxes(tmp_path / "workspaces")).write(conversation_id, rel, b"x")


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


async def test_conversation_files_write_replaces_a_planted_symlink(
    db: None, tmp_path: Path
) -> None:
    """An off-turn write lands in a directory the agent writes to on its turns, so the name it is
    given may already be a link the agent planted. The copy-in replaces the link instead of
    delivering through it: the host file it pointed at is untouched, and the attachment still
    arrives — refusing would hand the agent a way to deny that name for good."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        files = _files(_sandboxes(root))
        await files.write(conversation_id, "inbox/first.txt", b"{}\n")
        planted = root / str(conversation_id) / "inbox" / "planted.txt"
        planted.symlink_to(outside)

        await files.write(conversation_id, "inbox/planted.txt", b"delivered")

    assert outside.read_bytes() == b"host secret"
    assert not planted.is_symlink()
    assert planted.read_bytes() == b"delivered"


async def test_conversation_files_prune_refuses_a_symlinked_prefix(
    db: None, tmp_path: Path
) -> None:
    """Prune is the sharpest verb here — it deletes. A link planted at the prefix would aim the
    deletion at whatever it points to, so the directory is proved to be a real one inside the
    workspace before a single name is unlinked."""
    workspace_id = await _workspace()
    root = tmp_path / "workspaces"
    outside = tmp_path / "outside"
    outside.mkdir()
    for minute in range(3):
        (outside / f"host-{minute}.txt").write_bytes(b"host file")
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        files = _files(_sandboxes(root))
        await files.write(conversation_id, "keep/anchor.jsonl", b"{}\n")
        (root / str(conversation_id) / "log").symlink_to(outside, target_is_directory=True)

        with pytest.raises(OSError, match="escapes"):
            await files.prune(conversation_id, "log", keep=1)

    assert len(list(outside.iterdir())) == 3


@pytest.mark.parametrize("rel_prefix", ["../..", "log/../../..", "/etc"])
async def test_conversation_files_prune_refuses_a_prefix_outside_the_workspace(
    db: None, tmp_path: Path, rel_prefix: str
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        files = _files(_sandboxes(tmp_path / "workspaces"))
        await files.write(conversation_id, "log/keep.jsonl", b"{}\n")

        with pytest.raises((ValueError, OSError)):
            await files.prune(conversation_id, rel_prefix, keep=0)

    assert (tmp_path / "workspaces" / str(conversation_id) / "log/keep.jsonl").exists()


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


async def test_open_conversation_is_the_agents_own_and_keyed_by_its_trigger(db: None) -> None:
    """A conversation a trigger opens belongs to the agent that does the work and to no member, so
    it lists under that agent and reaches no member's rail; the key is the event's identity, so a
    replayed batch reopens the one it opened rather than a second."""
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
