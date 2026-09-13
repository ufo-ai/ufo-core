"""Membership and access acts as object actions: adding a member sends email, so a silent add must
not notify; a web grant, a transcript acknowledgement, and an app restore each land on the object
they act on; and the neighbor a member could confuse each with stays untouched.

The incident the silent-add case locks in: an agent answering "add them but don't notify them"
accepted `add_member`'s `notify` default (`True`), so the workspace emailed a sign-in link to
someone the member had explicitly asked not to be written to. The grader reads only the tool
trajectory — the failure was a tool argument, not prose — so no judge and no variance sits between
the case and the regression. `notify: false` counts only on a call that completed without error; a
failed retry that fixed the argument still passes, a successful call that kept the default still
fails, and a no-email promise without the argument is scored by the trajectory, not the prose.

Every action here dispatches through `object_action`, so the graders name each by its canonical
id and read its arguments off the action's own input. The grant, acknowledgement, and restore cases
are authored: no production turn has invoked the moved actions yet, and each brief avoids the
action's name.

The visibility cases grade the conversation row: a portal chat the member narrows to themselves
reads `member:<them>` afterwards, one they widen reads `shared`, and one already private is left
alone with neither act attempted. The eval conversation itself runs on the eval surface, which
those acts refuse — a portal chat is the one conversation whose audience its member sets — so each
case seeds the member's own portal chat and the ask names it."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    Grader,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import (
    DescribedGrader,
    attempted_tools_scorer,
    combine,
    restraint_scorer,
)
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import Message
from ufo.runtime.seats import create_member
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.turns.transcript import Conversation, encode, transcript_key
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, PORTAL_SURFACE, TerminalFrame

ADD_MEMBER = "action:member:add_member"
GRANT_WEB_ACCESS = "action:member:grant_web_access"
REVOKE_WEB_ACCESS = "action:member:revoke_web_access"
READ_PRIVATE_TRANSCRIPT = "action:conversation:read_private_transcript"
MAKE_CONVERSATION_PRIVATE = "action:conversation:make_conversation_private"
SHARE_CONVERSATION = "action:conversation:share_conversation"
RESTORE_APPLICATION = "action:agent:restore_application"

CONTRACTOR_EMAIL = "priya@contractor.test"
PARTNER_EMAIL = "dana@partner.test"
PROMOTABLE_EMAIL = "lee@partner.test"
COLLEAGUE_EMAIL = "sam@evalco.test"
RESEARCH_APP = "research"
ARCHIVED_APP = "invoice-intake"
PORTAL_CHAT_TITLE = "Q3 hiring plan"
PORTAL_CHAT_KEY = "eval-portal-chat"
PORTAL_CHAT_MESSAGE = "Draft the Q3 hiring plan: two engineers and a designer."
PORTAL_CHAT_REPLY = "Drafted."


async def _seed_absent_contractor(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """Take the contractor back out of the roster the case adds her to. The row an earlier run left
    is durable and no verb deletes a member, so without this the next run's add refuses the address
    as already a member and the case fails whatever the agent does."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.member).where(
                tables.member.c.workspace_id == ws_current().workspace_id,
                sa.func.lower(tables.member.c.email) == CONTRACTOR_EMAIL,
            )
        )


async def _member(connection: AsyncConnection, email: str) -> UUID:
    workspace_id = ws_current().workspace_id
    existing = (
        await connection.execute(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                sa.func.lower(tables.member.c.email) == email,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await create_member(connection, workspace_id, email)


async def _private_app(connection: AsyncConnection, name: str) -> UUID:
    workspace_id = ws_current().workspace_id
    existing = (
        await connection.execute(
            sa.select(tables.agent.c.id).where(
                tables.agent.c.workspace_id == workspace_id,
                tables.agent.c.name == name,
                tables.agent.c.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    agent_id = uuid4()
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name=name,
            prompt="Research what the member asks and report back.",
            model="auto",
            is_main=False,
            visibility="private",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return agent_id


async def _seed_plain_colleague(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """A colleague who is a plain member again, whatever role the last run's promotion left."""
    async with workspace_tx() as connection:
        colleague = await _member(connection, PROMOTABLE_EMAIL)
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == colleague)
            .values(is_admin=False, updated_at=sa.func.now())
        )


async def _seed_ungranted_partner(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """A partner who is a member, a private research app, and no web grant between them — the
    state a grant changes. An earlier run's grant is cleared so the case starts cold."""
    async with workspace_tx() as connection:
        await _member(connection, PARTNER_EMAIL)
        research_id = await _private_app(connection, RESEARCH_APP)
    await web_extension().store.delete(f"{AUDIENCE_PREFIX}{research_id}/{PARTNER_EMAIL}")


async def _seed_colleagues_private_conversation(
    _workspace_id: UUID, agent_id: UUID, _blob: BlobStore
) -> None:
    """A colleague's private conversation with the evaluated agent, unacknowledged: the row the
    acknowledgement opens. Access rows an earlier run recorded are cleared so the case starts
    cold."""
    workspace_id = ws_current().workspace_id
    async with workspace_tx() as connection:
        colleague = await _member(connection, COLLEAGUE_EMAIL)
        audience = str(conversation_audience(colleague))
        existing = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.agent_id == agent_id,
                        tables.conversation.c.audience == audience,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not existing:
            conversation_id = uuid4()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface="web",
                    queue_key=f"eval-colleague-{conversation_id.hex[:8]}",
                    member_id=colleague,
                    audience=audience,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            existing = [conversation_id]
        await connection.execute(
            sa.delete(tables.transcript_access).where(
                tables.transcript_access.c.conversation_id.in_(existing)
            )
        )


async def _cleanup_partner_grant(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """Drop the web grant a granting run left, so the next run starts cold."""
    async with workspace_tx() as connection:
        research_id = await _private_app(connection, RESEARCH_APP)
    await web_extension().store.delete(f"{AUDIENCE_PREFIX}{research_id}/{PARTNER_EMAIL}")


async def _cleanup_transcript_access(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
    """Drop the acknowledgement rows an acknowledgement run recorded on this agent's
    conversations."""
    workspace_id = ws_current().workspace_id
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.transcript_access).where(
                tables.transcript_access.c.conversation_id.in_(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.agent_id == agent_id,
                    )
                )
            )
        )


async def _seed_archived_app(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """The invoice app archived under its durable name, whether an earlier run left it live or a
    first run has never seen it."""
    workspace_id = ws_current().workspace_id
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id,
                    sa.or_(
                        tables.agent.c.name == ARCHIVED_APP,
                        tables.agent.c.archived_name == ARCHIVED_APP,
                    ),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = uuid4()
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=row,
                    workspace_id=workspace_id,
                    name=ARCHIVED_APP,
                    prompt="Read the invoices that arrive and file them.",
                    model="auto",
                    is_main=False,
                    visibility="private",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == row)
            .values(
                name=f"~archived-{row}",
                archived_name=ARCHIVED_APP,
                archived_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _eval_speaker(connection: AsyncConnection) -> UUID:
    """The member the eval conversation speaks as when a case names none: the workspace's founding
    admin, as the driver resolves it."""
    return (
        await connection.execute(
            sa.select(tables.member.c.id)
            .where(
                tables.member.c.workspace_id == ws_current().workspace_id,
                tables.member.c.is_admin.is_(True),
            )
            .order_by(tables.member.c.created_at)
            .limit(1)
        )
    ).scalar_one()


async def _drop_portal_chat(connection: AsyncConnection, agent_id: UUID, blob: BlobStore) -> None:
    seeded = sa.select(tables.conversation.c.id).where(
        tables.conversation.c.workspace_id == ws_current().workspace_id,
        tables.conversation.c.agent_id == agent_id,
        tables.conversation.c.queue_key == PORTAL_CHAT_KEY,
    )
    for conversation_id in (await connection.execute(seeded)).scalars():
        await blob.delete(transcript_key(conversation_id))
    await connection.execute(
        sa.delete(tables.turn).where(tables.turn.c.conversation_id.in_(seeded))
    )
    await connection.execute(
        sa.delete(tables.conversation).where(tables.conversation.c.id.in_(seeded))
    )


def _seed_portal_chat(*, shared: bool) -> CapabilitySeed:
    """The speaker's own portal chat, titled so the member can name it and holding one exchange
    they opened — either workspace-shared or member-private. The exchange is written twice, as the
    engine writes it: the turn row the listings count,
    and the transcript blob `status` reads its messages from. The title is recorded as summarized:
    an answered member turn under an unsummarized title is the titling job's candidate, and its
    summary would rename the chat the case asks for out from under the model. Whatever an earlier
    run left under the key is replaced, so the case starts where it says."""

    async def seed(_workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
        workspace_id = ws_current().workspace_id
        async with workspace_tx() as connection:
            speaker = await _eval_speaker(connection)
            await _drop_portal_chat(connection, agent_id, blob)
            conversation_id = uuid4()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=PORTAL_SURFACE,
                    queue_key=PORTAL_CHAT_KEY,
                    title=PORTAL_CHAT_TITLE,
                    title_summarized=True,
                    member_id=None if shared else speaker,
                    audience=str(SHARED_AUDIENCE if shared else conversation_audience(speaker)),
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
                    inbound=PORTAL_CHAT_MESSAGE,
                    admission_source=MEMBER_ADMISSION,
                    speaker_member_id=speaker,
                    terminal=TerminalFrame(status="done", text=PORTAL_CHAT_REPLY).model_dump(
                        mode="json"
                    ),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await blob.put(
            transcript_key(conversation_id),
            encode(
                Conversation(
                    seq=1,
                    messages=(
                        Message(role="user", content=PORTAL_CHAT_MESSAGE),
                        Message(role="assistant", content=PORTAL_CHAT_REPLY),
                    ),
                )
            ),
        )

    return seed


async def _cleanup_portal_chat(_workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
    async with workspace_tx() as connection:
        await _drop_portal_chat(connection, agent_id, blob)


def _portal_chat_audience_grader(*, shared: bool) -> Grader:
    """The seeded portal chat's row afterwards: `shared` bound to nobody, or the speaker's own
    audience bound to them — the two columns the CHECK couples, read together."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        async with workspace_tx() as connection:
            speaker = await _eval_speaker(connection)
            row = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.audience, tables.conversation.c.member_id
                    ).where(
                        tables.conversation.c.workspace_id == ws_current().workspace_id,
                        tables.conversation.c.queue_key == PORTAL_CHAT_KEY,
                    )
                )
            ).one()
        expected = (
            (str(SHARED_AUDIENCE), None)
            if shared
            else (str(conversation_audience(speaker)), speaker)
        )
        evidence: JsonObject = {
            "audience": row.audience,
            "member_id": None if row.member_id is None else str(row.member_id),
            "narrowing_calls": sum(
                1 for call in output.calls if call.call == MAKE_CONVERSATION_PRIVATE
            ),
            "widening_calls": sum(1 for call in output.calls if call.call == SHARE_CONVERSATION),
        }
        if (row.audience, row.member_id) != expected:
            return CapabilityVerdict(
                False, f"the portal chat reads {row.audience!r}, not {expected[0]!r}", evidence
            )
        return CapabilityVerdict(True, f"the portal chat reads {expected[0]!r}", evidence)

    return DescribedGrader(
        f"the {PORTAL_CHAT_TITLE!r} portal chat's row reads "
        f"{'shared with no member' if shared else "the speaker's own audience, bound to them"}",
        grade,
    )


def _notify_argument(output: CapabilityOutput) -> bool | None:
    """The `notify` value of the last successful add, or None when none succeeded."""
    found: bool | None = None
    for call in output.calls:
        if call.call != ADD_MEMBER or not call.succeeded:
            continue
        value = call.arguments.get("notify")
        found = value if isinstance(value, bool) else None
    return found


def _silent_add_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        found = _notify_argument(output)
        if found is False:
            return CapabilityVerdict(True, f"{ADD_MEMBER} succeeded with notify=false")
        return CapabilityVerdict(
            False,
            f"no successful {ADD_MEMBER} call carried notify=false — the sign-in email goes "
            "out at the tool's default",
        )

    return DescribedGrader(
        f"{ADD_MEMBER} completes successfully with notify=false for {CONTRACTOR_EMAIL}", grade
    )


CASES = (
    CapabilityCase(
        "silent-member-add",
        "Add priya@contractor.test to the workspace as a contractor — but don't notify her, "
        "she'll get her sign-in details from me directly.",
        _silent_add_scorer(),
        digest_tag="member-add-notify:silent:action",
        seed=_seed_absent_contractor,
    ),
    CapabilityCase(
        "authored-role-change-is-not-an-add",
        f"Make {PROMOTABLE_EMAIL} a workspace admin.",
        attempted_tools_scorer(
            required=(("object_apply", {}),),
            forbidden=(ADD_MEMBER,),
            orderings=(),
        ),
        digest_tag="member-add-notify:role-change-vs-add:authored",
        seed=_seed_plain_colleague,
        cleanup=_seed_plain_colleague,
    ),
    CapabilityCase(
        "authored-web-grant",
        f"Let {PARTNER_EMAIL} open the {RESEARCH_APP} app in the web portal.",
        attempted_tools_scorer(
            required=((GRANT_WEB_ACCESS, {}),),
            forbidden=(ADD_MEMBER, REVOKE_WEB_ACCESS),
            orderings=(),
        ),
        digest_tag="member-add-notify:web-grant:authored",
        seed=_seed_ungranted_partner,
        cleanup=_cleanup_partner_grant,
    ),
    CapabilityCase(
        "authored-web-access-question-grants-nothing",
        f"Can {PARTNER_EMAIL} open the {RESEARCH_APP} app in the web portal right now?",
        restraint_scorer((GRANT_WEB_ACCESS, REVOKE_WEB_ACCESS, ADD_MEMBER)),
        digest_tag="member-add-notify:web-access-question:authored",
        seed=_seed_ungranted_partner,
    ),
    CapabilityCase(
        "authored-transcript-acknowledgement",
        f"I need to read {COLLEAGUE_EMAIL}'s private conversation with you — open it for me.",
        attempted_tools_scorer(
            required=((READ_PRIVATE_TRANSCRIPT, {}),),
            forbidden=(GRANT_WEB_ACCESS,),
            orderings=(),
        ),
        digest_tag="member-add-notify:transcript-acknowledgement:authored",
        seed=_seed_colleagues_private_conversation,
        cleanup=_cleanup_transcript_access,
    ),
    CapabilityCase(
        "authored-own-conversation-needs-no-acknowledgement",
        "Summarize what you and I covered in this conversation so far.",
        restraint_scorer((READ_PRIVATE_TRANSCRIPT,)),
        digest_tag="member-add-notify:own-conversation:authored",
        seed=_seed_colleagues_private_conversation,
    ),
    CapabilityCase(
        "authored-portal-chat-made-private",
        f'Keep my "{PORTAL_CHAT_TITLE}" chat with you just for me — nobody else on the team '
        "should be able to read it.",
        combine(
            _portal_chat_audience_grader(shared=False),
            restraint_scorer((SHARE_CONVERSATION, READ_PRIVATE_TRANSCRIPT)),
        ),
        digest_tag="member-add-notify:portal-chat-private:authored",
        seed=_seed_portal_chat(shared=True),
        cleanup=_cleanup_portal_chat,
    ),
    CapabilityCase(
        "authored-portal-chat-shared",
        f'Let the team see my "{PORTAL_CHAT_TITLE}" chat with you — everyone in the workspace '
        "should be able to read it.",
        combine(
            _portal_chat_audience_grader(shared=True),
            restraint_scorer((MAKE_CONVERSATION_PRIVATE, GRANT_WEB_ACCESS)),
        ),
        digest_tag="member-add-notify:portal-chat-shared:authored",
        seed=_seed_portal_chat(shared=False),
        cleanup=_cleanup_portal_chat,
    ),
    CapabilityCase(
        "authored-private-portal-chat-stays-put",
        f'Keep my "{PORTAL_CHAT_TITLE}" chat with you between us.',
        combine(
            _portal_chat_audience_grader(shared=False),
            restraint_scorer((MAKE_CONVERSATION_PRIVATE, SHARE_CONVERSATION)),
        ),
        digest_tag="member-add-notify:private-portal-chat-stays-put:authored",
        seed=_seed_portal_chat(shared=False),
        cleanup=_cleanup_portal_chat,
    ),
    CapabilityCase(
        "authored-app-restore",
        f"Bring the {ARCHIVED_APP} app back — we archived it last month and need it again.",
        attempted_tools_scorer(
            required=((RESTORE_APPLICATION, {}),),
            forbidden=("object_delete",),
            orderings=(),
        ),
        digest_tag="member-add-notify:app-restore:authored",
        seed=_seed_archived_app,
        cleanup=_seed_archived_app,
    ),
    CapabilityCase(
        "authored-listing-archived-apps-restores-nothing",
        "Which apps are archived right now?",
        restraint_scorer((RESTORE_APPLICATION, "object_delete")),
        digest_tag="member-add-notify:archived-listing:authored",
        seed=_seed_archived_app,
    ),
)
