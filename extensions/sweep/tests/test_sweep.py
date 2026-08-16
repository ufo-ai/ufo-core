import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_memory.store import memory_item
from ufo_ext_sweep.manifest import (
    AGENT_NAME,
    AGENT_PROMPT,
    FINAL_MODEL,
    MAX_EDITION_ATTEMPTS,
    SCOUT_MODEL,
    Finding,
    MemberContextRecord,
    ScoutOutput,
    SweepInput,
    _changed_records,
    _finalize,
    _public_records,
    _sweep,
    _tick,
    edition,
    local_edition_date,
    manifest,
)

from ufo.agent_scope import agent
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import skill_registry, turn_hooks, turn_tools
from ufo.schema import tables
from ufo.schema.records import Agent as AgentRecord
from ufo.schema.records import Turn
from ufo.sdk.manifest import PreToolUse
from ufo.search import FetchedPage
from ufo.workspace import ws


def test_local_edition_date_uses_the_members_zone_and_dst_rules() -> None:
    assert local_edition_date(datetime(2026, 3, 8, 11, 59, tzinfo=UTC), "America/New_York") is None
    assert (
        str(local_edition_date(datetime(2026, 3, 8, 12, 0, tzinfo=UTC), "America/New_York"))
        == "2026-03-08"
    )
    assert str(local_edition_date(datetime(2026, 8, 14, 8, 0, tzinfo=UTC), "UTC")) == ("2026-08-14")


def test_changed_records_deduplicates_and_repeats_open_work_after_seven_days() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    task = MemberContextRecord(
        kind="task",
        ref="memory/1",
        title="Finish report",
        text="Finish it.",
        information_date=now,
        stable_subject_key="task:1",
    )
    page = task.model_copy(update={"kind": "page", "ref": "page/1", "stable_subject_key": "page:1"})
    records = _changed_records(
        (task, task, page),
        {"task:1": now - timedelta(days=7), "page:1": now - timedelta(days=7)},
        now,
    )
    assert records == (task,)


def test_manifest_pins_four_luna_scouts() -> None:
    declared = manifest()
    (provision,) = declared.agents
    assert provision.name == AGENT_NAME
    assert provision.spec.model == FINAL_MODEL
    assert provision.spec.prompt == AGENT_PROMPT
    assert provision.spec.reasoning == "high"
    assert not provision.spec.internet_access_allowed
    assert provision.spec.sandbox_size == "small"
    assert provision.tools == (
        "load_skill",
        "sweep_newspaper",
        "update_todo_list",
        "memory_update",
    )
    assert len(declared.subagents) == 4
    assert {profile.model for profile in declared.subagents} == {SCOUT_MODEL}
    assert all(profile.max_rounds == 1 for profile in declared.subagents)
    assert all(
        profile.input_model(records=()).preload_skills == ("daily-brief",)
        for profile in declared.subagents
    )
    assert declared.member_context_read
    assert [(hook.event, hook.tools) for hook in declared.hooks] == [
        ("pre_tool_use", ("update_todo_list", "memory_update"))
    ]

    skill = skill_registry((declared,)).named("daily-brief")
    assert skill.description.startswith("Load when")
    instructions = " ".join(skill.instructions.split())
    assert "Never call `update_todo_list` or `memory_update`" in instructions


def test_turn_context_wires_member_blobs_without_a_trajectory_corpus(tmp_path: Path) -> None:
    blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
    _tools, contexts = turn_tools(
        (manifest(),),
        None,
        audience=conversation_audience(uuid4()),
        member_context_blob=blob,
    )
    context = contexts["sweep_newspaper"]
    assert context.member_context_blob is blob
    assert context.corpus is None


async def test_public_collection_sends_only_literal_public_urls() -> None:
    class Provider:
        supports_fetch = True

        def __init__(self) -> None:
            self.requests: list[object] = []

        async def search(self, query: object) -> object:
            raise AssertionError(f"private search issued: {query}")

        async def fetch(self, request: object) -> FetchedPage:
            self.requests.append(request)
            return FetchedPage(url=request.url, text="Public result.")

    provider = Provider()
    private_term = "Project-Blackbird-floor-price"
    record = MemberContextRecord(
        kind="turn",
        ref="conversation/1",
        title="Private",
        text=(
            f"https://example.com/{private_term}?token=secret "
            f"http://planning.internal/{private_term} http://127.0.0.1/{private_term}"
        ),
        information_date=datetime.now(UTC),
        stable_subject_key="turn:1",
    )
    result = await _public_records(
        SimpleNamespace(search_provider=provider), (record,), datetime.now(UTC)
    )
    assert [request.url for request in provider.requests] == ["https://example.com/"]
    assert all(private_term not in request.url for request in provider.requests)
    assert result[0].text == "Public result."


async def test_sweep_rejects_a_scout_reference_outside_its_input(db: None, tmp_path: Path) -> None:
    workspace_id, member_id, agent_id, turn_id = await _seed()
    now = datetime.now(UTC)
    memory_ids = tuple(uuid4() for _ in range(30))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item),
            tuple(
                {
                    "id": memory_id,
                    "workspace_id": workspace_id,
                    "subject": "shared",
                    "body": f"Finish report {index}. " + "x" * 2_000,
                    "item_class": "fact",
                    "memory_kind": "task",
                    "confidence": 8,
                    "created_at": now,
                    "updated_at": now,
                }
                for index, memory_id in enumerate(memory_ids)
            ),
        )
    memory_refs = {f"memory/{memory_id}" for memory_id in memory_ids}

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        supplied = {record["ref"] for record in payload["records"]}
        references = (next(iter(memory_refs - supplied)),) if profile == "sweep-work" else ()
        finding = Finding(
            title=profile,
            why_it_matters="It needs attention.",
            information_date=datetime.now(UTC).date(),
            stable_subject_key=profile,
            references=references,
        )
        return SimpleNamespace(output=ScoutOutput(findings=(finding,), coverage="complete"))

    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        ctx = SimpleNamespace(
            ext=ext,
            acting_member_id=member_id,
            turn=SimpleNamespace(id=turn_id),
            search_provider=None,
            spawn=spawn,
        )
        result = await _sweep(ctx, SweepInput(user_description="Preparing the brief."))
        async with ext.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        edition.c.candidate_cursor,
                        edition.c.candidate_input_keys,
                    ).where(edition.c.turn_id == turn_id)
                )
            ).one()
    assert not result.is_error
    assert json.loads(result.content[0].text)["missing"] == ["work"]
    assert row.candidate_input_keys == []
    assert row.candidate_cursor.replace(tzinfo=UTC) < now - timedelta(days=6)


async def test_sweep_ledgers_only_bounded_scout_input_and_keeps_the_cursor_open(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, agent_id, turn_id = await _seed()
    now = datetime.now(UTC)
    memory_ids = tuple(uuid4() for _ in range(30))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item),
            tuple(
                {
                    "id": memory_id,
                    "workspace_id": workspace_id,
                    "subject": "shared",
                    "body": f"Finish report {index}. " + "x" * 2_000,
                    "item_class": "fact",
                    "memory_kind": "task",
                    "confidence": 8,
                    "created_at": now,
                    "updated_at": now,
                }
                for index, memory_id in enumerate(memory_ids)
            ),
        )
    supplied: set[str] = set()

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        if profile == "sweep-work":
            supplied.update(record["stable_subject_key"] for record in payload["records"])
        return SimpleNamespace(output=ScoutOutput(findings=(), coverage="complete"))

    with ws(workspace_id), agent(agent_id):
        blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=blob,
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        result = await _sweep(
            SimpleNamespace(
                ext=ext,
                acting_member_id=member_id,
                turn=SimpleNamespace(id=turn_id),
                search_provider=None,
                spawn=spawn,
            ),
            SweepInput(user_description="Preparing the brief."),
        )
        async with ext.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        edition.c.candidate_cursor,
                        edition.c.candidate_input_keys,
                    ).where(edition.c.turn_id == turn_id)
                )
            ).one()
    cursor = row.candidate_cursor.replace(tzinfo=UTC)
    assert not result.is_error
    assert 0 < len(supplied) < len(memory_ids)
    assert set(row.candidate_input_keys) == supplied
    assert cursor < now - timedelta(days=6)


async def _seed() -> tuple:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=AGENT_NAME,
                prompt="Brief.",
                model="claude-sonnet-5",
                provisioned_by="sweep",
                provisioned_name="daily-brief",
                provisioned_version="0.1.0",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="extension:sweep",
                queue_key=f"daily-brief:{member_id}:2026-08-14",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
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
                status="running",
                inbound="Prepare the brief.",
                admission_source="scheduled",
                on_behalf_of_member_id=member_id,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(edition).values(
                workspace_id=workspace_id,
                member_id=member_id,
                local_date="2026-08-14",
                timezone="UTC",
                status="pending",
                attempt=1,
                conversation_id=conversation_id,
                turn_id=turn_id,
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id, member_id, agent_id, turn_id


async def test_tick_reuses_a_pending_admission_key_and_advances_a_failed_attempt(db: None) -> None:
    workspace_id, member_id, _agent_id, _turn_id = await _seed()
    calls: list[str] = []
    turns: dict[str, UUID] = {}

    class Invoker:
        async def invoke(self, *args: object, **kwargs: object) -> UUID:
            key = str(args[3])
            calls.append(key)
            if key in turns:
                return turns[key]
            turn_id = uuid4()
            turns[key] = turn_id
            async with workspace_tx() as connection:
                seq = (
                    await connection.execute(
                        sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                            tables.turn.c.conversation_id == args[0]
                        )
                    )
                ).scalar_one()
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=args[0],
                        agent_id=args[1],
                        seq=seq,
                        status="queued",
                        inbound=args[2],
                        admission_source="scheduled",
                        on_behalf_of_member_id=member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            return turn_id

    with ws(workspace_id):
        ext = context_for("sweep", frozenset(), invoker=Invoker(), member_context_read=True)
        tick_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
        await _tick(ext, tick_at)
        local_date = tick_at.date().isoformat()
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.member_id == member_id, edition.c.local_date == local_date)
                .values(turn_id=None)
            )
        await _tick(ext, tick_at)
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.member_id == member_id, edition.c.local_date == local_date)
                .values(status="failed", turn_id=None)
            )
        await _tick(ext, tick_at)
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.member_id == member_id, edition.c.local_date == local_date)
                .values(status="failed", turn_id=None)
            )
        await _tick(ext, tick_at)
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.member_id == member_id, edition.c.local_date == local_date)
                .values(status="failed", turn_id=None)
            )
        await _tick(ext, tick_at)
    key = f"daily-brief:{member_id}:{local_date}"
    assert calls == [f"{key}:1", f"{key}:1", f"{key}:2", f"{key}:3"]
    assert len({call for call in calls if call.endswith(f":{MAX_EDITION_ATTEMPTS}")}) == 1


async def test_only_the_scheduled_edition_turn_is_refused_mutation_tools(db: None) -> None:
    workspace_id, member_id, agent_id, turn_id = await _seed()
    with ws(workspace_id):
        chain = turn_hooks(
            (manifest(),),
            CredentialStore(Fernet(Fernet.generate_key())),
            audience=conversation_audience(member_id),
        )
        scheduled = Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="Prepare the brief.",
            admission_source="scheduled",
            on_behalf_of_member_id=member_id,
            created_at=datetime.now(UTC),
        )
        ordinary = scheduled.model_copy(update={"id": uuid4()})
        agent_record = AgentRecord(prompt="Brief.", model=FINAL_MODEL)
        for tool_name in ("update_todo_list", "memory_update"):
            refused = await chain.fire(
                "pre_tool_use",
                PreToolUse(
                    tool_name=tool_name,
                    tool_input=SweepInput(user_description="Saving a draft."),
                ),
                scheduled,
                agent_record,
                member_id,
            )
            assert refused.denied is not None
        approved = await chain.fire(
            "pre_tool_use",
            PreToolUse(
                tool_name="memory_update",
                tool_input=SweepInput(user_description="Saving an approved memory."),
            ),
            ordinary,
            agent_record,
            member_id,
        )
    assert approved.denied is None


async def test_one_member_admission_refusal_does_not_stop_later_members(db: None) -> None:
    workspace_id, _member_id, _agent_id, _turn_id = await _seed()
    second_member = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member,
                workspace_id=workspace_id,
                email="second@example.com",
                timezone="UTC",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    class Invoker:
        def __init__(self) -> None:
            self.members: list[UUID] = []

        async def invoke(self, *args: object, **kwargs: object) -> UUID:
            member_id = kwargs["on_behalf_of_member_id"]
            assert isinstance(member_id, UUID)
            self.members.append(member_id)
            if len(self.members) == 1:
                raise PermissionError("seat changed")
            turn_id = uuid4()
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=args[0],
                        agent_id=args[1],
                        seq=1,
                        status="queued",
                        inbound=args[2],
                        admission_source="scheduled",
                        on_behalf_of_member_id=member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            return turn_id

    invoker = Invoker()
    with ws(workspace_id):
        tick_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
        await _tick(
            context_for("sweep", frozenset(), invoker=invoker, member_context_read=True),
            tick_at,
        )
        async with workspace_tx() as connection:
            states = (
                await connection.execute(
                    sa.select(edition.c.member_id, edition.c.status).where(
                        edition.c.local_date == tick_at.date().isoformat()
                    )
                )
            ).all()
    assert len(invoker.members) == 2
    assert {row.status for row in states} == {"failed", "pending"}


async def test_sweep_continues_with_one_failed_scout_and_uses_stable_child_keys(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, agent_id, turn_id = await _seed()
    calls: list[tuple[str, str | None]] = []

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        calls.append((profile, kwargs.get("dedup_key")))
        if profile == "sweep-public-context":
            raise RuntimeError("provider unavailable")
        finding = Finding(
            title=profile,
            why_it_matters="It needs attention.",
            information_date=datetime.now(UTC).date(),
            stable_subject_key=profile,
            references=(),
        )
        return SimpleNamespace(output=ScoutOutput(findings=(finding,), coverage="complete"))

    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        ctx = SimpleNamespace(
            ext=ext,
            acting_member_id=member_id,
            turn=SimpleNamespace(id=turn_id),
            search_provider=None,
            spawn=spawn,
        )
        result = await _sweep(ctx, SweepInput(user_description="Preparing the brief."))
        async with ext.transaction() as connection:
            stored = (
                await connection.execute(
                    sa.select(edition.c.candidate_cursor).where(edition.c.turn_id == turn_id)
                )
            ).scalar_one()
    payload = json.loads(result.content[0].text)
    assert not result.is_error
    assert payload["missing"] == ["public-context"]
    assert stored is not None
    assert {key for _profile, key in calls} == {
        f"daily-brief:{member_id}:2026-08-14/{name}"
        for name in ("work", "missed-items", "pages-artifacts", "public-context")
    }


async def test_finalizer_commits_only_a_done_answer_with_candidates(db: None) -> None:
    workspace_id, member_id, _agent_id, turn_id = await _seed()
    now = datetime.now(UTC)
    with ws(workspace_id):
        ext = context_for("sweep", frozenset(), member_context_read=True)
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.turn_id == turn_id)
                .values(candidate_cursor=now, candidate_finding_keys=["one"])
            )
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == turn_id)
                .values(status="done", terminal={"status": "done", "text": "Brief."})
            )
        await _finalize(ext, now)
        async with ext.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(edition.c.status, edition.c.completed_at).where(
                        edition.c.member_id == member_id
                    )
                )
            ).one()
    assert row.status == "completed"
    assert row.completed_at is not None


async def test_sweep_fails_when_fewer_than_three_scouts_finish(db: None, tmp_path: Path) -> None:
    workspace_id, member_id, agent_id, turn_id = await _seed()

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        if profile in {"sweep-work", "sweep-missed-items"}:
            raise RuntimeError("model unavailable")
        return SimpleNamespace(output=ScoutOutput(findings=(), coverage="complete"))

    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        ctx = SimpleNamespace(
            ext=ext,
            acting_member_id=member_id,
            turn=SimpleNamespace(id=turn_id),
            search_provider=None,
            spawn=spawn,
        )
        result = await _sweep(ctx, SweepInput(user_description="Preparing the brief."))
        async with ext.transaction() as connection:
            cursor = (
                await connection.execute(
                    sa.select(edition.c.candidate_cursor).where(edition.c.turn_id == turn_id)
                )
            ).scalar_one()
    assert result.is_error
    assert "missed-items" in result.content[0].text
    assert cursor is None
