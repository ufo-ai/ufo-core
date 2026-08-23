"""The report kind: the radar's rows as workspace objects, fenced by the reader's own audiences."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_report_digest.manifest import NAME
from ufo_ext_report_digest.objects import REPORT_OBJECT, ReportSpec
from ufo_ext_report_digest.writer import report_digest_entry
from ufo_ext_scheduled_tasks.schedules import scheduled_task

from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.object_scope import ObjectAgent, object_agent
from ufo.objects import ObjectListQuery, VerbNotSupported
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws

PORTAL = "https://portal.test"
SECRET = "0123456789abcdef0123456789abcdef"


def _ctx() -> ExtensionContext:
    return context_for(NAME, frozenset(), public_base_url=PORTAL, artifact_token_secret=SECRET)


def _query() -> ObjectListQuery:
    return ObjectListQuery(
        order_by="fired_at", order="desc", supported_fields=REPORT_OBJECT.list_fields
    )


async def _seed_workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="dana@example.com",
                timezone="UTC",
                seated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="analyst",
                prompt="Report.",
                model="auto",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                member_id=None,
                audience=str(SHARED_AUDIENCE),
                surface="slack",
                queue_key="slack/radar",
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


async def _seed_member(workspace_id: UUID, email: str) -> UUID:
    member_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                timezone="UTC",
                seated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    return member_id


async def _seed_conversation(
    workspace_id: UUID, agent_id: UUID, *, audience: str, member_id: UUID | None
) -> UUID:
    conversation_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                member_id=member_id,
                audience=audience,
                surface="web",
                queue_key=str(conversation_id),
                created_at=now,
                updated_at=now,
            )
        )
    return conversation_id


async def _seed_task(
    workspace_id: UUID, agent_id: UUID, conversation_id: UUID, member_id: UUID, name: str
) -> UUID:
    task_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(scheduled_task).values(
                id=task_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                name=name,
                created_by_member_id=member_id,
                schedule="0 9 * * *",
                prompt="check the queue",
                description="the queue check",
                next_run_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
                paused=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return task_id


async def _seed_run(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    seq: int,
    fired: datetime,
    status: str = "done",
    key: str | None = None,
    artifact: tuple[str, str] | None = ("queue.png", "image/png"),
) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="fired",
                admission_source="scheduled",
                idempotency_key=key,
                terminal={"status": status, "text": "the run's own last word"},
                created_at=fired,
                updated_at=fired,
            )
        )
        if artifact is not None:
            filename, media_type = artifact
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    turn_id=turn_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    filename=filename,
                    subject="the file",
                    media_type=media_type,
                    size_bytes=3,
                    created_at=fired,
                    updated_at=fired,
                )
            )
    return turn_id


async def _seed_entry(workspace_id: UUID, turn_id: UUID, *, title: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(report_digest_entry).values(
                workspace_id=workspace_id,
                turn_id=turn_id,
                title=title,
                summary="what the run published",
                points=[{"text": "one point", "actor": "the queue"}],
                reader="the queue owner",
                model="model-under-test",
                written_at=datetime(2026, 8, 14, 9, 5, tzinfo=UTC),
            )
        )


async def test_the_report_kind_lists_runs_with_entries_links_and_task_names(db: None) -> None:
    workspace_id, member_id, agent_id, shared_conversation = await _seed_workspace()
    other_id = await _seed_member(workspace_id, "nadia@example.com")
    task_id = await _seed_task(
        workspace_id, agent_id, shared_conversation, member_id, "morning-digest"
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    reported = await _seed_run(
        workspace_id,
        agent_id,
        shared_conversation,
        seq=1,
        fired=fired,
        key=f"{task_id}:2026-08-14T09:00:00+00:00",
    )
    failed = await _seed_run(
        workspace_id,
        agent_id,
        shared_conversation,
        seq=2,
        fired=fired + timedelta(minutes=3),
        status="failed",
        artifact=None,
    )
    private_conversation = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(other_id)), member_id=other_id
    )
    private = await _seed_run(
        workspace_id,
        agent_id,
        private_conversation,
        seq=1,
        fired=fired + timedelta(minutes=1),
        artifact=("mine.md", "text/markdown"),
    )
    await _seed_entry(workspace_id, reported, title="The queue holds two stale items")

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        page = await REPORT_OBJECT.store.member_page(
            _ctx(), member_id=member_id, admin=False, query=_query()
        )
        theirs = await REPORT_OBJECT.store.member_page(
            _ctx(), member_id=other_id, admin=False, query=_query()
        )

    assert [row.name for row in page.rows] == [str(failed), str(reported)]
    stumbled = page.rows[0]
    assert stumbled.fields["status"] == "failed"
    assert stumbled.fields["text"] == "the run's own last word"
    assert stumbled.fields["entry"] is None
    run = page.rows[1]
    assert run.summary == "The queue holds two stale items"
    assert run.fields["task"] == "morning-digest"
    assert run.fields["text"] == ""
    assert run.fields["conversation"] == str(shared_conversation)
    assert run.fields["surface"] == "slack"
    assert run.fields["entry"] == {
        "title": "The queue holds two stale items",
        "summary": "what the run published",
        "points": [{"text": "one point", "actor": "the queue"}],
    }
    (linked,) = run.fields["artifacts"]
    assert linked["filename"] == "queue.png"
    assert linked["url"].startswith(f"{PORTAL}/artifacts/")
    assert "preview" in linked["preview_url"]
    assert {row.name for row in theirs.rows} == {str(failed), str(reported), str(private)}


async def test_the_report_permalink_reads_one_run_under_the_same_fence(db: None) -> None:
    workspace_id, member_id, agent_id, _shared = await _seed_workspace()
    other_id = await _seed_member(workspace_id, "nadia@example.com")
    private_conversation = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(member_id)), member_id=member_id
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    run = await _seed_run(workspace_id, agent_id, private_conversation, seq=1, fired=fired)

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        mine = await REPORT_OBJECT.store.member_detail(
            _ctx(), str(run), member_id=member_id, admin=False
        )
        fenced = await REPORT_OBJECT.store.member_detail(
            _ctx(), str(run), member_id=other_id, admin=False
        )
        nameless = await REPORT_OBJECT.store.member_detail(
            _ctx(), "not-a-turn", member_id=member_id, admin=False
        )

    assert mine is not None
    assert mine.row.name == str(run)
    (link,) = mine.detail.links
    assert (link.relation, link.target.name) == ("created_in", str(private_conversation))
    assert fenced is None
    assert nameless is None


async def test_a_rooms_subjects_fence_the_runs_a_turn_reads(db: None) -> None:
    """The turn path's fence: a run reporting into a workspace-shared conversation answers a
    member's own read, and answers nothing inside a room whose subjects do not carry the shared
    audience — an externally shared room is never handed workspace content."""
    workspace_id, member_id, agent_id, shared_conversation = await _seed_workspace()
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    run = await _seed_run(workspace_id, agent_id, shared_conversation, seq=1, fired=fired)

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        workspace_read = await _ctx().scheduled_runs(
            member_id, agent_id=None, limit=10, subjects=frozenset({str(SHARED_AUDIENCE)})
        )
        sealed_read = await _ctx().scheduled_runs(
            member_id, agent_id=None, limit=10, subjects=frozenset({"foreign:acme/room"})
        )
        pinned = await _ctx().scheduled_runs(
            member_id,
            agent_id=None,
            limit=1,
            turn_id=run,
            subjects=frozenset({"foreign:acme/room"}),
        )

    assert [entry.turn_id for entry in workspace_read] == [run]
    assert sealed_read == ()
    assert pinned == ()


async def test_the_report_kind_refuses_every_mutation(db: None) -> None:
    store = REPORT_OBJECT.store
    with pytest.raises(VerbNotSupported):
        await store.apply(None, "x", ReportSpec(), None, expected_generation=None)  # type: ignore[arg-type]
    with pytest.raises(VerbNotSupported):
        await store.delete(None, "x", expected_generation=None)  # type: ignore[arg-type]
