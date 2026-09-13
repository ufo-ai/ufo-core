from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_memory.store import body_digest, memory_item
from ufo_ext_objectives.store import objective, objective_event, objective_step

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import _member_blob_text, context_for
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.turns.audience import conversation_audience, foreign_room_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7

pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, other_member_id = uuid4(), uuid4(), uuid4()
    main_id, notes_id, brief_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member),
            (
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": "member@example.com",
                    "timezone": "America/New_York",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": other_member_id,
                    "workspace_id": workspace_id,
                    "email": "other@example.com",
                    "timezone": None,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
        await connection.execute(
            sa.insert(tables.agent),
            (
                {
                    "id": main_id,
                    "workspace_id": workspace_id,
                    "name": "assistant",
                    "prompt": "Help.",
                    "model": "auto",
                    "is_main": True,
                    "provisioned_by": None,
                    "provisioned_name": None,
                    "provisioned_version": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": notes_id,
                    "workspace_id": workspace_id,
                    "name": "notes",
                    "prompt": "Handle notes.",
                    "model": "auto",
                    "is_main": False,
                    "provisioned_by": None,
                    "provisioned_name": None,
                    "provisioned_version": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": brief_id,
                    "workspace_id": workspace_id,
                    "name": "digest",
                    "prompt": "Brief.",
                    "model": "auto",
                    "is_main": False,
                    "provisioned_by": None,
                    "provisioned_name": None,
                    "provisioned_version": None,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    return workspace_id, member_id, other_member_id, brief_id


async def test_member_blob_read_closes_a_bounded_stream(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    async def oversized(_store: FilesystemBlobStore, _key: str) -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"x" * 10_001
        finally:
            closed = True

    monkeypatch.setattr(FilesystemBlobStore, "get_stream", oversized)
    with ws(uuid4()):
        text = await _member_blob_text(
            WorkspaceBlobStore(FilesystemBlobStore(tmp_path)), "pages/large"
        )
    assert len(text) == 10_000
    assert closed


async def test_workspace_agents_carry_the_roster_with_owners_and_archive_state(db: None) -> None:
    workspace_id, member_id, _other_member_id, brief_id = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == brief_id)
            .values(
                name=f"~archived-{brief_id}",
                archived_name=tables.agent.c.name,
                owner_member_id=member_id,
                tools=["load_skill", "action:report:rebuild_report_digest"],
                archived_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        context = context_for("report_digest", frozenset(), member_context_read=True)
        agents = await context.workspace_agents()
    assert {a.name for a in agents} == {"assistant", "notes", "digest"}
    owners = {a.id: a.owner_member_id for a in agents}
    assert owners[brief_id] == member_id
    assert [owner for agent_id, owner in owners.items() if agent_id != brief_id] == [None, None]
    allowlists = {a.id: a.tools for a in agents}
    assert allowlists[brief_id] == ("load_skill", "action:report:rebuild_report_digest")
    assert [tools for agent_id, tools in allowlists.items() if agent_id != brief_id] == [None, None]
    archived = {a.id: a.archived for a in agents}
    assert archived[brief_id] is True
    assert [held for agent_id, held in archived.items() if agent_id != brief_id] == [False, False]


async def test_agent_visibilities_answer_by_id_without_member_context(db: None) -> None:
    workspace_id, _member_id, _other_member_id, brief_id = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == brief_id)
            .values(visibility="workspace")
        )
    with ws(workspace_id):
        context = context_for("report_digest", frozenset())
        visibilities = await context.agent_visibilities()
    assert visibilities[brief_id] == "workspace"
    assert {level for agent_id, level in visibilities.items() if agent_id != brief_id} == {
        "private"
    }


async def test_an_agent_identity_answers_by_live_name_without_member_context(db: None) -> None:
    workspace_id, member_id, _other_member_id, brief_id = await _seed()
    async with workspace_tx() as connection:
        brief_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == brief_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == brief_id)
            .values(owner_member_id=member_id)
        )
    with ws(workspace_id):
        context = context_for("report_digest", frozenset())
        brief = await context.agent_named(brief_name)
        assert brief is not None
        assert (brief.id, brief.owner_member_id) == (brief_id, member_id)
        notes = await context.agent_named("notes")
        assert notes is not None
        assert notes.id != brief_id and notes.owner_member_id is None
        assert await context.agent_named("nobody") is None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == brief_id)
            .values(
                name=f"~archived-{brief_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert await context.agent_named(brief_name) is None
        assert await context.agent_named(f"~archived-{brief_id}") is None


async def test_member_reads_are_gated_on_member_context(db: None) -> None:
    workspace_id, member_id, _other_member_id, _brief_id = await _seed()
    with ws(workspace_id):
        context = context_for("report_digest", frozenset())
        with pytest.raises(PermissionError):
            await context.workspace_agents()
        with pytest.raises(PermissionError):
            await context.earliest_seated_admin()
        with pytest.raises(PermissionError):
            await context.scheduled_member_timezone()
        unbound = context_for("report_digest", frozenset(), member_context_read=True)
        with pytest.raises(PermissionError):
            await unbound.scheduled_member_timezone()
        bound = context_for(
            "report_digest",
            frozenset(),
            member_context_read=True,
            member_context_member_id=member_id,
        )
        assert await bound.scheduled_member_timezone() == "America/New_York"


async def test_earliest_seated_admin_is_deterministic(db: None) -> None:
    workspace_id, member_id, other_member_id, _brief_id = await _seed()
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 2, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == other_member_id)
            .values(is_admin=True, seated_at=early)
        )
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == member_id)
            .values(is_admin=True, seated_at=late)
        )
    with ws(workspace_id):
        context = context_for("report_digest", frozenset(), member_context_read=True)
        assert await context.earliest_seated_admin() == other_member_id
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.workspace_id == workspace_id)
                .values(is_admin=False)
            )
        assert await context.earliest_seated_admin() is None


async def test_member_context_excludes_foreign_other_member_and_current_conversation(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, other_member_id, brief_id = await _seed()
    now = datetime.now(UTC)
    connection_id, source_uid = uuid4(), uuid7()
    page_id, missing_page_id, invalid_page_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        main_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.is_main.is_(True))
            )
        ).scalar_one()
        cases = (
            ("shared", None, "shared", main_id, "test"),
            ("mine", member_id, str(conversation_audience(member_id)), main_id, "test"),
            (
                "subagent",
                member_id,
                str(conversation_audience(member_id)),
                main_id,
                "subagent",
            ),
            (
                "other",
                other_member_id,
                str(conversation_audience(other_member_id)),
                main_id,
                "test",
            ),
            ("foreign", None, str(foreign_room_audience("slack", "outside")), main_id, "test"),
            (
                "self",
                member_id,
                str(conversation_audience(member_id)),
                brief_id,
                "web",
            ),
        )
        conversation_ids: dict[str, UUID] = {}
        turn_ids: dict[str, UUID] = {}
        for index, (title, owner, audience_value, agent_id, surface) in enumerate(cases):
            conversation_id = uuid4()
            turn_id = uuid4()
            conversation_ids[title] = conversation_id
            turn_ids[title] = turn_id
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=surface,
                    queue_key=title,
                    title=title,
                    member_id=owner,
                    audience=audience_value,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound=f"{title} input",
                    terminal={"status": "done", "text": f"{title} result"},
                    created_at=now + timedelta(seconds=index),
                    updated_at=now + timedelta(seconds=index),
                )
            )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_ids["mine"],
                blob_key="artifacts/notes.txt",
                id=uuid4(),
                workspace_id=workspace_id,
                filename="notes.txt",
                subject="Notes",
                media_type="text/plain",
                size_bytes=18,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_ids["mine"],
                blob_key="artifacts/invalid.txt",
                id=uuid4(),
                workspace_id=workspace_id,
                filename="invalid.txt",
                subject="Invalid text",
                media_type="text/plain",
                size_bytes=1,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_ids["mine"],
                blob_key="artifacts/missing.txt",
                id=uuid4(),
                workspace_id=workspace_id,
                filename="missing.txt",
                subject="Missing notes",
                media_type="text/plain",
                size_bytes=18,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_ids["mine"],
                blob_key="artifacts/compose.yaml",
                id=uuid4(),
                workspace_id=workspace_id,
                filename="compose.yaml",
                subject="Compose",
                media_type="application/yaml",
                size_bytes=12,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="probe",
                account_id="",
                host="",
                owner_member_id=None,
                shared=True,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend="probe",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page),
            (
                {
                    "uid": page_id,
                    "source_uid": source_uid,
                    "workspace_id": workspace_id,
                    "digest": "sha256:page",
                    "body_ref": f"pages/{page_id}",
                    "stream": "reports",
                    "title": "Weekly report",
                    "subject": "shared",
                    "tombstone": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "uid": missing_page_id,
                    "workspace_id": workspace_id,
                    "source_uid": source_uid,
                    "digest": "sha256:missing",
                    "body_ref": f"pages/{missing_page_id}",
                    "stream": "reports",
                    "title": "Missing report",
                    "subject": "shared",
                    "tombstone": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "uid": invalid_page_id,
                    "workspace_id": workspace_id,
                    "source_uid": source_uid,
                    "digest": "sha256:invalid",
                    "body_ref": f"pages/{invalid_page_id}",
                    "stream": "reports",
                    "title": "Invalid report",
                    "subject": "shared",
                    "tombstone": False,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
        await connection.execute(
            sa.insert(memory_item),
            (
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "subject": "shared",
                    "body": "Finish the brief.",
                    "body_digest": body_digest("Finish the brief."),
                    "item_class": "fact",
                    "memory_kind": "task",
                    "confidence": 8,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "subject": f"member:{other_member_id}",
                    "body": "Other member task.",
                    "body_digest": body_digest("Other member task."),
                    "item_class": "fact",
                    "memory_kind": "task",
                    "confidence": 8,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
        await connection.execute(
            sa.insert(objective).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_ids["mine"],
                name="Ship report",
                directive="Send the final report.",
                created_at=now,
                updated_at=now,
            )
        )
        finished_id, finished_step_id = uuid4(), uuid4()
        stale_open_id, stale_open_step_id = uuid4(), uuid4()
        old = now - timedelta(days=8)
        await connection.execute(
            sa.insert(objective),
            (
                {
                    "id": finished_id,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_ids["mine"],
                    "name": "Finished work",
                    "directive": "Do not repeat this.",
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": stale_open_id,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_ids["mine"],
                    "name": "Stale open work",
                    "directive": "Keep this visible.",
                    "created_at": old,
                    "updated_at": old,
                },
            ),
        )
        await connection.execute(
            sa.insert(objective_step),
            (
                {
                    "id": finished_step_id,
                    "workspace_id": workspace_id,
                    "objective_id": finished_id,
                    "position": 0,
                    "title": "Done",
                    "accepts": [],
                    "independent": False,
                    "created_at": old,
                },
                {
                    "id": stale_open_step_id,
                    "workspace_id": workspace_id,
                    "objective_id": stale_open_id,
                    "position": 0,
                    "title": "Open",
                    "accepts": [],
                    "independent": False,
                    "created_at": old,
                },
            ),
        )
        await connection.execute(
            sa.insert(objective_event).values(
                id=uuid4(),
                workspace_id=workspace_id,
                step_id=finished_step_id,
                kind="did",
                actor_turn_id=turn_ids["mine"],
                evidence="Complete.",
                created_at=old,
            )
        )
    with ws(workspace_id), agent(brief_id):
        blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
        await blob.put("artifacts/notes.txt", b"Private file text.")
        await blob.put("artifacts/invalid.txt", b"\x96")
        await blob.put("artifacts/compose.yaml", b"services: {}")
        await blob.put(f"pages/{page_id}", b"Granted page text.")
        await blob.put(f"pages/{invalid_page_id}", b"\x96")
        context = context_for(
            "report_digest",
            frozenset(),
            member_context_blob=blob,
            member_context_read=True,
            member_context_member_id=member_id,
        )
        records = await context.member_context(
            since=now - timedelta(days=1),
            exclude_conversation_id=conversation_ids["self"],
        )
    assert {record.title for record in records} == {
        "shared",
        "mine",
        "Task memory",
        "Ship report",
        "Stale open work",
        "notes.txt",
        "missing.txt",
        "invalid.txt",
        "compose.yaml",
        "Weekly report",
    }
    assert all("Other member task" not in record.text for record in records)
    assert next(record.text for record in records if record.title == "notes.txt") == (
        "Private file text."
    )
    assert next(record.text for record in records if record.title == "compose.yaml") == (
        "services: {}"
    )
    assert next(record.text for record in records if record.title == "Weekly report") == (
        "Granted page text."
    )
    assert next(record.text for record in records if record.title == "missing.txt") == (
        "Missing notes (text/plain)."
    )
    assert next(record.text for record in records if record.title == "invalid.txt") == (
        "Invalid text (text/plain)."
    )
    assert all(record.title != "Missing report" for record in records)
    assert all(record.title != "Invalid report" for record in records)


async def test_member_context_drops_a_memory_the_page_pass_retired(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, _, brief_id = await _seed()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item),
            (
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "subject": "shared",
                    "body": "Live shared fact.",
                    "body_digest": body_digest("Live shared fact."),
                    "item_class": "fact",
                    "memory_kind": "fact",
                    "confidence": 8,
                    "retired_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "subject": "shared",
                    "body": "Retired shared fact.",
                    "body_digest": body_digest("Retired shared fact."),
                    "item_class": "fact",
                    "memory_kind": "fact",
                    "confidence": 8,
                    "retired_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "subject": f"member:{member_id}",
                    "body": "Retired member task.",
                    "body_digest": body_digest("Retired member task."),
                    "item_class": "fact",
                    "memory_kind": "task",
                    "confidence": 8,
                    "retired_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    with ws(workspace_id), agent(brief_id):
        context = context_for(
            "report_digest",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            member_context_member_id=member_id,
        )
        records = await context.member_context(since=now - timedelta(days=1))
    assert [record.text for record in records] == ["Live shared fact."]
