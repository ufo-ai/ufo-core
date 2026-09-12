"""The `turn` object kind: the selected agent's turns as read-only objects, the runs of scheduled
work among them, fenced by the reader's own audiences."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import CORE_OBJECT_KINDS
from ufo.host.kinds.turns import TURN_OBJECT, TurnSpec, last_fires
from ufo.runtime.object_scope import ObjectAgent, object_agent
from ufo.runtime.objects import ObjectListQuery, VerbNotSupported
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, FiredBy, Turn
from ufo.sdk.audience import SHARED_AUDIENCE

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

FIRED = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
NIGHTLY = FiredBy(kind="scheduled_task", name="nightly-digest", title="nightly-digest")
WATCH = FiredBy(kind="source_trigger", name="github-acme-1a2b3c4d", title="acme/repo on github")


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError


def _query(**filters: str | bool) -> ObjectListQuery:
    return ObjectListQuery(
        filters=filters,
        order_by="created_at",
        order="desc",
        supported_fields=TURN_OBJECT.list_fields,
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
                surface_label="#ops",
                queue_key="slack/ops",
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


async def _seed_agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="Report.",
                model="auto",
                created_at=now,
                updated_at=now,
            )
        )
    return agent_id


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


async def _seed_turn(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    seq: int,
    at: datetime,
    status: str = "done",
    fired_by: FiredBy | None = None,
    speaker_member_id: UUID | None = None,
) -> UUID:
    turn_id = uuid4()
    terminal = (
        None
        if status in ("queued", "running", "parked")
        else {"status": status, "text": f"the turn ended {status}"}
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="said",
                admission_source=(
                    "member"
                    if speaker_member_id is not None
                    else "scheduled"
                    if fired_by is not None and fired_by.kind == "scheduled_task"
                    else "internal"
                ),
                speaker_member_id=speaker_member_id,
                fired_by_kind=None if fired_by is None else fired_by.kind,
                fired_by_name=None if fired_by is None else fired_by.name,
                fired_by_title=None if fired_by is None else fired_by.title,
                terminal=terminal,
                created_at=at,
                updated_at=at,
            )
        )
    return turn_id


def _context(workspace_id: UUID, speaker_id: UUID, agent_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=Path()),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="what ran overnight",
            created_at=datetime(2026, 8, 15, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="m"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_id,
        audience=conversation_audience(speaker_id),
        artifact_token_secret="",
    )


def test_the_kind_is_registered_beside_the_conversation_kind() -> None:
    assert TURN_OBJECT in {bound.kind for bound in CORE_OBJECT_KINDS}


async def test_fired_turns_list_newest_first_and_narrow_in_sql(db: None) -> None:
    workspace_id, member_id, agent_id, shared = await _seed_workspace()
    other_id = await _seed_member(workspace_id, "nadia@example.com")
    spoken = await _seed_turn(
        workspace_id, agent_id, shared, seq=1, at=FIRED, speaker_member_id=member_id
    )
    quiet = await _seed_turn(
        workspace_id, agent_id, shared, seq=2, at=FIRED + timedelta(hours=1), fired_by=NIGHTLY
    )
    woken = await _seed_turn(
        workspace_id,
        agent_id,
        shared,
        seq=3,
        at=FIRED + timedelta(hours=2),
        status="failed",
        fired_by=WATCH,
    )
    running = await _seed_turn(
        workspace_id,
        agent_id,
        shared,
        seq=4,
        at=FIRED + timedelta(hours=3),
        status="running",
        fired_by=NIGHTLY,
    )
    private = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(other_id)), member_id=other_id
    )
    theirs = await _seed_turn(
        workspace_id, agent_id, private, seq=1, at=FIRED + timedelta(hours=4), fired_by=NIGHTLY
    )

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        every = await TURN_OBJECT.store.member_page(
            None, member_id=member_id, admin=True, query=_query()
        )
        fired = await TURN_OBJECT.store.member_page(
            None, member_id=member_id, admin=False, query=_query(fired=True)
        )
        nightly = await TURN_OBJECT.store.member_page(
            None, member_id=member_id, admin=False, query=_query(source_name="nightly-digest")
        )
        live = await TURN_OBJECT.store.member_page(
            None, member_id=member_id, admin=False, query=_query(status="running")
        )
        others = await TURN_OBJECT.store.member_page(
            None, member_id=other_id, admin=False, query=_query(fired=True)
        )

    assert [row.name for row in every.rows] == [
        str(running),
        str(woken),
        str(quiet),
        str(spoken),
    ]
    assert [row.name for row in fired.rows] == [str(running), str(woken), str(quiet)]
    assert [row.name for row in nightly.rows] == [str(running), str(quiet)]
    assert [row.name for row in live.rows] == [str(running)]
    assert [row.name for row in others.rows] == [str(theirs), str(running), str(woken), str(quiet)]
    run = fired.rows[0]
    assert run.fields["status"] == "running"
    assert run.fields["text"] == ""
    assert run.fields["fired"] is True
    assert run.fields["source"] == "scheduled_task"
    assert run.fields["source_name"] == "nightly-digest"
    assert run.fields["title"] == "nightly-digest"
    assert run.fields["admission"] == "scheduled"
    assert run.fields["origin"] == "#ops"
    assert run.fields["surface"] == "slack"
    assert run.fields["conversation"] == str(shared)
    wake = fired.rows[1]
    assert wake.fields["text"] == "the turn ended failed"
    assert wake.fields["title"] == "acme/repo on github"
    assert wake.summary.startswith("acme/repo on github, failed, 2026-08-14 11:00")
    said = every.rows[3]
    assert said.fields["fired"] is False
    assert said.fields["source"] is None
    assert said.summary.startswith("member, done, 2026-08-14 09:00")


async def test_the_last_fire_of_each_name_reads_off_the_index_it_groups_on(db: None) -> None:
    """`last_fires` answers the newest fire of each named object of one kind, over the whole
    workspace and never one agent. It filters and groups on `fired_by_kind` and `fired_by_name`, so
    the plan walks `turn_fired_by` — the index that leads on those two — rather than scanning every
    fired turn the workspace holds."""
    workspace_id, _member_id, agent_id, shared = await _seed_workspace()
    await _seed_turn(workspace_id, agent_id, shared, seq=1, at=FIRED, fired_by=NIGHTLY)
    await _seed_turn(
        workspace_id, agent_id, shared, seq=2, at=FIRED + timedelta(hours=1), fired_by=WATCH
    )
    await _seed_turn(
        workspace_id, agent_id, shared, seq=3, at=FIRED + timedelta(hours=2), fired_by=WATCH
    )

    with ws(workspace_id):
        fires = await last_fires(WATCH.kind, (WATCH.name, NIGHTLY.name))
        async with workspace_tx() as connection:
            plan = (
                await connection.exec_driver_sql(
                    "explain query plan select fired_by_name, max(created_at) from turn "
                    "where workspace_id = ? and fired_by_kind = ? and fired_by_name in (?) "
                    "group by fired_by_name",
                    (str(workspace_id), WATCH.kind, WATCH.name),
                )
            ).all()

    assert set(fires) == {WATCH.name}
    assert fires[WATCH.name] == FIRED + timedelta(hours=2)
    assert any("turn_fired_by" in str(step) for step in plan)


async def test_a_turn_reads_whole_with_its_links_under_the_same_fence(db: None) -> None:
    workspace_id, member_id, agent_id, shared = await _seed_workspace()
    other_id = await _seed_member(workspace_id, "nadia@example.com")
    fired = await _seed_turn(workspace_id, agent_id, shared, seq=1, at=FIRED, fired_by=NIGHTLY)
    spoken = await _seed_turn(
        workspace_id,
        agent_id,
        shared,
        seq=2,
        at=FIRED + timedelta(hours=1),
        speaker_member_id=member_id,
    )
    private = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(other_id)), member_id=other_id
    )
    theirs = await _seed_turn(workspace_id, agent_id, private, seq=1, at=FIRED, fired_by=NIGHTLY)

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        run = await TURN_OBJECT.store.member_detail(
            None, str(fired), member_id=member_id, admin=False
        )
        said = await TURN_OBJECT.store.member_detail(
            None, str(spoken), member_id=member_id, admin=False
        )
        sealed = await TURN_OBJECT.store.member_detail(
            None, str(theirs), member_id=member_id, admin=True
        )
        unnamed = await TURN_OBJECT.store.member_detail(
            None, "not-a-turn", member_id=member_id, admin=False
        )

    assert run is not None
    assert run.detail.created_at == FIRED
    assert [(link.relation, link.target.kind, link.target.name) for link in run.detail.links] == [
        ("created_in", "conversation", str(shared)),
        ("created_from", "scheduled_task", "nightly-digest"),
    ]
    assert said is not None
    assert [link.relation for link in said.detail.links] == ["created_in"]
    assert sealed is None
    assert unnamed is None


async def test_a_turn_reads_its_own_conversation_and_the_requesters_but_no_further(
    db: None,
) -> None:
    workspace_id, member_id, agent_id, shared = await _seed_workspace()
    other_id = await _seed_member(workspace_id, "nadia@example.com")
    ours = await _seed_turn(workspace_id, agent_id, shared, seq=1, at=FIRED, fired_by=NIGHTLY)
    mine = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(member_id)), member_id=member_id
    )
    own = await _seed_turn(
        workspace_id, agent_id, mine, seq=1, at=FIRED + timedelta(hours=1), fired_by=NIGHTLY
    )
    private = await _seed_conversation(
        workspace_id, agent_id, audience=str(conversation_audience(other_id)), member_id=other_id
    )
    theirs = await _seed_turn(
        workspace_id, agent_id, private, seq=1, at=FIRED + timedelta(hours=2), fired_by=NIGHTLY
    )

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        ctx = _context(workspace_id, member_id, agent_id)
        page = await TURN_OBJECT.store.list(ctx, _query(fired=True))
        got = await TURN_OBJECT.store.get(ctx, str(theirs))

    assert [row.name for row in page.rows] == [str(own), str(ours)]
    assert got is None


async def test_a_turn_of_another_agent_is_not_read_through_this_one(db: None) -> None:
    """The wall the listing holds is the wall a detail holds: a run reached by id under one agent
    is that agent's run, so a member reading the drawer of a task fired on `analyst` cannot pull a
    turn of `courier` through it."""
    workspace_id, member_id, agent_id, _shared = await _seed_workspace()
    courier = await _seed_agent(workspace_id, "courier")
    theirs = await _seed_conversation(
        workspace_id, courier, audience=str(SHARED_AUDIENCE), member_id=None
    )
    elsewhere = await _seed_turn(workspace_id, courier, theirs, seq=1, at=FIRED, fired_by=NIGHTLY)

    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        crossed = await TURN_OBJECT.store.member_detail(
            None, str(elsewhere), member_id=member_id, admin=True
        )
        got = await TURN_OBJECT.store.get(
            _context(workspace_id, member_id, agent_id), str(elsewhere)
        )
    with ws(workspace_id), object_agent(ObjectAgent(id=courier, name="courier")):
        held = await TURN_OBJECT.store.member_detail(
            None, str(elsewhere), member_id=member_id, admin=True
        )

    assert crossed is None
    assert got is None
    assert held is not None


async def test_the_kind_refuses_every_mutation(db: None) -> None:
    workspace_id, member_id, agent_id, _shared = await _seed_workspace()
    with ws(workspace_id), object_agent(ObjectAgent(id=agent_id, name="analyst")):
        ctx = _context(workspace_id, member_id, agent_id)
        with pytest.raises(VerbNotSupported):
            await TURN_OBJECT.store.apply(ctx, "x", TurnSpec(), None, expected_generation=None)
        with pytest.raises(VerbNotSupported):
            await TURN_OBJECT.store.delete(ctx, "x", expected_generation=None)
        assert await TURN_OBJECT.store.status(ctx, "x", expected_generation=None) is None
