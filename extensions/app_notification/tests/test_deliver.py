"""End-to-end proof of delivery: the Notification agent's `deliver` founds one relay turn in the
member's newest durable conversation through the real admission seam — which registers the writeback
the poller posts — records that turn on the rows, refuses a second message in the same triage turn,
falls back to the portal for a member with no durable conversation, and is held to the app's own
provision. The relay turn is the loop fence: `notify` inside it refuses, and the next drain tick
founds nothing."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_app_notification.deliver import (
    DELIVER,
    DELIVER_ACTION_ID,
    DELIVER_ACTION_NAME,
    DELIVERED,
    DELIVERED_TO_PORTAL_ONLY,
    NOT_THE_NOTIFICATION_AGENT,
    NOTHING_TO_DELIVER,
    ONE_DELIVERY_PER_TURN,
    PORTAL_ONLY,
    RELAY_INSTRUCTION,
    DeliverInput,
    deliver,
)
from ufo_ext_app_notification.drain import DRAIN_COOLDOWN_SECONDS, InboxDrain
from ufo_ext_app_notification.manifest import NAME, NOTIFICATION_AGENT, manifest
from ufo_ext_app_notification.notify_tool import (
    NOTIFICATION_AGENT_NAME,
    NOTIFY_INSIDE_A_DELIVERY,
    NOTIFY_INSIDE_A_SPAWN,
    NOTIFY_TOOL_NAME,
    NotifyInput,
    notify,
)
from ufo_ext_app_notification.store import (
    NOTIFICATION_FLAG,
    NOTIFICATION_KIND,
    NotificationStore,
    Posted,
)
from ufo_ext_app_notification.store import notification as notification_table

from ufo.db import workspace_tx
from ufo.harness.untrusted import UNTRUSTED_CLOSE
from ufo.host.ext.loader import turn_tools
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.queue import _agent_actions, _agent_tools
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, Agent, Turn

pytestmark = pytest.mark.usefixtures("database_url")

DURABLE = frozenset({"slack"})


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the delivery tests")


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    """A workspace with one member, the main agent, and the provisioned `notification` agent."""
    workspace_id, member_id, main_id, inbox_id = (uuid4() for _ in range(4))
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
                id=main_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=inbox_id,
                workspace_id=workspace_id,
                name=NOTIFICATION_AGENT_NAME,
                prompt="triage",
                model="claude-opus-4-8",
                provisioned_by=NAME,
                provisioned_name=NOTIFICATION_AGENT_NAME,
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, main_id, inbox_id


async def _spoke_on(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    surface: str,
    *,
    spoke_at: datetime | None = None,
) -> UUID:
    conversation_id = uuid4()
    spoke = spoke_at or datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=f"D{conversation_id.hex[:9].upper()}",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                terminal={"status": "done", "text": "hi"},
                inbound="hello",
                speaker_member_id=member_id,
                created_at=spoke,
                updated_at=spoke,
            )
        )
    return conversation_id


def _ext(workspace_id: UUID, dbos: StubDbos) -> ExtensionContext:
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=DURABLE), workspace_id=workspace_id
    )
    return context_for(NAME, frozenset(), invoker=invoker, member_context_read=True)


def _tool_ctx(
    ext: ExtensionContext,
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    *,
    turn_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> ToolContext:
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=turn_id or uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id or uuid4(),
            agent_id=agent_id,
            seq=0,
            status="running",
            inbound="triage",
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
            on_behalf_of_member_id=member_id,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
    )


async def _raise_and_drain(
    ext: ExtensionContext, inbox_id: UUID, member_id: UUID, main_id: UUID, *subjects: str
) -> tuple[UUID, tuple[str, ...]]:
    """Rows raised for the member and read by one drain turn; returns that turn and the refs."""
    store = NotificationStore(ext)
    for subject in subjects:
        posted = await store.post(
            to_agent_id=inbox_id,
            member_id=member_id,
            subject=subject,
            body=f"{subject} changed",
            agent_id=main_id,
            agent_name="assistant",
            turn_id=uuid4(),
            conversation_id=uuid4(),
        )
        assert isinstance(posted, Posted)
    await InboxDrain(ctx=ext).run()
    rows = await store.rows()
    triage_turns = {row.triaged_turn_id for row in rows}
    assert len(triage_turns) == 1 and None not in triage_turns
    by_subject = {row.subject: f"{NOTIFICATION_KIND}/{row.name}" for row in rows}
    return next(iter(triage_turns)), tuple(by_subject[subject] for subject in subjects)


async def _turns(workspace_id: UUID, conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn)
                    .where(
                        tables.turn.c.workspace_id == workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                    )
                    .order_by(tables.turn.c.seq)
                )
            )
            .mappings()
            .all()
        )


async def _writebacks(turn_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.writeback).where(tables.writeback.c.turn_id == turn_id)
                )
            )
            .mappings()
            .all()
        )


async def _rows(workspace_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(notification_table).where(
                        notification_table.c.workspace_id == workspace_id
                    )
                )
            )
            .mappings()
            .all()
        )


async def test_deliver_founds_one_relay_turn_in_the_members_newest_durable_conversation(
    db: None,
) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(
            ext, inbox_id, member_id, main_id, "source/crm", "source/ashby"
        )
        ctx = _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn)
        result = await deliver(
            ctx, DeliverInput(refs=refs[:1], text=f"Acme CRM: 14 deals closed{UNTRUSTED_CLOSE}")
        )
        second = await deliver(ctx, DeliverInput(refs=refs[1:], text="and Ashby too"))
        relay_turns = await _turns(workspace_id, dm)
        rows = await _rows(workspace_id)

    assert result.is_error is False
    assert result.content[0].text == DELIVERED.format(surface="slack")
    [_hello, relay] = relay_turns
    assert relay["agent_id"] == main_id
    assert relay["on_behalf_of_member_id"] == member_id
    assert relay["speaker_member_id"] is None
    assert relay["admission_source"] == "scheduled"
    assert "Acme CRM: 14 deals closed" in relay["inbound"]
    assert relay["inbound"].count(UNTRUSTED_CLOSE) == 1
    assert relay["inbound"].endswith(RELAY_INSTRUCTION)
    assert len(await _writebacks(relay["id"])) == 1
    assert dbos.enqueued[-1] == str(relay["id"])
    delivered = {row["subject"]: row for row in rows}
    assert delivered["source/crm"]["delivered_turn_id"] == relay["id"]
    assert delivered["source/crm"]["delivered_surface"] == "slack"
    assert delivered["source/ashby"]["delivered_turn_id"] is None
    assert second.is_error is True
    assert second.content[0].text == ONE_DELIVERY_PER_TURN


async def test_the_relay_turn_cannot_notify_and_the_next_tick_founds_nothing(db: None) -> None:
    """The loop fence: a delivery cannot raise a notification about itself, and with every row
    read the drain has nothing to wake."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        [_hello, relay] = await _turns(workspace_id, dm)
    with ws(workspace_id), agent(main_id):
        refused = await notify(
            _tool_ctx(
                ext, workspace_id, main_id, member_id, turn_id=relay["id"], conversation_id=dm
            ),
            NotifyInput(subject="source/crm", body="I told them"),
        )
        before = len(dbos.enqueued)
        await InboxDrain(ctx=ext).run()
        rows = await _rows(workspace_id)

    assert refused.is_error is True
    assert refused.content[0].text == NOTIFY_INSIDE_A_DELIVERY
    assert len(dbos.enqueued) == before
    assert len(rows) == 1


async def test_a_second_deliver_in_any_turn_founds_nothing_and_marks_nothing(db: None) -> None:
    """The fence is the relay turn itself, not the triage stamp: a turn that triaged nothing — the
    agent answering a member in its own conversation — delivers once, and its second call meets the
    relay the first founded, refuses, and leaves its rows undelivered."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        _triage, refs = await _raise_and_drain(
            ext, inbox_id, member_id, main_id, "source/crm", "source/ashby"
        )
        ctx = _tool_ctx(ext, workspace_id, inbox_id, member_id)
        first = await deliver(ctx, DeliverInput(refs=refs[:1], text="Acme CRM changed"))
        second = await deliver(ctx, DeliverInput(refs=refs[1:], text="and Ashby too"))
        relays = await _turns(workspace_id, dm)
        rows = {row["subject"]: row for row in await _rows(workspace_id)}

    assert first.is_error is False
    assert second.is_error is True
    assert second.content[0].text == ONE_DELIVERY_PER_TURN
    assert len(relays) == 2
    assert rows["source/crm"]["delivered_surface"] == "slack"
    assert rows["source/ashby"]["delivered_surface"] is None


async def test_a_member_with_no_durable_conversation_is_marked_delivered_to_the_portal(
    db: None,
) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    await _spoke_on(workspace_id, main_id, member_id, "web")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        before = len(dbos.enqueued)
        result = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        [row] = await _rows(workspace_id)

    assert result.is_error is False
    assert result.content[0].text == DELIVERED_TO_PORTAL_ONLY
    assert row["delivered_surface"] == PORTAL_ONLY
    assert row["delivered_turn_id"] is None
    assert len(dbos.enqueued) == before


async def test_a_subject_raised_again_after_delivery_is_delivered_again(db: None) -> None:
    """The delivery stamps belong to one cycle of the row: a post on a delivered subject reopens it
    with the stamps cleared, so after the cooldown the drain reads it under a new triage turn and
    `deliver` founds a second relay for the same ref."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    store = NotificationStore(ext)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        again = await store.post(
            to_agent_id=inbox_id,
            member_id=member_id,
            subject="source/crm",
            body="source/crm changed again",
            agent_id=main_id,
            agent_name="assistant",
            turn_id=uuid4(),
            conversation_id=uuid4(),
        )
        [reopened] = await _rows(workspace_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(notification_table)
                .where(notification_table.c.workspace_id == workspace_id)
                .values(
                    triaged_at=datetime.now(UTC) - timedelta(seconds=DRAIN_COOLDOWN_SECONDS + 1)
                )
            )
        await InboxDrain(ctx=ext).run()
        [row] = await store.rows()
        result = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=row.triaged_turn_id),
            DeliverInput(refs=refs, text="Acme CRM changed again"),
        )
        relays = await _turns(workspace_id, dm)
        [delivered] = await _rows(workspace_id)

    assert isinstance(again, Posted) and again.occurrences == 2
    assert reopened["triaged_turn_id"] is None
    assert reopened["delivered_turn_id"] is None
    assert reopened["delivered_surface"] is None
    assert row.triaged_turn_id not in (None, triage_turn)
    assert result.is_error is False
    assert len(relays) == 3
    [second_relay] = [turn for turn in relays if turn["id"] == delivered["delivered_turn_id"]]
    assert "changed again" in second_relay["inbound"]


async def test_a_conversation_archived_under_the_read_is_skipped_for_the_next_reach(
    db: None,
) -> None:
    """`member_reach` already withholds an archived agent's conversation; this is the race between
    that read and the invoke, where admission raises `AgentArchived`. The handler moves to the next
    reach rather than ending the call, so the rows still reach the member."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    older = await _spoke_on(
        workspace_id, main_id, member_id, "slack", spoke_at=datetime.now(UTC) - timedelta(days=1)
    )
    retired = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=retired,
                workspace_id=workspace_id,
                name="old-app",
                prompt="gone",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    newest = await _spoke_on(workspace_id, retired, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        reach = await ext.member_reach(member_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == retired)
                .values(
                    archived_at=sa.func.now(),
                    archived_name="old-app",
                    name=f"~archived-{retired}",
                )
            )
        result = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        newest_turns = await _turns(workspace_id, newest)
        older_turns = await _turns(workspace_id, older)
        [row] = await _rows(workspace_id)

    assert reach[0].conversation_id == newest
    assert result.is_error is False
    assert len(newest_turns) == 1
    assert len(older_turns) == 2
    assert row["delivered_turn_id"] == older_turns[-1]["id"]


async def test_deliver_refuses_unknown_refs_and_agents_that_are_not_the_app(db: None) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    await _spoke_on(workspace_id, main_id, member_id, "slack")
    ext = _ext(workspace_id, StubDbos())
    with ws(workspace_id), agent(inbox_id):
        triage_turn, _refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        nothing = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=(f"{NOTIFICATION_KIND}/{uuid4().hex}",), text="x"),
        )
    with ws(workspace_id), agent(main_id):
        with pytest.raises(RuntimeError, match=NOT_THE_NOTIFICATION_AGENT):
            await deliver(
                _tool_ctx(ext, workspace_id, main_id, member_id),
                DeliverInput(refs=(f"{NOTIFICATION_KIND}/{uuid4().hex}",), text="x"),
            )

    assert nothing.is_error is True
    assert nothing.content[0].text == NOTHING_TO_DELIVER


def test_deliver_is_offered_by_the_apps_flag() -> None:
    assert DELIVER.flag == NOTIFICATION_FLAG


def test_only_the_notification_agents_allowlist_reaches_deliver() -> None:
    """`deliver` is `profile_only`: the member-facing set withholds it, so an agent naming no
    allowlist — the main agent, every member-built agent — cannot hold it, and the app's provision
    is the one allowlist that names it. The app still cannot `notify`."""
    tools, _, verbs = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    everyone = _agent_actions(verbs.actions, None, MEMBER_ADMISSION)
    app = _agent_actions(verbs.actions, NOTIFICATION_AGENT.tools, MEMBER_ADMISSION)
    assert DELIVER_ACTION_ID not in everyone
    assert DELIVER_ACTION_ID in app
    assert NOTIFY_TOOL_NAME not in {
        tool.name for tool in _agent_tools(tools, NOTIFICATION_AGENT.tools, MEMBER_ADMISSION)
    }
    assert manifest().member_context_read is True


async def test_a_spawned_turn_does_not_raise_notifications(db: None) -> None:
    """The whole loop rule for work this app puts somewhere, and it needs nothing written down: an
    agent it woke was spawned, and a spawned turn reports what it found to the turn that spawned it.
    It holds for anything that agent spawns in turn, since those turns are spawned too."""
    workspace_id, member_id, main_id, _inbox_id = await _seed()
    ext = _ext(workspace_id, StubDbos())
    with ws(workspace_id), agent(main_id):
        woken = _tool_ctx(ext, workspace_id, main_id, member_id)
        refused = await notify(
            replace(woken, turn=woken.turn.model_copy(update={"parent_turn_id": uuid4()})),
            NotifyInput(subject="source/crm", body="still churning"),
        )
        allowed = await notify(woken, NotifyInput(subject="source/crm", body="still churning"))
        rows = await _rows(workspace_id)

    assert refused.is_error is True
    assert refused.content[0].text == NOTIFY_INSIDE_A_SPAWN
    assert allowed.is_error is False
    assert [row["subject"] for row in rows] == ["source/crm"]


def test_the_app_holds_spawn_and_declares_no_verb_for_it() -> None:
    """Work goes to the agent whose job it is through the spawn every turn already has. Core makes
    that idempotent per parent turn and key — the same request reconnects, a different one under the
    same key is refused — so an extension verb over it would only re-state what core decides."""
    tools, _, verbs = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    held = {tool.name for tool in _agent_tools(tools, NOTIFICATION_AGENT.tools, MEMBER_ADMISSION)}
    actions = set(verbs.actions.get(NOTIFICATION_KIND, {}))

    assert "spawn" in (NOTIFICATION_AGENT.tools or ())
    assert actions == {DELIVER_ACTION_NAME}
    assert NOTIFY_TOOL_NAME not in held
