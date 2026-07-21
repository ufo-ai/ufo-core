"""End-to-end proof of the page-alerts seam: the chat tool binds a watch to its conversation, and
the page_change hook classifies changed pages against it — a match invokes a real admitted turn
into the bound conversation, registered for delivery when that conversation's surface is durable.

The hook drives the real `Admission` (real turn row, real writeback registration); the DBOS enqueue
and the paid model are the only stand-ins — the classifier client returns a scripted verdict and
records each request, so the tests assert bounded prompts and call counts without a provider."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_page_alerts.alerts import (
    PAGE_EXCERPT_CHARS,
    WATCH_PREFIX,
    CancelPageWatchInput,
    ListPageWatchesInput,
    WatchPagesInput,
    cancel_page_watch,
    list_page_watches,
    on_page_change,
    watch_pages,
)
from ufo_ext_page_alerts.manifest import NAME, manifest

from ufo.accounting import CORE_PRICING, Pricing
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore, context_for
from ufo.ext.manifest import HookContext, PageChangeBatch
from ufo.models.interface import ModelClient, ModelRequest, TextDelta
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, Agent, Turn, Usage
from ufo.sources.sync import PageChange
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

BILLED_MODEL = "claude-opus-4-8"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


@dataclass
class ScriptedClassifier:
    """Stands in for the paid model at the ModelClient seam: answers a scripted verdict and records
    each request, so tests assert what was asked without a provider."""

    verdict: str
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest):
        self.requests.append(request)
        yield TextDelta(text=self.verdict)
        yield Usage(input_tokens=3, output_tokens=1)


@dataclass
class _Resolver:
    """A ModelResolver standing in for the registry: fixed auto_model and pricing, and a client_for
    that hands back the one scripted classifier the metered seam completes against."""

    auto_model: str
    client: ModelClient
    pricing: Pricing = CORE_PRICING

    async def client_for(self, model: str) -> ModelClient:
        return self.client

    def key_slot_for(self, model: str) -> str | None:
        return None


async def _seed(surface: str = "slack") -> tuple[UUID, UUID, UUID]:
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
                model=BILLED_MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface=surface,
                queue_key="D123",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the page-alerts tests")


def _tool_ctx(workspace_id: UUID, conversation_id: UUID, agent_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="watch my pages",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model=BILLED_MODEL),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


def _hook_ctx(
    workspace_id: UUID, classifier: ScriptedClassifier, changes: tuple[PageChange, ...]
) -> HookContext:
    ext = context_for(
        NAME,
        frozenset(),
        invoker=AdmissionInvoker(
            admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset({"slack"})),
            workspace_id=workspace_id,
        ),
        model_resolver=_Resolver(BILLED_MODEL, classifier),
    )
    return HookContext(ext=ext, payload=PageChangeBatch(changes=changes))


def _page(subject: str, body: str, tombstone: bool = False) -> PageChange:
    now = datetime.now(UTC)
    return PageChange(
        page_id=uuid4(),
        source_id=uuid4(),
        subject=subject,
        body=body,
        digest=f"digest-{subject}",
        tombstone=tombstone,
        created_at=now,
        changed_at=now,
    )


async def _turns(conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn).where(tables.turn.c.conversation_id == conversation_id)
                )
            )
            .mappings()
            .all()
        )


async def test_watch_pages_tool_binds_the_conversation(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id):
        result = await watch_pages(ctx, WatchPagesInput(topic="orbital widget fleet"))
        assert result.is_error is False
        stored = await ScopedStore(extension=NAME).get(f"{WATCH_PREFIX}orbital-widget-fleet")
    assert stored == {
        "topic": "orbital widget fleet",
        "conversation_id": str(conversation_id),
        "agent_id": str(agent_id),
    }


async def test_matching_page_invokes_a_delivered_alert_turn_idempotently(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed(surface="slack")
    classifier = ScriptedClassifier(verdict="MATCH")
    changes = (_page("handbook", "the orbital widget fleet migrates next quarter " + "x" * 3000),)
    with ws(workspace_id):
        await watch_pages(
            _tool_ctx(workspace_id, conversation_id, agent_id),
            WatchPagesInput(topic="orbital widget fleet"),
        )
        await on_page_change(_hook_ctx(workspace_id, classifier, changes))
        await on_page_change(_hook_ctx(workspace_id, classifier, changes))

    turns = await _turns(conversation_id)
    assert len(turns) == 1
    assert "orbital widget fleet" in turns[0]["inbound"]
    assert len(classifier.requests) == 2
    prompt = classifier.requests[0].messages[0].content
    assert isinstance(prompt, str)
    assert len(prompt) < PAGE_EXCERPT_CHARS + 200
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.writeback.c.status).where(
                    tables.writeback.c.turn_id == turns[0]["id"]
                )
            )
        ).scalar_one()
    assert status == WRITEBACK_PENDING


async def test_non_matching_page_alerts_nothing(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    classifier = ScriptedClassifier(verdict="NO")
    with ws(workspace_id):
        await watch_pages(
            _tool_ctx(workspace_id, conversation_id, agent_id), WatchPagesInput(topic="widgets")
        )
        await on_page_change(
            _hook_ctx(workspace_id, classifier, (_page("memo", "quarterly parking rota"),))
        )
    assert await _turns(conversation_id) == []
    assert len(classifier.requests) == 1


async def test_tombstones_and_watchless_batches_never_reach_the_model(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    classifier = ScriptedClassifier(verdict="MATCH")
    with ws(workspace_id):
        await on_page_change(
            _hook_ctx(workspace_id, classifier, (_page("gone", "", tombstone=True),))
        )
        assert classifier.requests == []
        await watch_pages(
            _tool_ctx(workspace_id, conversation_id, agent_id), WatchPagesInput(topic="widgets")
        )
        await on_page_change(
            _hook_ctx(workspace_id, classifier, (_page("gone", "", tombstone=True),))
        )
        assert classifier.requests == []
    assert await _turns(conversation_id) == []


async def test_cancel_page_watch_removes_it_and_unknown_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id):
        await watch_pages(ctx, WatchPagesInput(topic="widgets", name="fleet watch"))
        listed = await list_page_watches(ctx, ListPageWatchesInput())
        assert "fleet-watch" in listed.content[0].text
        cancelled = await cancel_page_watch(ctx, CancelPageWatchInput(name="fleet-watch"))
        assert "Cancelled" in cancelled.content[0].text
        assert await ScopedStore(extension=NAME).list(WATCH_PREFIX) == ()
        with pytest.raises(ValueError, match="no page watch"):
            await cancel_page_watch(ctx, CancelPageWatchInput(name="fleet-watch"))


def test_manifest_declares_the_tools_and_the_page_change_hook() -> None:
    declared = manifest()
    assert {tool.name for tool in declared.tools} == {
        "watch_pages",
        "list_page_watches",
        "cancel_page_watch",
    }
    assert [hook.event for hook in declared.hooks] == ["page_change"]
