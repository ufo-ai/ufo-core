"""Scenario cases over the deterministic eval environment: the agent reaches a seeded fake mailbox
and calendar only through the real connector dispatch, and graders read the extension's own tables
back after the conversation. Each case's seed resets the environment and re-records the grants, so
a rerun never inherits a prior run's rows. Requires a deploy on the `assistant_eval` pack — the
suite is selected explicitly (`--only scenario_env`), never by default."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_eval_env.manifest import (
    ACCOUNT_ID,
    CALENDAR_HOST,
    CALENDAR_PROVIDER,
    CANCELLED,
    CONFIRMED,
    EMAIL_HOST,
    EMAIL_PROVIDER,
    eval_env_email,
    eval_env_event,
)

from evals.driver import EVAL_SURFACE
from evals.harness.capability import CapabilityVerdict
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from ufo.db import workspace_tx
from ufo.grants import GrantStore
from ufo.schema import tables
from ufo.workspace import ws_current

DANA = "dana@evalco.test"
BOB = "bob@evalco.test"
MARA = "mara@evalco.test"
MEMBER = "member@evalco.test"
SYNC_TITLE = "Design sync"
SYNC_OLD_START = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
SYNC_OLD_END = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
SYNC_NEW_DATE = datetime(2026, 7, 24, tzinfo=UTC).date()


async def _reset_environment(workspace_id: UUID, agent_id: UUID) -> None:
    """Wipe the environment's rows and (re)record both provider grants for the target agent — the
    grant rows a real connect would have written, seeded through the same store."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(eval_env_email).where(eval_env_email.c.workspace_id == workspace_id)
        )
        await connection.execute(
            sa.delete(eval_env_event).where(eval_env_event.c.workspace_id == workspace_id)
        )
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id)
                .where(tables.member.c.workspace_id == workspace_id)
                .order_by(tables.member.c.created_at)
                .limit(1)
            )
        ).scalar_one()
        conversation_id = uuid4()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface=EVAL_SURFACE,
                queue_key=f"{EVAL_SURFACE}-seed:{conversation_id}",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    grants = GrantStore()
    for provider, host in (
        (EMAIL_PROVIDER, EMAIL_HOST),
        (CALENDAR_PROVIDER, CALENDAR_HOST),
    ):
        await grants.record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=provider,
            account_id=ACCOUNT_ID,
            host=host,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )


async def _seed_inbox(workspace_id: UUID, agent_id: UUID) -> None:
    await _reset_environment(workspace_id, agent_id)
    emails = (
        (
            DANA,
            "Budget planning",
            "Quick update: the Q3 budget was approved for Project Bluefin. Kickoff next month.",
            datetime(2026, 7, 14, 9, 30, tzinfo=UTC),
        ),
        (
            BOB,
            "Lunch?",
            "Want to grab lunch tomorrow?",
            datetime(2026, 7, 14, 11, 5, tzinfo=UTC),
        ),
        (
            MARA,
            "Design files",
            "The new mockups are in the shared folder.",
            datetime(2026, 7, 15, 8, 12, tzinfo=UTC),
        ),
    )
    async with workspace_tx() as connection:
        for sender, subject, body, sent_at in emails:
            await connection.execute(
                sa.insert(eval_env_email).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    folder="inbox",
                    sender=sender,
                    recipients=[MEMBER],
                    subject=subject,
                    body=body,
                    sent_at=sent_at,
                )
            )


async def _seed_calendar(workspace_id: UUID, agent_id: UUID) -> None:
    await _reset_environment(workspace_id, agent_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(eval_env_event).values(
                id=uuid4(),
                workspace_id=workspace_id,
                title=SYNC_TITLE,
                start_at=SYNC_OLD_START,
                end_at=SYNC_OLD_END,
                attendees=[MARA],
                status=CONFIRMED,
            )
        )


async def _sent_rows(workspace_id: UUID) -> tuple[sa.Row, ...]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(eval_env_email).where(
                    eval_env_email.c.workspace_id == workspace_id,
                    eval_env_email.c.folder == "sent",
                )
            )
        ).all()
    return tuple(rows)


async def _graded_inbox_lookup(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if not any(
        call.name == "call_external_tool" and call.succeeded for call in outcome.output.calls
    ):
        return CapabilityVerdict(False, "agent never dispatched call_external_tool")
    if "bluefin" not in "\n".join(outcome.replies).lower():
        return CapabilityVerdict(False, "agent never named Project Bluefin")
    sent = await _sent_rows(ws_current().workspace_id)
    if sent:
        return CapabilityVerdict(False, f"a read-only lookup sent {len(sent)} email(s)")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "found Bluefin in the seeded inbox without writing")


async def _graded_send_confirmation(outcome: ScenarioOutcome) -> CapabilityVerdict:
    sent = await _sent_rows(ws_current().workspace_id)
    if len(sent) != 1:
        return CapabilityVerdict(False, f"expected exactly one sent email, found {len(sent)}")
    email = sent[0]
    if BOB not in tuple(email.recipients):
        return CapabilityVerdict(False, f"email went to {email.recipients}, not {BOB}")
    text = f"{email.subject}\n{email.body}".lower()
    if "friday" not in text:
        return CapabilityVerdict(False, "the email never mentions Friday")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "one email to Bob confirming Friday")


async def _graded_reschedule(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    async with workspace_tx() as connection:
        events = (
            await connection.execute(
                sa.select(eval_env_event).where(
                    eval_env_event.c.workspace_id == workspace_id,
                    eval_env_event.c.title.ilike(f"%{SYNC_TITLE}%"),
                    eval_env_event.c.status != CANCELLED,
                )
            )
        ).all()
    on_new_date = [event for event in events if event.start_at.date() == SYNC_NEW_DATE]
    still_old = [event for event in events if event.start_at.date() == SYNC_OLD_START.date()]
    if not on_new_date:
        return CapabilityVerdict(False, "no live Design sync lands on July 24")
    if still_old:
        return CapabilityVerdict(False, "the original Thursday Design sync is still live")
    sent = await _sent_rows(workspace_id)
    to_mara = [email for email in sent if MARA in tuple(email.recipients)]
    if len(to_mara) != 1:
        return CapabilityVerdict(False, f"expected exactly one email to Mara, found {len(to_mara)}")
    text = f"{to_mara[0].subject}\n{to_mara[0].body}".lower()
    if "friday" not in text and "july 24" not in text:
        return CapabilityVerdict(False, "Mara's email never mentions the new day")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "Design sync moved to July 24 and Mara notified once")


CASES = (
    ScenarioCase(
        "inbox-lookup",
        ScenarioUser(
            reason_for_call="You want to know which project Dana's recent email said was approved.",
            known_info="Dana emailed you in the last few days about the budget.",
            unknown_info="You do not remember the project name — that is why you are asking.",
            task_instructions=(
                "Ask the assistant to check your email for Dana's message about the budget and "
                "tell you which project was approved. Once it names the project, you are "
                "satisfied."
            ),
        ),
        _graded_inbox_lookup,
        max_turns=5,
        digest_tag="scenario-env:inbox-lookup",
        seed=_seed_inbox,
    ),
    ScenarioCase(
        "send-confirmation",
        ScenarioUser(
            reason_for_call="You want the assistant to email Bob confirming dinner plans.",
            known_info=(
                "Bob's address is bob@evalco.test. Dinner is this Friday at 7pm at Luigi's."
            ),
            task_instructions=(
                "Ask the assistant to send Bob a short email confirming dinner on Friday at 7pm "
                "at Luigi's. Answer any questions from what you know. Once it confirms the email "
                "was sent, you are satisfied."
            ),
        ),
        _graded_send_confirmation,
        max_turns=6,
        digest_tag="scenario-env:send-confirmation",
        seed=_reset_environment,
    ),
    ScenarioCase(
        "reschedule-and-notify",
        ScenarioUser(
            reason_for_call=(
                "You need your Design sync moved to a new time and the attendee told about it."
            ),
            known_info=(
                "The Design sync is currently on Thursday July 23 at 2pm. Mara "
                "(mara@evalco.test) is the attendee. It must move to Friday July 24 at 3pm, "
                "still one hour long."
            ),
            task_instructions=(
                "Ask the assistant to move the Design sync to Friday July 24 at 3pm and to email "
                "Mara letting her know the new time. Answer questions from what you know. Once "
                "both are done, you are satisfied."
            ),
        ),
        _graded_reschedule,
        max_turns=8,
        digest_tag="scenario-env:reschedule-and-notify",
        seed=_seed_calendar,
    ),
)
