"""The reach projection: where an invoke reaches a member who is not in the invoking conversation.
Only a durable-surface chat addressed to that member alone — their own audience — in which they
personally spoke is returned, in the surface order the caller names and newest first within one
surface; a shared channel thread, a live surface, another member's room, a private channel, and a
conversation they never spoke in are all absent."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet
from ufo_testsupport.invoker import RecordingInvoker

from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import context_for
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    room_audience,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("database_url")

DURABLE = frozenset({"slack", "imessage"})


class _NoDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("member_reach admits nothing")


async def _seed() -> tuple[UUID, UUID, UUID]:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
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
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, agent_id


async def _member(workspace_id: UUID) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _conversation(
    workspace_id: UUID,
    agent_id: UUID,
    surface: str,
    owner: UUID | None,
    *,
    spoken_by: UUID | None,
    spoke_at: datetime,
    audience: Audience | None = None,
) -> UUID:
    """A conversation on `surface` with `owner`'s private audience (shared when None, or the
    `audience` named), holding one turn `spoken_by` spoke at `spoke_at`, or a speakerless one when
    None."""
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=conversation_id.hex,
                member_id=owner,
                audience=str(audience or conversation_audience(owner)),
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
                speaker_member_id=spoken_by,
                created_at=spoke_at,
                updated_at=spoke_at,
            )
        )
    return conversation_id


async def test_member_reach_is_the_members_own_chats_in_the_order_the_caller_names(
    db: None,
) -> None:
    """Reach is a chat addressed to this member alone, taken in the caller's surface order: the
    Slack DM comes first because the caller named Slack first, not because of when anybody spoke,
    and the shared channel thread the member spoke in an hour ago is no reach at all. A room they
    spoke in, another member's DM, a live surface, and a chat of their own they never spoke in are
    all absent."""
    workspace_id, member_id, agent_id = await _seed()
    other = await _member(workspace_id)
    now = datetime.now(UTC)
    older_slack = await _conversation(
        workspace_id,
        agent_id,
        "slack",
        member_id,
        spoken_by=member_id,
        spoke_at=now - timedelta(days=2),
    )
    newer_imessage = await _conversation(
        workspace_id,
        agent_id,
        "imessage",
        member_id,
        spoken_by=member_id,
        spoke_at=now - timedelta(hours=1),
    )
    older_imessage = await _conversation(
        workspace_id,
        agent_id,
        "imessage",
        member_id,
        spoken_by=member_id,
        spoke_at=now - timedelta(days=3),
    )
    await _conversation(
        workspace_id,
        agent_id,
        "slack",
        None,
        spoken_by=member_id,
        spoke_at=now - timedelta(hours=1),
    )
    await _conversation(workspace_id, agent_id, "web", member_id, spoken_by=member_id, spoke_at=now)
    await _conversation(workspace_id, agent_id, "slack", other, spoken_by=other, spoke_at=now)
    await _conversation(workspace_id, agent_id, "slack", None, spoken_by=other, spoke_at=now)
    await _conversation(
        workspace_id,
        agent_id,
        "slack",
        None,
        spoken_by=member_id,
        spoke_at=now,
        audience=room_audience("slack", "C1"),
    )
    await _conversation(workspace_id, agent_id, "slack", member_id, spoken_by=None, spoke_at=now)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=_NoDbos(), durable_surfaces=DURABLE), workspace_id=workspace_id
    )
    with ws(workspace_id):
        reach = await context_for(
            "sample", frozenset(), invoker=invoker, member_context_read=True
        ).member_reach(member_id, ("slack", "imessage"))
        imessage_first = await invoker.member_reach(member_id, ("imessage", "slack"), 4)
        one = await invoker.member_reach(member_id, ("slack", "imessage"), 1)
        slack_alone = await invoker.member_reach(member_id, ("slack",), 4)
        unreachable = await invoker.member_reach(member_id, ("web",), 4)
        nobody = await invoker.member_reach(uuid4(), ("slack", "imessage"), 4)

    assert [(hit.surface, hit.conversation_id) for hit in reach] == [
        ("slack", older_slack),
        ("imessage", newer_imessage),
        ("imessage", older_imessage),
    ]
    assert all(hit.agent_id == agent_id for hit in reach)
    assert reach[1].last_spoke_at > reach[2].last_spoke_at
    assert [hit.conversation_id for hit in imessage_first] == [
        newer_imessage,
        older_imessage,
        older_slack,
    ]
    assert [hit.conversation_id for hit in one] == [older_slack]
    assert [hit.conversation_id for hit in slack_alone] == [older_slack]
    assert unreachable == ()
    assert nobody == ()


async def test_member_reach_skips_an_archived_agents_conversation(db: None) -> None:
    """An archived agent admits no turn, so its conversation is no reach — however recently the
    member spoke there."""
    workspace_id, member_id, agent_id = await _seed()
    retired = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=retired,
                workspace_id=workspace_id,
                name="~archived-00000000-0000-0000-0000-000000000001",
                archived_name="old-app",
                archived_at=now,
                prompt="gone",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _conversation(
        workspace_id, retired, "slack", member_id, spoken_by=member_id, spoke_at=now
    )
    live = await _conversation(
        workspace_id,
        agent_id,
        "slack",
        member_id,
        spoken_by=member_id,
        spoke_at=now - timedelta(days=1),
    )
    invoker = AdmissionInvoker(
        admission=Admission(dbos=_NoDbos(), durable_surfaces=DURABLE), workspace_id=workspace_id
    )
    with ws(workspace_id):
        reach = await invoker.member_reach(member_id, ("slack", "imessage"), 4)

    assert [hit.conversation_id for hit in reach] == [live]


def test_turn_tools_hand_every_extension_context_the_turns_invoker() -> None:
    """The production wiring the delivery seam rests on: a tool dispatched inside a turn reaches
    `invoke` and `member_reach` through the context `turn_tools` built, so the invoker the host was
    given at boot has to arrive there. Without it every such tool fails loud at the first call."""
    invoker = RecordingInvoker()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    _tools, ext_by_tool, verbs = turn_tools(
        (sample.manifest(),), store, audience=SHARED_AUDIENCE, invoker=invoker
    )
    assert ext_by_tool
    assert all(context.invoker is invoker for context in ext_by_tool.values())
    extension_actions = [
        bound.context
        for held in verbs.actions.values()
        for bound in held.values()
        if bound.context is not None
    ]
    assert extension_actions
    assert all(context.invoker is invoker for context in extension_actions)
    _tools, unwired, _verbs = turn_tools((sample.manifest(),), store, audience=SHARED_AUDIENCE)
    assert all(context.invoker is None for context in unwired.values())


async def test_member_reach_is_member_data_and_needs_an_invoker(db: None) -> None:
    workspace_id, member_id, _agent_id = await _seed()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=_NoDbos(), durable_surfaces=DURABLE), workspace_id=workspace_id
    )
    with ws(workspace_id):
        with pytest.raises(PermissionError):
            await context_for("sample", frozenset(), invoker=invoker).member_reach(
                member_id, ("slack",)
            )
        with pytest.raises(RuntimeError, match="invoker"):
            await context_for("sample", frozenset(), member_context_read=True).member_reach(
                member_id, ("slack",)
            )
