"""What a member asks for in chat reaches the one place suppression is applied, and reaches
nothing else: product email stops, and a notice about the workspace's credit does not.

The gate itself is control's, proved by `servers/control/tests/email_send_it.rs`; what is asserted
here is that the tool records the speaker's own address under the topic a member may silence, and
that every sequence sends under that topic while the balance notice does not."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_lifecycle_email.balance_notice import BALANCE_EXHAUSTED, BalanceNotice
from ufo_ext_lifecycle_email.enrollments import Enrollments
from ufo_ext_lifecycle_email.preference import (
    RESUMED,
    SILENCED,
    UNRESOLVED,
    ProductEmailInput,
    product_email_handler,
)
from ufo_ext_lifecycle_email.runner import Reconciling, SequenceRunner
from ufo_ext_lifecycle_email.sequences import CONNECT_SOMETHING_KIND, NOTHING_CONNECTED
from ufo_testsupport.lifecycle_email import Gateway, context

from ufo.db import workspace_tx
from ufo.runtime.billing.balance import credit
from ufo.runtime.email import PRODUCT_NEWS, TRANSACTIONAL
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.tools import SpeakerRequired

pytestmark = pytest.mark.usefixtures("db")

RESERVE = 5_000_000


async def _seed() -> tuple[UUID, UUID]:
    workspace_id, member_id = uuid4(), uuid4()
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
                email="sam@acme.com",
                is_admin=True,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id


def _tool_context(ext: ExtensionContext, workspace_id: UUID, member_id: UUID | None) -> ToolContext:
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="running",
            inbound="stop emailing me",
            created_at=datetime(2026, 9, 10, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,  # type: ignore[arg-type]
        speaker_member_id=member_id,
        audience=SHARED_AUDIENCE,
        artifact_token_secret="",
        ext=ext,
    )


async def _said(
    ext: ExtensionContext, workspace_id: UUID, member_id: UUID | None, *, receiving: bool
) -> str:
    answered = await product_email_handler(
        _tool_context(ext, workspace_id, member_id), ProductEmailInput(receiving=receiving)
    )
    return "".join(block.text for block in answered.content)  # type: ignore[union-attr]


async def test_a_member_silences_product_email_for_their_own_address() -> None:
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    ext = context(gateway)

    with ws(workspace_id):
        assert await _said(ext, workspace_id, member_id, receiving=False) == SILENCED
        assert await _said(ext, workspace_id, member_id, receiving=True) == RESUMED

    assert gateway.preferences == [
        {"email": "sam@acme.com", "topic": PRODUCT_NEWS, "silenced": True},
        {"email": "sam@acme.com", "topic": PRODUCT_NEWS, "silenced": False},
    ], "the speaker's own address, under the one topic a member may silence"


async def test_a_call_with_no_speaker_asks_for_one_rather_than_refusing_outright() -> None:
    """`SpeakerRequired` is what tells the engine to name the active members, so the model can
    resubmit the same call carrying `requested_by`. A refusal of our own is a dead end: a member
    speaking where the speaker is not resolved could never set their own preference."""
    workspace_id, _ = await _seed()
    gateway = Gateway()
    ext = context(gateway)

    with ws(workspace_id), pytest.raises(SpeakerRequired):
        await product_email_handler(
            _tool_context(ext, workspace_id, None), ProductEmailInput(receiving=False)
        )

    assert gateway.preferences == []


async def test_a_speaker_who_is_not_a_member_here_changes_nothing() -> None:
    workspace_id, _ = await _seed()
    gateway = Gateway()
    ext = context(gateway)

    with ws(workspace_id):
        stranger = await _said(ext, workspace_id, uuid4(), receiving=False)

    assert stranger == UNRESOLVED
    assert gateway.preferences == []


async def test_a_sequence_sends_as_product_news_and_the_balance_notice_does_not() -> None:
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    async with workspace_tx() as connection:
        await credit(connection, workspace_id, 10_000_000, 10_000_000, "first")
        await connection.execute(
            sa.update(tables.workspace_balance)
            .where(tables.workspace_balance.c.workspace_id == workspace_id)
            .values(reserve_micro_usd=RESERVE, balance_micro_usd=0)
        )

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
        await Enrollments(ctx=ctx).record(NOTHING_CONNECTED, member_id, datetime.now(UTC))
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    topics = {message["kind"]: message["topic"] for message in gateway.sent}
    assert topics == {
        BALANCE_EXHAUSTED: TRANSACTIONAL,
        CONNECT_SOMETHING_KIND: PRODUCT_NEWS,
    }, "what the workspace is doing with their money is not theirs to silence"
