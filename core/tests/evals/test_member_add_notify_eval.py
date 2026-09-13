"""The member_add_notify grader is a pure decision over the tool trajectory: whether a successful
`add_member` dispatch on the member collection carried `notify=false`. It reproduces the incident
where an agent asked to add someone quietly accepted the tool's default (`notify=True`) and the
workspace emailed a sign-in link to a person the member had explicitly asked not to be written
to."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.member_add_notify import (
    CASES,
    CONTRACTOR_EMAIL,
    PORTAL_CHAT_KEY,
    PORTAL_CHAT_TITLE,
    _cleanup_portal_chat,
    _portal_chat_audience_grader,
    _seed_absent_contractor,
    _seed_portal_chat,
    _silent_add_scorer,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.host.kinds.conversations import CONVERSATION_OBJECT
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import awaiting_a_title
from ufo.runtime.ext.surface import ConversationDirectory
from ufo.runtime.objects import ObjectListQuery
from ufo.runtime.seats import create_member
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.turns.transcript import transcript_key
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, PORTAL_SURFACE, Agent, Turn


def _add_member(notify: bool | None, is_error: bool = False) -> ToolInvocation:
    input_payload: dict[str, object] = {"email": CONTRACTOR_EMAIL}
    if notify is not None:
        input_payload["notify"] = notify
    return ToolInvocation(
        name="object_action",
        input={"kind": "member", "action": "add_member", "input": input_payload},
        result="added" if not is_error else "already a member",
        has_result=True,
        is_error=is_error,
    )


def _output(*calls: ToolInvocation, response: str = "Done.") -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def test_a_call_left_at_the_notify_default_fails() -> None:
    """The exact incident: `add_member` called with no `notify` argument keeps the tool's default
    (`True`) and the sign-in email goes out anyway."""
    verdict = await _silent_add_scorer()(_output(_add_member(notify=None)))

    assert not verdict.passed
    assert "notify=false" in verdict.reason


async def test_a_call_that_explicitly_sets_notify_true_fails() -> None:
    verdict = await _silent_add_scorer()(_output(_add_member(notify=True)))

    assert not verdict.passed


async def test_a_call_with_notify_false_passes() -> None:
    verdict = await _silent_add_scorer()(_output(_add_member(notify=False)))

    assert verdict.passed
    assert "notify=false" in verdict.reason


async def test_no_add_member_call_at_all_fails() -> None:
    verdict = await _silent_add_scorer()(_output())

    assert not verdict.passed


async def test_a_failed_call_does_not_count_even_with_notify_false() -> None:
    """A call that errored never reached the tool, so its argument never suppressed anything."""
    verdict = await _silent_add_scorer()(_output(_add_member(notify=False, is_error=True)))

    assert not verdict.passed


async def test_a_failed_default_retry_that_fixes_the_argument_passes() -> None:
    """A retry that corrects the mistake before it lands is the behavior the case wants, not a
    failure — only a call that actually completed with the default counts against it."""
    verdict = await _silent_add_scorer()(
        _output(_add_member(notify=None, is_error=True), _add_member(notify=False))
    )

    assert verdict.passed


async def test_grading_follows_the_last_successful_call() -> None:
    """A member could add someone quietly, then add a second person loudly on purpose — the grader
    reads the trajectory as given rather than assuming only one call ever happens; here the last
    successful call is the one that matters."""
    verdict = await _silent_add_scorer()(
        _output(_add_member(notify=False), _add_member(notify=True))
    )

    assert not verdict.passed


def test_the_case_asks_for_a_silent_add_and_names_the_contractor() -> None:
    case = next(case for case in CASES if case.name == "silent-member-add")

    assert CONTRACTOR_EMAIL in case.message
    assert "don't notify" in case.message or "notify" in case.message.casefold()
    assert case.seed is _seed_absent_contractor


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _contractor_ids(workspace_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.email == CONTRACTOR_EMAIL,
                    )
                )
            ).scalars()
        )


async def test_seed_removes_the_contractor_an_earlier_run_added(db: None, tmp_path) -> None:
    """The second run of the default suites is the failure this seed answers: the member row is
    durable, so without the reset `add_member` refuses the address as already a member."""
    workspace_id = await _workspace()
    other_workspace_id = await _workspace()
    async with workspace_tx() as connection:
        await create_member(connection, workspace_id, CONTRACTOR_EMAIL)
        await create_member(connection, other_workspace_id, CONTRACTOR_EMAIL)

    with ws(workspace_id):
        await _seed_absent_contractor(workspace_id, uuid4(), FilesystemBlobStore(root=tmp_path))

    assert await _contractor_ids(workspace_id) == []
    assert len(await _contractor_ids(other_workspace_id)) == 1


async def test_seed_is_a_no_op_on_the_first_run(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seed_absent_contractor(workspace_id, uuid4(), FilesystemBlobStore(root=tmp_path))

    assert await _contractor_ids(workspace_id) == []


async def _portal_workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id = await _workspace()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        admin_id = await create_member(connection, workspace_id, "owner@evalco.test")
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == admin_id).values(is_admin=True)
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, admin_id, agent_id


async def _portal_chat_rows(workspace_id: UUID) -> list[tuple[str, UUID | None, str | None]]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.audience,
                    tables.conversation.c.member_id,
                    tables.conversation.c.title,
                ).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key == PORTAL_CHAT_KEY,
                )
            )
        ).all()
    return [(row.audience, row.member_id, row.title) for row in rows]


async def _portal_chats_awaiting_a_title(workspace_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.queue_key == PORTAL_CHAT_KEY,
                        awaiting_a_title(),
                    )
                )
            ).scalars()
        )


async def _portal_chat_speakers(workspace_id: UUID) -> list[UUID | None]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn.c.speaker_member_id)
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.queue_key == PORTAL_CHAT_KEY,
                        tables.turn.c.admission_source == "member",
                    )
                )
            ).scalars()
        )


async def test_the_portal_chat_seed_shapes_the_row_the_grader_reads(db: None, tmp_path) -> None:
    """The seed leaves exactly one titled portal chat under its key, spoken once by the eval
    speaker — the shape the narrowing act's sole-speaker gate and the kind's listing both need —
    replacing whatever an earlier run left; the titling job finds nothing to rename, so the
    conversation kind lists the chat under the title the case names; the grader answers off that
    row and nothing else, and the cleanup takes the row and its turn away."""
    workspace_id, admin_id, agent_id = await _portal_workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        await _seed_portal_chat(shared=True)(workspace_id, agent_id, blob)
        assert await _portal_chat_rows(workspace_id) == [
            (str(SHARED_AUDIENCE), None, PORTAL_CHAT_TITLE)
        ]
        assert await _portal_chat_speakers(workspace_id) == [admin_id]
        assert await _portal_chats_awaiting_a_title(workspace_id) == []
        listed = await ConversationDirectory(workspace_id).list(
            agent_id, admin_id, admin=False, limit=10, participation="mine", member_admitted=True
        )
        assert [(entry.title, entry.mine) for entry in listed] == [(PORTAL_CHAT_TITLE, True)]
        assert (await _portal_chat_audience_grader(shared=True)(_output())).passed
        still_shared = await _portal_chat_audience_grader(shared=False)(_output())
        assert not still_shared.passed
        assert "reads 'shared'" in still_shared.reason

        await _seed_portal_chat(shared=False)(workspace_id, agent_id, blob)
        assert await _portal_chat_rows(workspace_id) == [
            (str(conversation_audience(admin_id)), admin_id, PORTAL_CHAT_TITLE)
        ]
        assert await _portal_chat_speakers(workspace_id) == [admin_id]
        narrowed = await _portal_chat_audience_grader(shared=False)(_output())
        assert narrowed.passed
        assert narrowed.evidence == {
            "audience": str(conversation_audience(admin_id)),
            "member_id": str(admin_id),
            "narrowing_calls": 0,
            "widening_calls": 0,
        }
        assert not (await _portal_chat_audience_grader(shared=True)(_output())).passed

        await _cleanup_portal_chat(workspace_id, agent_id, blob)
        assert await _portal_chat_rows(workspace_id) == []
        assert await _portal_chat_speakers(workspace_id) == []


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("listing a conversation spawns nothing")


async def _eval_turn_context(
    workspace_id: UUID, agent_id: UUID, speaker: UUID, blob: WorkspaceBlobStore, tmp_path
) -> ToolContext:
    conversation_id = uuid4()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=conversation_id,
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    return ToolContext(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound=f'Keep my "{PORTAL_CHAT_TITLE}" chat with you between us.',
            created_at=datetime.now(UTC),
            admission_source=MEMBER_ADMISSION,
            speaker_member_id=speaker,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker,
        audience=SHARED_AUDIENCE,
        artifact_token_secret="eval-test-secret",
    )


async def test_object_list_finds_the_seeded_portal_chat_by_its_title(db: None, tmp_path) -> None:
    """The read the case's model makes: `object_list` on the conversation kind from the eval
    speaker's turn, searching the title the ask names. The seeded chat answers it whether the
    portal's shared shape or the member's private one, titled on the row, listed unsearched too,
    and its status counts the exchange the seed wrote; the cleanup takes the transcript with the
    row."""
    workspace_id, admin_id, agent_id = await _portal_workspace()
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    fields = CONVERSATION_OBJECT.list_fields
    by_title = ObjectListQuery(query=PORTAL_CHAT_TITLE, supported_fields=fields)
    unsearched = ObjectListQuery(supported_fields=fields)
    with ws(workspace_id):
        ctx = await _eval_turn_context(workspace_id, agent_id, admin_id, blob, tmp_path)
        for shared in (True, False):
            await _seed_portal_chat(shared=shared)(workspace_id, agent_id, blob)
            with agent(agent_id):
                found = await CONVERSATION_OBJECT.store.list(ctx, by_title)
                listed = await CONVERSATION_OBJECT.store.list(ctx, unsearched)
                (row,) = found.rows
                status = await CONVERSATION_OBJECT.store.status(
                    ctx, row.name, expected_generation=None
                )
            assert row.summary == PORTAL_CHAT_TITLE
            assert row.fields["title"] == PORTAL_CHAT_TITLE
            assert row.fields["surface"] == PORTAL_SURFACE
            assert row.name in {listed_row.name for listed_row in listed.rows}
            assert status is not None
            assert status["messages"] == 2
        await _cleanup_portal_chat(workspace_id, agent_id, blob)
        with agent(agent_id):
            assert (await CONVERSATION_OBJECT.store.list(ctx, by_title)).rows == ()
        assert not await blob.exists(transcript_key(UUID(row.name)))


def test_the_visibility_cases_name_the_chat_and_never_the_act() -> None:
    by_name = {case.name: case for case in CASES}
    for name in (
        "authored-portal-chat-made-private",
        "authored-portal-chat-shared",
        "authored-private-portal-chat-stays-put",
    ):
        case = by_name[name]
        assert PORTAL_CHAT_TITLE in case.message
        assert "make_conversation_private" not in case.message
        assert "share_conversation" not in case.message
        assert case.cleanup is _cleanup_portal_chat
