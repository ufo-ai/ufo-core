from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_memory.store import memory_item
from ufo_ext_objectives.store import objective, objective_event, objective_step

from ufo.agent_scope import agent
from ufo.audience import conversation_audience, foreign_room_audience
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import _member_blob_text, context_for
from ufo.schema import tables
from ufo.workspace import ws


@dataclass
class Invoker:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        **kwargs: object,
    ) -> UUID:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "message": message,
                "idempotency_key": idempotency_key,
                **kwargs,
            }
        )
        return uuid4()


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, other_member_id = uuid4(), uuid4(), uuid4()
    main_id, conflicting_id, sweep_id = uuid4(), uuid4(), uuid4()
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
                    "id": conflicting_id,
                    "workspace_id": workspace_id,
                    "name": "daily-brief",
                    "prompt": "Member agent.",
                    "model": "auto",
                    "is_main": False,
                    "provisioned_by": None,
                    "provisioned_name": None,
                    "provisioned_version": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": sweep_id,
                    "workspace_id": workspace_id,
                    "name": "daily-brief-sweep",
                    "prompt": "Brief.",
                    "model": "claude-sonnet-5",
                    "is_main": False,
                    "provisioned_by": "sweep",
                    "provisioned_name": "daily-brief",
                    "provisioned_version": "0.1.0",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    return workspace_id, member_id, other_member_id, sweep_id


async def test_member_blob_read_closes_a_bounded_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


async def test_seated_members_are_paged_with_utc_fallback(db: None) -> None:
    workspace_id, _member_id, _other_member_id, _sweep_id = await _seed()
    with ws(workspace_id):
        context = context_for("sweep", frozenset(), member_context_read=True)
        first = await context.seated_members(limit=1)
        second = await context.seated_members(cursor=first.next_cursor, limit=1)
    assert len(first.members) == len(second.members) == 1
    assert {first.members[0].timezone, second.members[0].timezone} == {
        "America/New_York",
        "UTC",
    }
    assert second.next_cursor is None


async def test_scheduled_member_turn_uses_a_private_stable_conversation(db: None) -> None:
    workspace_id, member_id, _other_member_id, sweep_id = await _seed()
    invoker = Invoker()
    with ws(workspace_id):
        context = context_for("sweep", frozenset(), invoker=invoker, member_context_read=True)
        result = await context.invoke_agent_for_member(
            agent_name="daily-brief",
            member_id=member_id,
            conversation_key=f"daily-brief:{member_id}:2026-08-14",
            message="Call sweep_newspaper once.",
            idempotency_key=f"daily-brief:{member_id}:2026-08-14",
        )
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation).where(
                        tables.conversation.c.id == result.conversation_id
                    )
                )
            ).one()
    assert row.agent_id == sweep_id
    assert row.member_id == member_id
    assert row.audience == str(conversation_audience(member_id))
    assert invoker.calls[0]["on_behalf_of_member_id"] == member_id
    assert invoker.calls[0]["as_scheduled"] is True


async def test_scheduled_member_turn_refuses_a_foreign_conversation_key(db: None) -> None:
    workspace_id, member_id, other_member_id, _sweep_id = await _seed()
    invoker = Invoker()
    with ws(workspace_id):
        context = context_for("sweep", frozenset(), invoker=invoker, member_context_read=True)
        await context.invoke_agent_for_member(
            agent_name="daily-brief",
            member_id=member_id,
            conversation_key="shared-key",
            message="Prepare the brief.",
            idempotency_key="first",
        )
        try:
            await context.invoke_agent_for_member(
                agent_name="daily-brief",
                member_id=other_member_id,
                conversation_key="shared-key",
                message="Prepare the brief.",
                idempotency_key="second",
            )
        except PermissionError as error:
            assert str(error) == "scheduled conversation belongs to another principal"
        else:
            raise AssertionError("foreign conversation key was accepted")
    assert len(invoker.calls) == 1


async def test_member_context_excludes_foreign_other_member_and_sweep_data(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, other_member_id, sweep_id = await _seed()
    now = datetime.now(UTC)
    source_id, page_id, missing_page_id, invalid_page_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                sweep_id,
                "extension:sweep",
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
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="probe",
                config={},
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=main_id,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page),
            (
                {
                    "id": page_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
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
                    "id": missing_page_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
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
                    "id": invalid_page_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
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
    with ws(workspace_id), agent(sweep_id):
        blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
        await blob.put("artifacts/notes.txt", b"Private file text.")
        await blob.put("artifacts/invalid.txt", b"\x96")
        await blob.put(f"pages/{page_id}", b"Granted page text.")
        await blob.put(f"pages/{invalid_page_id}", b"\x96")
        context = context_for(
            "sweep",
            frozenset(),
            member_context_blob=blob,
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        records = await context.member_context(since=now - timedelta(days=1))
    assert {record.title for record in records} == {
        "shared",
        "mine",
        "Task memory",
        "Ship report",
        "Stale open work",
        "notes.txt",
        "missing.txt",
        "invalid.txt",
        "Weekly report",
    }
    assert all("Other member task" not in record.text for record in records)
    assert next(record.text for record in records if record.title == "notes.txt") == (
        "Private file text."
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
