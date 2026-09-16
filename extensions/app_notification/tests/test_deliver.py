"""End-to-end proof of delivery: the Notification agent's `deliver` founds one relay turn in the
member's own direct chat — Slack before iMessage, never a channel thread their colleagues read —
through the real admission seam, which registers the writeback the poller posts. It records that
turn on the rows, refuses a second message in the same triage turn, falls back to the portal for a
member with no direct chat, and is held to the app's own provision. The append-only delivery
identity is the loop fence: `notify` inside its relay refuses, and the next drain tick founds
nothing."""

from collections.abc import Awaitable, Callable
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
    REACH_SURFACES,
    RELAY_INSTRUCTION,
    RELAY_KEY,
    RELAY_SOURCE,
    DeliverInput,
    _delivery_request_digest,
    _names,
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
    DeliveryDestination,
    NotificationStore,
    Posted,
)
from ufo_ext_app_notification.store import notification as notification_table
from ufo_ext_app_notification.store import notification_delivery as notification_delivery_table

from ufo.db import workspace_tx
from ufo.harness.sandbox.session import RunToken
from ufo.harness.untrusted import UNTRUSTED_CLOSE
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import InternetRule
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.queue import _agent_actions, _agent_tools, _load_turn
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, Agent, Turn, TurnRuntimeConfig
from ufo.sdk.surfaces import SILENCE_SENTINEL, is_silence_sentinel
from ufo.sdk.untrusted import wall

pytestmark = pytest.mark.usefixtures("database_url")

DURABLE = frozenset({"slack"})


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)
    on_enqueue: Callable[[str], Awaitable[None]] | None = None

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)
        if self.on_enqueue is not None:
            await self.on_enqueue(turn_id)


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
                seated_at=sa.func.now(),
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
    audience: Audience | None = None,
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
                member_id=None if audience is not None else member_id,
                audience=str(audience or conversation_audience(member_id)),
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


def _ext(workspace_id: UUID, dbos: StubDbos, durable: frozenset[str] = DURABLE) -> ExtensionContext:
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=durable), workspace_id=workspace_id
    )
    return context_for(NAME, frozenset(), invoker=invoker, member_context_read=True)


async def _connection(workspace_id: UUID, agent_id: UUID, member_id: UUID, account: str) -> UUID:
    with ws(workspace_id), agent(agent_id):
        return await GrantStore().record(
            provider="hub",
            account_id=account,
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )


def _tool_ctx(
    ext: ExtensionContext,
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    *,
    turn_id: UUID | None = None,
    conversation_id: UUID | None = None,
    speaker_member_id: UUID | None = None,
    runtime_config: TurnRuntimeConfig | None = None,
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
            runtime_config=runtime_config,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
    )


async def _raise_and_drain(
    ext: ExtensionContext,
    inbox_id: UUID,
    member_id: UUID,
    main_id: UUID,
    *subjects: str,
    runtime_config: TurnRuntimeConfig | None = None,
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
            runtime_config=runtime_config,
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


async def _turn(turn_id: UUID) -> sa.RowMapping:
    async with workspace_tx() as connection:
        return (
            (await connection.execute(sa.select(tables.turn).where(tables.turn.c.id == turn_id)))
            .mappings()
            .one()
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


async def _deliveries(workspace_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(notification_delivery_table).where(
                        notification_delivery_table.c.workspace_id == workspace_id
                    )
                )
            )
            .mappings()
            .all()
        )


def test_the_relay_instruction_carries_the_silence_sentinel() -> None:
    """The skip path: a notification the member's standing orders cover reaches them as nothing at
    all, which the surface can only do when the relay writes the sentinel it suppresses on."""
    assert SILENCE_SENTINEL in RELAY_INSTRUCTION
    assert is_silence_sentinel(SILENCE_SENTINEL)


async def test_deliver_founds_one_relay_turn_in_the_members_own_direct_chat(
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


async def test_deliver_takes_the_slack_dm_over_a_channel_thread_and_a_newer_imessage_chat(
    db: None,
) -> None:
    """The routing the surfaces cannot fix afterwards: a channel thread is where the member talks
    to their colleagues, so a message meant for them alone never lands there however recently they
    spoke in it, and Slack is asked before iMessage whichever chat they used last."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    now = datetime.now(UTC)
    channel = await _spoke_on(
        workspace_id,
        main_id,
        member_id,
        "slack",
        spoke_at=now - timedelta(minutes=1),
        audience=SHARED_AUDIENCE,
    )
    dm = await _spoke_on(
        workspace_id, main_id, member_id, "slack", spoke_at=now - timedelta(days=2)
    )
    texts = await _spoke_on(
        workspace_id, main_id, member_id, "imessage", spoke_at=now - timedelta(hours=1)
    )
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos, frozenset({"slack", "imessage"}))
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        result = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        dm_turns = await _turns(workspace_id, dm)
        channel_turns = await _turns(workspace_id, channel)
        text_turns = await _turns(workspace_id, texts)
        [row] = await _rows(workspace_id)

    assert result.content[0].text == DELIVERED.format(surface="slack")
    [_hello, relay] = dm_turns
    assert relay["admission_source"] == "scheduled"
    assert row["delivered_turn_id"] == relay["id"]
    assert len(channel_turns) == 1
    assert len(text_turns) == 1


async def test_deliver_reaches_imessage_when_the_member_holds_no_slack_chat_of_their_own(
    db: None,
) -> None:
    """The second surface is a fallback, not a tie-break: with no Slack chat of the member's own,
    the push goes to their texts rather than to the portal."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    now = datetime.now(UTC)
    await _spoke_on(
        workspace_id, main_id, member_id, "slack", spoke_at=now, audience=SHARED_AUDIENCE
    )
    texts = await _spoke_on(
        workspace_id, main_id, member_id, "imessage", spoke_at=now - timedelta(days=4)
    )
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos, frozenset({"slack", "imessage"}))
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        result = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn),
            DeliverInput(refs=refs, text="Acme CRM changed"),
        )
        text_turns = await _turns(workspace_id, texts)
        [row] = await _rows(workspace_id)

    assert result.content[0].text == DELIVERED.format(surface="imessage")
    assert len(text_turns) == 2
    assert row["delivered_surface"] == "imessage"


async def test_scoped_notifications_keep_exact_capabilities_through_triage_and_delivery(
    db: None,
) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    await _connection(workspace_id, main_id, member_id, "allowed")
    await _connection(workspace_id, main_id, member_id, "outside")
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    allowed_config = TurnRuntimeConfig(internet_access=False)
    outside_config = TurnRuntimeConfig()
    with ws(workspace_id), agent(main_id):
        await notify(
            replace(
                _tool_ctx(
                    ext,
                    workspace_id,
                    main_id,
                    member_id,
                    speaker_member_id=member_id,
                    runtime_config=allowed_config,
                ),
                grants=GrantStore(),
            ),
            NotifyInput(subject="source/allowed", body="allowed changed"),
        )
        await notify(
            replace(
                _tool_ctx(
                    ext,
                    workspace_id,
                    main_id,
                    member_id,
                    speaker_member_id=member_id,
                    runtime_config=outside_config,
                ),
                grants=GrantStore(),
            ),
            NotifyInput(subject="source/outside", body="outside changed"),
        )
    with ws(workspace_id), agent(inbox_id):
        await InboxDrain(ctx=ext).run()
        rows = {row.subject: row for row in await NotificationStore(ext).rows()}
        allowed_row = rows["source/allowed"]
        outside_row = rows["source/outside"]
        assert allowed_row.triaged_turn_id is not None
        triage = await _turn(allowed_row.triaged_turn_id)
        triage_config = TurnRuntimeConfig.model_validate(triage["runtime_config"])
        unrestricted = await deliver(
            _tool_ctx(ext, workspace_id, inbox_id, member_id),
            DeliverInput(refs=(f"{NOTIFICATION_KIND}/{allowed_row.name}",), text="allowed changed"),
        )
        refused = await deliver(
            _tool_ctx(
                ext,
                workspace_id,
                inbox_id,
                member_id,
                turn_id=allowed_row.triaged_turn_id,
                runtime_config=triage_config,
            ),
            DeliverInput(refs=(f"{NOTIFICATION_KIND}/{outside_row.name}",), text="outside changed"),
        )
        delivered = await deliver(
            _tool_ctx(
                ext,
                workspace_id,
                inbox_id,
                member_id,
                turn_id=allowed_row.triaged_turn_id,
                runtime_config=triage_config,
            ),
            DeliverInput(refs=(f"{NOTIFICATION_KIND}/{allowed_row.name}",), text="allowed changed"),
        )
        [_hello, relay] = await _turns(workspace_id, dm)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == relay["id"])
                .values(status="running", updated_at=sa.func.now())
            )
        settled = {row.subject: row for row in await NotificationStore(ext).rows()}

    relay_config = TurnRuntimeConfig.model_validate(relay["runtime_config"])
    relayed = _tool_ctx(
        ext,
        workspace_id,
        main_id,
        member_id,
        turn_id=relay["id"],
        conversation_id=dm,
        runtime_config=relay_config,
    )
    relay_ctx = replace(
        relayed,
        turn=relayed.turn.model_copy(update={"member_id": relay["member_id"]}),
        grants=GrantStore(),
    )
    with ws(workspace_id), agent(main_id):
        accounts = await relay_ctx.connector_accounts("hub")
    rules = await PerAgentRules(base=(), grants=GrantStore(), internet=(InternetRule(),)).resolve(
        RunToken(workspace_id, relay["id"])
    )

    assert triage_config == allowed_config
    assert unrestricted.is_error is True
    assert unrestricted.content[0].text == NOTHING_TO_DELIVER
    assert refused.is_error is True
    assert refused.content[0].text == NOTHING_TO_DELIVER
    assert delivered.is_error is False
    assert relay_config == allowed_config
    assert relay["member_id"] == member_id
    assert accounts == ("allowed", "outside")
    assert InternetRule() not in rules
    assert settled["source/outside"].delivered_turn_id is None


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
                ext,
                workspace_id,
                main_id,
                member_id,
                turn_id=relay["id"],
                conversation_id=dm,
                speaker_member_id=member_id,
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


async def test_a_fold_during_relay_admission_keeps_the_new_occurrence_and_the_loop_fence(
    db: None,
) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    first_config = TurnRuntimeConfig(internet_access=False)
    second_config = TurnRuntimeConfig()
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    store = NotificationStore(ext)
    refused_in_relay: list[ToolResult] = []
    with ws(workspace_id):
        with agent(inbox_id):
            triage_turn, refs = await _raise_and_drain(
                ext,
                inbox_id,
                member_id,
                main_id,
                "source/crm",
                runtime_config=first_config,
            )

        async def fold_while_enqueuing(relay_turn_id: str) -> None:
            loaded, loaded_agent, loaded_audience = await _load_turn(UUID(relay_turn_id))
            relay_ctx = replace(
                _tool_ctx(
                    ext,
                    workspace_id,
                    main_id,
                    member_id,
                    turn_id=loaded.id,
                    conversation_id=dm,
                    speaker_member_id=member_id,
                    runtime_config=loaded.runtime_config,
                ),
                turn=loaded,
                agent=loaded_agent,
                audience=loaded_audience,
            )
            with agent(main_id):
                refused_in_relay.append(
                    await notify(
                        relay_ctx,
                        NotifyInput(subject="relay/loop", body="delivery started"),
                    )
                )
            await store.post(
                to_agent_id=inbox_id,
                member_id=member_id,
                subject="source/crm",
                body="new occurrence",
                agent_id=main_id,
                agent_name="assistant",
                turn_id=uuid4(),
                conversation_id=dm,
                runtime_config=second_config,
            )

        dbos.on_enqueue = fold_while_enqueuing
        first = await deliver(
            _tool_ctx(
                ext,
                workspace_id,
                inbox_id,
                member_id,
                turn_id=triage_turn,
                runtime_config=first_config,
            ),
            DeliverInput(refs=refs, text="first occurrence"),
        )
        dbos.on_enqueue = None
        [folded] = await store.rows()
        [first_delivery] = await _deliveries(workspace_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(notification_table)
                .where(notification_table.c.id == folded.id)
                .values(
                    triaged_at=datetime.now(UTC) - timedelta(seconds=DRAIN_COOLDOWN_SECONDS + 1)
                )
            )
        await InboxDrain(ctx=ext).run()
        [retried] = await store.rows()
        assert retried.triaged_turn_id is not None
        second = await deliver(
            _tool_ctx(
                ext,
                workspace_id,
                inbox_id,
                member_id,
                turn_id=retried.triaged_turn_id,
                runtime_config=second_config,
            ),
            DeliverInput(refs=refs, text="new occurrence"),
        )
        [settled] = await store.rows()

    [refused] = refused_in_relay
    assert refused.is_error is True
    assert refused.content[0].text == NOTIFY_INSIDE_A_DELIVERY
    assert first.is_error is False
    assert folded.occurrences == 2
    assert folded.body == "new occurrence"
    assert folded.runtime_config == second_config
    assert folded.delivered_turn_id is None
    assert first_delivery["relay_turn_id"] is not None
    assert second.is_error is False
    assert settled.delivered_turn_id not in (None, first_delivery["relay_turn_id"])


async def test_an_exact_delivery_replay_reuses_the_relay_and_finishes_the_mark(db: None) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    dm = await _spoke_on(workspace_id, main_id, member_id, "slack")
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        ctx = _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn)
        request = DeliverInput(refs=refs, text="Acme CRM changed")
        first = await deliver(ctx, request)
        [_hello, relay] = await _turns(workspace_id, dm)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(notification_table)
                .where(notification_table.c.workspace_id == workspace_id)
                .values(delivered_turn_id=None, delivered_surface=None)
            )
        replay = await deliver(ctx, request)
        turns = await _turns(workspace_id, dm)
        [row] = await _rows(workspace_id)
        [delivery] = await _deliveries(workspace_id)

    assert first.is_error is False
    assert replay.is_error is False
    assert len(turns) == 2
    assert delivery["relay_turn_id"] == relay["id"]
    assert row["delivered_turn_id"] == relay["id"]


@pytest.mark.parametrize(
    "archive_destination", [False, True], ids=["newer-reach", "archived-destination"]
)
async def test_a_crash_replay_uses_the_reserved_destination_after_reach_changes(
    db: None, archive_destination: bool
) -> None:
    workspace_id, member_id, main_id, inbox_id = await _seed()
    original_conversation = await _spoke_on(
        workspace_id,
        main_id,
        member_id,
        "slack",
        spoke_at=datetime.now(UTC) - timedelta(days=1),
    )
    dbos = StubDbos()
    ext = _ext(workspace_id, dbos)
    store = NotificationStore(ext)
    with ws(workspace_id), agent(inbox_id):
        triage_turn, refs = await _raise_and_drain(ext, inbox_id, member_id, main_id, "source/crm")
        ctx = _tool_ctx(ext, workspace_id, inbox_id, member_id, turn_id=triage_turn)
        request = DeliverInput(refs=refs, text="Acme CRM changed")
        rows = await store.deliverable(member_id, _names(refs))
        [reach] = await ext.member_reach(member_id, REACH_SURFACES)
        delivery_key = RELAY_KEY.format(turn=triage_turn.hex)
        destination = await store.prepare_delivery(
            delivery_key,
            _delivery_request_digest(rows, request.text),
            DeliveryDestination(
                conversation_id=reach.conversation_id,
                agent_id=reach.agent_id,
                surface=reach.surface,
            ),
        )
        assert destination is not None
        original_relay = await ext.invoke(
            destination.conversation_id,
            destination.agent_id,
            wall(RELAY_SOURCE, request.text) + RELAY_INSTRUCTION,
            delivery_key,
            holds_work_already_done=True,
            as_scheduled=True,
            runtime_config=ctx.turn.runtime_config,
        )
        assert original_relay is not None
        [unbound] = await _deliveries(workspace_id)
        newer_conversation: UUID | None = None
        if archive_destination:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.agent)
                    .where(tables.agent.c.id == main_id)
                    .values(
                        name=f"~archived-{main_id}",
                        archived_name="assistant",
                        archived_at=sa.func.now(),
                        is_main=False,
                    )
                )
        else:
            newer_conversation = await _spoke_on(workspace_id, main_id, member_id, "slack")
        current_reaches = await ext.member_reach(member_id, REACH_SURFACES)
        result = await deliver(ctx, request)
        original_turns = await _turns(workspace_id, original_conversation)
        newer_turns = (
            [] if newer_conversation is None else await _turns(workspace_id, newer_conversation)
        )
        [bound] = await _deliveries(workspace_id)
        [row] = await _rows(workspace_id)

    assert str(original_relay) in dbos.enqueued
    assert unbound["relay_turn_id"] is None
    assert result.is_error is False
    assert len(original_turns) == 2
    if archive_destination:
        assert current_reaches == ()
    else:
        assert len(newer_turns) == 1
        assert current_reaches[0].conversation_id == newer_conversation
    assert bound["conversation_id"] == original_conversation
    assert bound["surface"] == "slack"
    assert bound["relay_turn_id"] == original_relay
    assert row["delivered_turn_id"] == original_relay


async def test_a_second_deliver_in_any_turn_founds_nothing_and_marks_nothing(db: None) -> None:
    """The delivery identity is the calling turn, not the triage stamp: a turn that triaged
    nothing delivers once, and a different second request is refused."""
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


async def test_a_member_with_no_direct_chat_is_marked_delivered_to_the_portal(
    db: None,
) -> None:
    """A member whose whole Slack presence is a channel thread has no chat of their own, so the
    push has nowhere private to land and the rows stay on the portal."""
    workspace_id, member_id, main_id, inbox_id = await _seed()
    await _spoke_on(workspace_id, main_id, member_id, "web")
    channel = await _spoke_on(workspace_id, main_id, member_id, "slack", audience=SHARED_AUDIENCE)
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
        channel_turns = await _turns(workspace_id, channel)

    assert result.is_error is False
    assert result.content[0].text == DELIVERED_TO_PORTAL_ONLY
    assert row["delivered_surface"] == PORTAL_ONLY
    assert row["delivered_turn_id"] is None
    assert len(dbos.enqueued) == before
    assert len(channel_turns) == 1


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
            runtime_config=None,
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
        reach = await ext.member_reach(member_id, REACH_SURFACES)
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
        woken = _tool_ctx(ext, workspace_id, main_id, member_id, speaker_member_id=member_id)
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
