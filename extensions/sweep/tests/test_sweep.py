import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_memory.store import memory_item
from ufo_ext_sweep.manifest import (
    CONFIGURE_TOOL_NAME,
    HOMEPAGE_TOOL_NAME,
    MAX_COVERAGE_CHARS,
    REFERENCE_COVERAGE,
    SCOUT_MODEL,
    ConfigureDailyBriefInput,
    Finding,
    MemberContextRecord,
    ScoutOutput,
    SweepInput,
    _changed_records,
    _configure_daily_brief,
    _finalize,
    _public_records,
    _sweep,
    application,
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
    assert str(local_edition_date(datetime(2026, 3, 8, 4, 59, tzinfo=UTC), "America/New_York")) == (
        "2026-03-07"
    )
    assert str(local_edition_date(datetime(2026, 3, 8, 5, 0, tzinfo=UTC), "America/New_York")) == (
        "2026-03-08"
    )


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
    assert declared.agents == ()
    assert declared.jobs == ()
    assert len(declared.subagents) == 4
    assert {profile.model for profile in declared.subagents} == {SCOUT_MODEL}
    assert all(profile.max_rounds == 1 for profile in declared.subagents)
    assert all(
        profile.input_model(records=()).preload_skills == ("daily-brief",)
        for profile in declared.subagents
    )
    assert declared.member_context_read
    assert [tool.name for tool in declared.tools] == [CONFIGURE_TOOL_NAME, "sweep_newspaper"]
    assert declared.tools[0].side_effecting
    assert [(hook.event, hook.tools) for hook in declared.hooks] == [
        ("pre_tool_use", ("update_todo_list", "memory_update", "set_homepage"))
    ]

    skill = skill_registry((declared,)).named("daily-brief")
    assert skill.description.startswith("Load when")
    instructions = " ".join(skill.instructions.split())
    assert "Never call `update_todo_list` or `memory_update`" in instructions
    assert "write" in instructions
    assert "share_file" in instructions
    assert "`configure_daily_brief`" in instructions


def test_turn_context_wires_member_blobs_without_a_trajectory_corpus(tmp_path: Path) -> None:
    blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
    _tools, contexts = turn_tools(
        (manifest(),),
        None,
        audience=conversation_audience(uuid4()),
        member_context_blob=blob,
    )
    assert set(contexts) >= {"configure_daily_brief", "sweep_newspaper"}
    for context in contexts.values():
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


async def test_sweep_removes_a_scout_reference_outside_its_input(db: None, tmp_path: Path) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
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
    supplied_by_profile: dict[str, set[str]] = {}

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        supplied = {record["ref"] for record in payload["records"]}
        supplied_by_profile[profile] = supplied
        references = (
            (next(iter(supplied)), next(iter(memory_refs - supplied)))
            if profile == "profile:sweep-work"
            else ()
        )
        findings = (
            Finding(
                title=profile,
                why_it_matters="It needs attention.",
                information_date=datetime.now(UTC).date(),
                stable_subject_key=profile,
                references=references,
            ),
            Finding(
                title="Unsupported",
                why_it_matters="It needs attention.",
                information_date=datetime.now(UTC).date(),
                stable_subject_key="unsupported",
                references=(next(iter(memory_refs - supplied)),),
            ),
        )
        return SimpleNamespace(
            output=ScoutOutput(
                findings=findings if profile == "profile:sweep-work" else findings[:1],
                coverage="x" * MAX_COVERAGE_CHARS
                if profile == "profile:sweep-work"
                else "complete",
            )
        )

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
            audience=conversation_audience(member_id),
            turn=SimpleNamespace(
                id=turn_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                admission_source="scheduled",
            ),
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
                        edition.c.candidate_finding_keys,
                    ).where(edition.c.turn_id == turn_id)
                )
            ).one()
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == turn_id)
                .values(status="done", terminal={"status": "done", "text": "Brief."})
            )
        await _finalize(ext, now)
        second_turn_id = uuid4()
        async with ext.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(edition.c.turn_id == turn_id)
                .values(local_date=(datetime.now(UTC).date() - timedelta(days=1)).isoformat())
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=second_turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=2,
                    status="running",
                    inbound="Prepare the brief.",
                    admission_source="scheduled",
                    on_behalf_of_member_id=member_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        repeated = await _sweep(
            SimpleNamespace(
                ext=ext,
                acting_member_id=member_id,
                audience=conversation_audience(member_id),
                turn=SimpleNamespace(
                    id=second_turn_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    admission_source="scheduled",
                ),
                search_provider=None,
                spawn=spawn,
            ),
            SweepInput(user_description="Preparing the brief."),
        )
    payload = json.loads(result.content[0].text)
    assert not result.is_error
    assert payload["missing"] == []
    work = next(finding for finding in payload["findings"] if finding["section"] == "work")
    assert len(work["references"]) == 1
    assert work["references"][0] in supplied_by_profile["profile:sweep-work"]
    assert all(
        f"conversation/{conversation_id}" not in supplied
        for supplied in supplied_by_profile.values()
    )
    unsupported = next(
        finding for finding in payload["findings"] if finding["stable_subject_key"] == "unsupported"
    )
    assert unsupported["references"] == []
    assert REFERENCE_COVERAGE in payload["coverage"]["work"]
    assert len(payload["coverage"]["work"]) == MAX_COVERAGE_CHARS
    assert row.candidate_input_keys == []
    assert {"profile:sweep-work", "unsupported"} <= set(row.candidate_finding_keys)
    assert row.candidate_cursor.replace(tzinfo=UTC) < now - timedelta(days=6)
    assert json.loads(repeated.content[0].text)["findings"] == []


async def test_sweep_ledgers_only_bounded_scout_input_and_keeps_the_cursor_open(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
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
        if profile == "profile:sweep-work":
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
                audience=conversation_audience(member_id),
                turn=SimpleNamespace(
                    id=turn_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    admission_source="scheduled",
                ),
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


async def _seed(*, registered: bool = True) -> tuple[UUID, UUID, UUID, UUID, UUID]:
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
                name="daily-brief",
                prompt="Brief.",
                model="auto",
                visibility="private",
                owner_member_id=member_id,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"daily-brief:{member_id}",
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
        if registered:
            await connection.execute(
                sa.insert(application).values(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    member_id=member_id,
                    agent_id=agent_id,
                    created_at=now,
                    updated_at=now,
                )
            )
    return workspace_id, member_id, agent_id, conversation_id, turn_id


async def _insert_edition(workspace_id: UUID, member_id: UUID, turn_id: UUID) -> None:
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(edition).values(
                workspace_id=workspace_id,
                member_id=member_id,
                local_date=now.date().isoformat(),
                timezone="UTC",
                status="pending",
                turn_id=turn_id,
                created_at=now,
                updated_at=now,
            )
        )


async def test_owner_registers_one_private_daily_brief_conversation(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed(registered=False)
    with ws(workspace_id), agent(agent_id):
        ext = context_for("sweep", frozenset(), member_context_read=True)
        ctx = SimpleNamespace(
            ext=ext,
            speaker_member_id=member_id,
            audience=conversation_audience(member_id),
            turn=SimpleNamespace(
                id=turn_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                admission_source="member",
            ),
        )
        first = await _configure_daily_brief(
            ctx, ConfigureDailyBriefInput(user_description="Configure this Daily Brief.")
        )
        second = await _configure_daily_brief(
            ctx, ConfigureDailyBriefInput(user_description="Configure this Daily Brief.")
        )
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        application.c.conversation_id,
                        application.c.member_id,
                        application.c.agent_id,
                    ).where(application.c.workspace_id == workspace_id)
                )
            ).all()
    assert first.content[0].text == "Daily Brief configured."
    assert second.content[0].text == "Daily Brief configured."
    assert rows == [(conversation_id, member_id, agent_id)]


async def test_daily_brief_registration_refuses_a_shared_conversation(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed(registered=False)
    with ws(workspace_id), agent(agent_id):
        ext = context_for("sweep", frozenset(), member_context_read=True)
        with pytest.raises(RuntimeError, match="owner's private member turn"):
            await _configure_daily_brief(
                SimpleNamespace(
                    ext=ext,
                    speaker_member_id=member_id,
                    audience="shared",
                    turn=SimpleNamespace(
                        id=turn_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        admission_source="member",
                    ),
                ),
                ConfigureDailyBriefInput(user_description="Configure this Daily Brief."),
            )
        async with ext.transaction() as connection:
            rows = (await connection.execute(sa.select(application.c.agent_id))).all()
    assert rows == []


async def test_daily_brief_registration_refuses_a_workspace_application(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed(registered=False)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == agent_id)
            .values(visibility="workspace")
        )
    with ws(workspace_id), agent(agent_id):
        ext = context_for("sweep", frozenset(), member_context_read=True)
        with pytest.raises(RuntimeError, match="member-owned private application"):
            await _configure_daily_brief(
                SimpleNamespace(
                    ext=ext,
                    speaker_member_id=member_id,
                    audience=conversation_audience(member_id),
                    turn=SimpleNamespace(
                        id=turn_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        admission_source="member",
                    ),
                ),
                ConfigureDailyBriefInput(user_description="Configure this Daily Brief."),
            )


async def test_sweep_refuses_a_shared_scheduled_conversation(db: None, tmp_path: Path) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        with pytest.raises(RuntimeError, match="registered private scheduled Daily Brief turn"):
            await _sweep(
                SimpleNamespace(
                    ext=ext,
                    acting_member_id=member_id,
                    audience="shared",
                    turn=SimpleNamespace(
                        id=turn_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        admission_source="scheduled",
                    ),
                ),
                SweepInput(user_description="Preparing the brief."),
            )
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(edition.c.turn_id).where(edition.c.workspace_id == workspace_id)
                )
            ).all()
    assert rows == []


async def test_sweep_refuses_an_unregistered_private_application(db: None, tmp_path: Path) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed(registered=False)
    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
            scheduled_member_id=member_id,
        )
        with pytest.raises(RuntimeError, match="registered private scheduled Daily Brief turn"):
            await _sweep(
                SimpleNamespace(
                    ext=ext,
                    acting_member_id=member_id,
                    audience=conversation_audience(member_id),
                    turn=SimpleNamespace(
                        id=turn_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        admission_source="scheduled",
                    ),
                ),
                SweepInput(user_description="Preparing the brief."),
            )


async def test_sweep_refuses_a_member_turn_without_registering_an_edition(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        ext = context_for(
            "sweep",
            frozenset(),
            member_context_blob=WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
            member_context_read=True,
        )
        with pytest.raises(RuntimeError, match="registered private scheduled Daily Brief turn"):
            await _sweep(
                SimpleNamespace(
                    ext=ext,
                    acting_member_id=member_id,
                    audience=conversation_audience(member_id),
                    turn=SimpleNamespace(
                        id=turn_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        admission_source="member",
                    ),
                ),
                SweepInput(user_description="Preparing the brief."),
            )
        async with ext.transaction() as connection:
            rows = (await connection.execute(sa.select(edition.c.turn_id))).all()
    assert rows == []


async def test_registered_scheduled_turn_is_draft_only_before_collection(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
    with ws(workspace_id):
        chain = turn_hooks(
            (manifest(),),
            CredentialStore(Fernet(Fernet.generate_key())),
            audience=conversation_audience(member_id),
        )
        scheduled = Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="Prepare the brief.",
            admission_source="scheduled",
            on_behalf_of_member_id=member_id,
            created_at=datetime.now(UTC),
        )
        member_facing = scheduled.model_copy(
            update={
                "id": uuid4(),
                "admission_source": "member",
                "speaker_member_id": member_id,
            }
        )
        unrelated = scheduled.model_copy(
            update={
                "id": uuid4(),
                "conversation_id": uuid4(),
                "agent_id": uuid4(),
            }
        )
        agent_record = AgentRecord(prompt="Brief.", model="auto")
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
            member_facing,
            agent_record,
            member_id,
        )
        unaffected = await chain.fire(
            "pre_tool_use",
            PreToolUse(
                tool_name="memory_update",
                tool_input=SweepInput(user_description="Saving another application's memory."),
            ),
            unrelated,
            agent_record,
            member_id,
        )
    assert approved.denied is None
    assert unaffected.denied is None


async def test_the_scheduled_edition_cannot_bind_a_homepage(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
    with ws(workspace_id):
        chain = turn_hooks(
            (manifest(),),
            CredentialStore(Fernet(Fernet.generate_key())),
            audience=conversation_audience(member_id),
        )
        scheduled = Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="Prepare the brief.",
            admission_source="scheduled",
            on_behalf_of_member_id=member_id,
            created_at=datetime.now(UTC),
        )
        member_facing = scheduled.model_copy(
            update={
                "id": uuid4(),
                "admission_source": "member",
                "inbound": "Make the brief page your homepage.",
                "speaker_member_id": member_id,
            }
        )
        agent_record = AgentRecord(prompt="Brief.", model="auto")
        call = PreToolUse(
            tool_name=HOMEPAGE_TOOL_NAME,
            tool_input=SweepInput(user_description="Binding the brief page."),
        )
        refused = await chain.fire("pre_tool_use", call, scheduled, agent_record, member_id)
        allowed = await chain.fire("pre_tool_use", call, member_facing, agent_record, member_id)
    assert refused.denied is not None and "member-facing turn" in refused.denied
    assert allowed.denied is None


async def test_sweep_continues_with_one_failed_scout_and_uses_stable_child_keys(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()
    calls: list[tuple[str, str | None]] = []

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        calls.append((profile, kwargs.get("dedup_key")))
        if profile == "profile:sweep-public-context":
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
            audience=conversation_audience(member_id),
            turn=SimpleNamespace(
                id=turn_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                admission_source="scheduled",
            ),
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
    local_date = datetime.now(UTC).date().isoformat()
    assert {key for _profile, key in calls} == {
        f"daily-brief:{member_id}:{local_date}/{name}"
        for name in ("work", "missed-items", "pages-artifacts", "public-context")
    }


async def test_finalizer_commits_only_a_done_answer_with_candidates(db: None) -> None:
    workspace_id, member_id, _agent_id, _conversation_id, turn_id = await _seed()
    await _insert_edition(workspace_id, member_id, turn_id)
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
    workspace_id, member_id, agent_id, conversation_id, turn_id = await _seed()

    async def spawn(profile: str, payload: dict, **kwargs: object) -> SimpleNamespace:
        if profile in {"profile:sweep-work", "profile:sweep-missed-items"}:
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
            audience=conversation_audience(member_id),
            turn=SimpleNamespace(
                id=turn_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                admission_source="scheduled",
            ),
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
