"""End-to-end proof of the notification queue: `notify` writes one row under the turn's own
authority, a repeat on a subject already raised folds and counts, the fences refuse with a reason
the model reads, and the `notification` kind reads, refuses, and dismisses through the real object
verbs."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from ufo_ext_app_notification.kind import RAISE_REFUSAL
from ufo_ext_app_notification.manifest import NAME, NOTIFICATION_AGENT, manifest
from ufo_ext_app_notification.notify_tool import (
    NOTIFICATION_AGENT_NAME,
    NOTIFY_FOLDED,
    NOTIFY_NEEDS_A_MEMBER,
    NOTIFY_QUEUED,
    NOTIFY_REPLY_REACHES,
    NOTIFY_SELF,
    NOTIFY_TOOL,
    NOTIFY_TOOL_NAME,
    NOTIFY_UNSTABLE_SUBJECT,
    NotifyInput,
    notify,
)
from ufo_ext_app_notification.store import (
    NOTIFICATION_FLAG,
    NOTIFICATION_KIND,
    NOTIFY_SUBJECTS_PER_TURN,
    NotificationStore,
)
from ufo_ext_app_notification.store import notification as notification_table

from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.objects import UnknownObject, VerbNotSupported
from ufo.runtime.queue import _agent_tools
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    MEMBER_ADMISSION,
    Agent,
    ModelAccountCapability,
    Turn,
    TurnContext,
    TurnRuntimeConfig,
)
from ufo.sdk.surfaces import REPLY_REACHES_NOBODY

pytestmark = pytest.mark.usefixtures("database_url")

SOURCE = "source/acme-crm"


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the notification tests")


async def _seed() -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """A workspace with a member, a working agent in a conversation, and the provisioned
    `notification` agent the inbox belongs to."""
    workspace_id, member_id, agent_id, inbox_id, conversation_id = (uuid4() for _ in range(5))
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
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, agent_id, inbox_id, conversation_id


async def _member(workspace_id: UUID, *, is_admin: bool = False) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                is_admin=is_admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


def _tool_ctx(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    *,
    speaker_member_id: UUID | None,
    runtime_config: TurnRuntimeConfig | None = None,
    model_accounts: tuple[ModelAccountCapability, ...] = (),
) -> ToolContext:
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=0,
            status="running",
            inbound="sync the crm",
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
            runtime_config=runtime_config,
            model_accounts=model_accounts,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(speaker_member_id),
        artifact_token_secret="",
        ext=context_for(NAME, frozenset(), member_context_read=True),
    )


def _object_tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    return next(tool for tool in tools if tool.name == name)


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


async def _rows(workspace_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(notification_table)
                    .where(notification_table.c.workspace_id == workspace_id)
                    .order_by(notification_table.c.subject)
                )
            )
            .mappings()
            .all()
        )


async def test_notify_writes_one_row_for_the_member_the_turn_acts_for(db: None) -> None:
    workspace_id, member_id, agent_id, inbox_id, conversation_id = await _seed()
    ctx = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=member_id,
        model_accounts=(
            ModelAccountCapability(
                provider="anthropic", slot=f"anthropic_api_key:member:{member_id}"
            ),
        ),
    )
    with ws(workspace_id), agent(agent_id):
        result = await notify(ctx, NotifyInput(subject=SOURCE, body="14 deals moved to closed won"))
        rows = await _rows(workspace_id)
        [stored] = await NotificationStore(ctx.ext).rows()

    assert result.is_error is False
    assert result.content[0].text == NOTIFY_QUEUED
    [row] = rows
    assert row["to_agent_id"] == inbox_id
    assert row["member_id"] == member_id
    assert row["subject"] == SOURCE
    assert row["occurrences"] == 1
    assert row["produced_by_agent_id"] == agent_id
    assert row["produced_by_agent_name"] == "assistant"
    assert row["produced_by_turn_id"] == ctx.turn.id
    assert row["produced_in_conversation_id"] == conversation_id
    assert ctx.turn.model_accounts
    assert stored.runtime_config == TurnRuntimeConfig(connections=())


async def test_notify_snapshots_only_the_selected_members_private_connection(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    other = await _member(workspace_id)
    grants = GrantStore()
    parent_config = TurnRuntimeConfig(
        model="claude-opus-4-8",
        internet_access=False,
        environment=f"sha256:{'a' * 64}",
    )
    with ws(workspace_id), agent(agent_id):
        selected = await grants.record(
            provider="hub",
            account_id="selected",
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )
        outside = await grants.record(
            provider="hub",
            account_id="outside",
            host="api.hub.test",
            grantor_member_id=other,
            shared=False,
        )
        ctx = replace(
            _tool_ctx(
                workspace_id,
                conversation_id,
                agent_id,
                speaker_member_id=member_id,
                runtime_config=parent_config,
            ),
            grants=grants,
            other_members_active=True,
            member_messages_active=True,
        )
        result = await notify(ctx, NotifyInput(subject=SOURCE, body="14 deals moved"))
        [row] = await NotificationStore(ctx.ext).rows()

    assert result.is_error is False
    assert row.runtime_config == parent_config.model_copy(update={"connections": (selected,)})
    assert outside not in row.runtime_config.connections


async def test_the_inbox_is_the_provisioned_agent_whatever_name_it_landed_under(
    db: None,
) -> None:
    """A member may already hold the name `notification`; the provision then lands on a free
    variant. `notify` finds the app by the extension that shipped it, so the member's agent gets no
    rows and is not what the self-mail fence guards."""
    workspace_id, member_id, agent_id, inbox_id, conversation_id = await _seed()
    impostor = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == inbox_id)
            .values(name="notification-app-notification")
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=impostor,
                workspace_id=workspace_id,
                name=NOTIFICATION_AGENT_NAME,
                prompt="a member's own",
                model="claude-opus-4-8",
                owner_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id), agent(agent_id):
        await notify(
            _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id),
            NotifyInput(subject=SOURCE, body="14 deals moved"),
        )
    with ws(workspace_id), agent(impostor):
        from_impostor = await notify(
            _tool_ctx(workspace_id, conversation_id, impostor, speaker_member_id=member_id),
            NotifyInput(subject="issue/7", body="the member's agent raising one"),
        )
        rows = await _rows(workspace_id)

    assert from_impostor.is_error is False
    assert {row["to_agent_id"] for row in rows} == {inbox_id}


async def test_a_repeat_on_a_subject_already_raised_folds_and_counts(db: None) -> None:
    """The page-revision rule: one subject is one row, the body is the latest revision, and the
    count is the number of times it was raised — across turns and across agents."""
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    grants = GrantStore()
    with ws(workspace_id), agent(agent_id):
        first_connection = await grants.record(
            provider="hub",
            account_id="first",
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )
        second_connection = await grants.record(
            provider="hub",
            account_id="second",
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )
        first = replace(
            _tool_ctx(
                workspace_id,
                conversation_id,
                agent_id,
                speaker_member_id=member_id,
                runtime_config=TurnRuntimeConfig(connections=(first_connection,)),
            ),
            grants=grants,
        )
        second = replace(
            _tool_ctx(
                workspace_id,
                conversation_id,
                agent_id,
                speaker_member_id=member_id,
                runtime_config=TurnRuntimeConfig(
                    internet_access=False, connections=(second_connection,)
                ),
            ),
            grants=grants,
        )
        await notify(first, NotifyInput(subject=SOURCE, body="14 deals moved"))
        result = await notify(second, NotifyInput(subject=SOURCE, body="now 400 pages changed"))
        [row] = await NotificationStore(second.ext).rows()

    assert result.content[0].text == NOTIFY_FOLDED.format(n=2)
    assert row.occurrences == 2
    assert row.body == "now 400 pages changed"
    assert row.last_raised_at >= row.created_at
    assert row.produced_by_turn_id == second.turn.id
    assert row.runtime_config == second.turn.runtime_config


async def test_a_missing_or_stale_runtime_config_fails_closed(db: None) -> None:
    workspace_id, member_id, agent_id, inbox_id, conversation_id = await _seed()
    ctx = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=member_id,
        runtime_config=TurnRuntimeConfig(connections=(uuid4(),)),
    )
    with ws(workspace_id), agent(agent_id):
        await notify(ctx, NotifyInput(subject=SOURCE, body="first"))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(notification_table)
                .where(notification_table.c.workspace_id == workspace_id)
                .values(
                    body="folded without scope",
                    occurrences=notification_table.c.occurrences + 1,
                )
            )
            await connection.execute(
                sa.insert(notification_table).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    to_agent_id=inbox_id,
                    member_id=member_id,
                    subject="source/outgoing",
                    body="written without runtime config",
                    occurrences=1,
                    produced_by_agent_id=agent_id,
                    produced_by_agent_name="assistant",
                    produced_by_turn_id=ctx.turn.id,
                    produced_in_conversation_id=conversation_id,
                    last_raised_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        rows = {row.subject: row for row in await NotificationStore(ctx.ext).rows()}

    assert rows[SOURCE].body == "folded without scope"
    assert rows[SOURCE].runtime_config == TurnRuntimeConfig(internet_access=False, connections=())
    assert rows["source/outgoing"].runtime_config == TurnRuntimeConfig(
        internet_access=False, connections=()
    )


def test_notify_is_offered_by_the_apps_flag() -> None:
    """One key withholds the feature from an environment: the tool names the flag, so where it
    reads off the catalog never carries `notify` and nothing is refused."""
    assert NOTIFY_TOOL.flag == NOTIFICATION_FLAG


async def test_the_same_subject_for_two_members_is_two_rows(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    other = await _member(workspace_id)
    with ws(workspace_id), agent(agent_id):
        await notify(
            _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id),
            NotifyInput(subject=SOURCE, body="a"),
        )
        await notify(
            _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=other),
            NotifyInput(subject=SOURCE, body="b"),
        )
        rows = await _rows(workspace_id)

    assert {row["member_id"] for row in rows} == {member_id, other}
    assert all(row["occurrences"] == 1 for row in rows)


async def test_a_turn_past_the_subject_cap_is_refused_a_new_subject_but_may_fold(
    db: None,
) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        for index in range(NOTIFY_SUBJECTS_PER_TURN):
            opened = await notify(ctx, NotifyInput(subject=f"page/{index}", body="changed"))
            assert opened.is_error is False
        refused = await notify(ctx, NotifyInput(subject="page/one-more", body="changed"))
        folded = await notify(ctx, NotifyInput(subject="page/0", body="changed again"))
        rows = await _rows(workspace_id)

    assert refused.is_error is True
    assert str(NOTIFY_SUBJECTS_PER_TURN) in refused.content[0].text
    assert folded.is_error is False
    assert len(rows) == NOTIFY_SUBJECTS_PER_TURN


async def test_a_speakerless_turn_notifies_nobody(db: None) -> None:
    workspace_id, _member_id, agent_id, _, conversation_id = await _seed()
    connections = (uuid4(), uuid4())
    ctx = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=None,
        runtime_config=TurnRuntimeConfig(internet_access=False, connections=connections),
    )
    with ws(workspace_id), agent(agent_id):
        result = await notify(ctx, NotifyInput(subject=SOURCE, body="auth failed 3 nights"))
        rows = await NotificationStore(ctx.ext).rows()

    assert result.is_error is True
    assert rows == ()


async def test_speakerless_turn_and_the_inbox_agent_itself_are_refused(db: None) -> None:
    workspace_id, member_id, agent_id, inbox_id, conversation_id = await _seed()
    nobody = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=None)
    itself = _tool_ctx(workspace_id, conversation_id, inbox_id, speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        refused_nobody = await notify(nobody, NotifyInput(subject=SOURCE, body="x"))
    with ws(workspace_id), agent(inbox_id):
        refused_self = await notify(itself, NotifyInput(subject=SOURCE, body="x"))
    rows = await _rows(workspace_id)

    assert refused_nobody.is_error is True
    assert refused_nobody.content[0].text == NOTIFY_NEEDS_A_MEMBER
    assert refused_self.is_error is True
    assert refused_self.content[0].text == NOTIFY_SELF
    assert rows == []


def _reaching(ctx: ToolContext, reply_reaches: str) -> ToolContext:
    return replace(
        ctx, turn=ctx.turn.model_copy(update={"context": TurnContext(reply_reaches=reply_reaches)})
    )


async def test_a_turn_whose_reply_reaches_the_member_says_it_there(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    spoken = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
    on_slack = _reaching(spoken, "slack")
    with ws(workspace_id), agent(agent_id):
        refused = await notify(
            on_slack, NotifyInput(subject="github/3733", body="rebased clean, checks green")
        )
        rows = await NotificationStore(on_slack.ext).rows()

    assert refused.is_error is True
    assert refused.content[0].text == NOTIFY_REPLY_REACHES.format(surface="slack")
    assert rows == ()


async def test_a_turn_nobody_reads_notifies(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    spoken = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
    unread = _reaching(spoken, REPLY_REACHES_NOBODY)
    with ws(workspace_id), agent(agent_id):
        queued = await notify(
            unread, NotifyInput(subject="github/3733", body="checks failed on main")
        )
        rows = await NotificationStore(unread.ext).rows()

    assert queued.is_error is False
    assert len(rows) == 1


async def test_the_kind_reads_the_members_own_rows_and_dismisses_them(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    other = await _member(workspace_id)
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await notify(ctx, NotifyInput(subject=SOURCE, body="14 deals moved"))
        [row] = await NotificationStore(ctx.ext).rows()
        mine = json.loads(await _dispatch(_object_tool("object_list"), ctx, kind=NOTIFICATION_KIND))
        theirs = json.loads(
            await _dispatch(
                _object_tool("object_list"),
                replace(ctx, speaker_member_id=other),
                kind=NOTIFICATION_KIND,
            )
        )
        got = yaml.safe_load(
            await _dispatch(_object_tool("object_get"), ctx, ref=f"{NOTIFICATION_KIND}/{row.name}")
        )
        with pytest.raises(VerbNotSupported, match=RAISE_REFUSAL):
            await _dispatch(
                _object_tool("object_apply"),
                ctx,
                manifest=(
                    f"kind: {NOTIFICATION_KIND}\nname: {row.name}\nspec:\n"
                    f"  subject: {SOURCE}\n  body: typed\n"
                ),
            )
        with pytest.raises(UnknownObject):
            await _dispatch(
                _object_tool("object_delete"),
                replace(ctx, speaker_member_id=other),
                kind=NOTIFICATION_KIND,
                name=row.name,
            )
        await _dispatch(_object_tool("object_delete"), ctx, kind=NOTIFICATION_KIND, name=row.name)
        remaining = await _rows(workspace_id)

    [listed] = mine["objects"]
    assert listed["name"] == row.name
    assert listed["subject"] == SOURCE
    assert listed["producer"] == "assistant"
    assert "mine" not in listed
    assert theirs["objects"] == []
    assert got["spec"] == {"subject": SOURCE, "body": "14 deals moved"}
    assert {link["relation"] for link in got["links"]} == {"created_in", "scoped_to"}
    assert got["status"]["occurrences"] == 1
    assert remaining == []


async def test_a_member_reads_and_dismisses_notifications_across_origin_scopes(db: None) -> None:
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    first_connection, second_connection = uuid4(), uuid4()
    first = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=member_id,
        runtime_config=TurnRuntimeConfig(connections=(first_connection,)),
    )
    second = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=member_id,
        runtime_config=TurnRuntimeConfig(connections=(second_connection,)),
    )
    reader = _tool_ctx(
        workspace_id,
        conversation_id,
        agent_id,
        speaker_member_id=member_id,
    )
    with ws(workspace_id), agent(agent_id):
        await notify(first, NotifyInput(subject="source/first", body="first"))
        await notify(second, NotifyInput(subject="source/second", body="second"))
        rows = {row.subject: row for row in await NotificationStore(first.ext).rows()}
        listed = json.loads(
            await _dispatch(_object_tool("object_list"), reader, kind=NOTIFICATION_KIND)
        )
        got = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                reader,
                ref=f"{NOTIFICATION_KIND}/{rows['source/second'].name}",
            )
        )
        await _dispatch(
            _object_tool("object_delete"),
            reader,
            kind=NOTIFICATION_KIND,
            name=rows["source/second"].name,
        )
        await _dispatch(
            _object_tool("object_delete"),
            reader,
            kind=NOTIFICATION_KIND,
            name=rows["source/first"].name,
        )
        remaining = await NotificationStore(first.ext).rows()

    assert {row["subject"] for row in listed["objects"]} == {"source/first", "source/second"}
    assert got["spec"] == {"subject": "source/second", "body": "second"}
    assert remaining == ()


async def test_an_admin_does_not_read_or_dismiss_another_members_notification(db: None) -> None:
    """Where this kind departs from the member-owned default. Every other such kind lets a
    workspace admin inspect a row, because the row is a thing the workspace holds. A notification
    is one agent's judgement about what should interrupt one person, addressed to them, so a second
    admin opening this app would otherwise read the first admin's whole inbox. The homepage draws
    on the same gate, which is how it was noticed."""
    workspace_id, member_id, agent_id, _, conversation_id = await _seed()
    admin = await _member(workspace_id, is_admin=True)
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await notify(ctx, NotifyInput(subject=SOURCE, body="14 deals moved"))
        [row] = await NotificationStore(ctx.ext).rows()
        inspector = replace(ctx, speaker_member_id=admin)
        listed = json.loads(
            await _dispatch(_object_tool("object_list"), inspector, kind=NOTIFICATION_KIND)
        )
        with pytest.raises(UnknownObject):
            await _dispatch(
                _object_tool("object_delete"), inspector, kind=NOTIFICATION_KIND, name=row.name
            )
        mine = json.loads(await _dispatch(_object_tool("object_list"), ctx, kind=NOTIFICATION_KIND))
        remaining = await _rows(workspace_id)

    assert listed["objects"] == []
    assert [entry["name"] for entry in mine["objects"]] == [row.name]
    assert len(remaining) == 1


def test_every_agent_holds_notify_and_the_notification_agent_does_not() -> None:
    """The app cannot raise a notification: its allowlist is the fence, and an agent naming no
    allowlist holds the tool through the member-facing set."""
    tools, _, _ = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    everyone = {tool.name for tool in _agent_tools(tools, None, MEMBER_ADMISSION)}
    app = {tool.name for tool in _agent_tools(tools, NOTIFICATION_AGENT.tools, MEMBER_ADMISSION)}
    assert NOTIFY_TOOL_NAME in everyone
    assert NOTIFY_TOOL_NAME not in app
    assert app == {
        "object_list",
        "object_get",
        "object_explain",
        "object_delete",
        "load_skill",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "spawn",
    }
    assert NOTIFICATION_AGENT.spec.visibility == "workspace"
    assert NOTIFICATION_AGENT.icon == "bell"


async def test_a_subject_minted_per_event_is_refused_and_a_stable_one_folds(db: None) -> None:
    """The subject is the fold key, so an identifier minted per event can never fold: every repeat
    opens a row of its own, which is the flood the fold exists to prevent. The field's description
    said as much and the first live sweep still reached for the page id it had in hand, so the
    refusal is what holds. It names what to use instead, and a stable subject folds as it always
    did."""
    workspace_id, member_id, agent_id, _inbox_id, conversation_id = await _seed()
    ext = context_for(NAME, frozenset(), member_context_read=True)
    with ws(workspace_id), agent(agent_id):
        ctx = _tool_ctx(workspace_id, conversation_id, agent_id, speaker_member_id=member_id)
        dashed = await notify(
            ctx,
            NotifyInput(
                subject="page/e715af2f-4ccc-537f-9444-092e0a092a5b", body="CI failed on a PR"
            ),
        )
        bare = await notify(
            ctx,
            NotifyInput(subject="notification/a123f8411ae74b7bbfdc656e8fa0b92a", body="x"),
        )
        first = await notify(ctx, NotifyInput(subject="github/3132", body="CI failed"))
        again = await notify(ctx, NotifyInput(subject="github/3132", body="CI failed again"))
        rows = await NotificationStore(ext).rows()

    assert dashed.is_error is True
    assert "e715af2f-4ccc-537f-9444-092e0a092a5b" in dashed.content[0].text
    assert "github/3132" in dashed.content[0].text
    assert bare.is_error is True
    assert bare.content[0].text == NOTIFY_UNSTABLE_SUBJECT.format(
        found="a123f8411ae74b7bbfdc656e8fa0b92a"
    )
    assert first.is_error is False
    assert again.is_error is False
    assert [(row.subject, row.occurrences) for row in rows] == [("github/3132", 2)]
