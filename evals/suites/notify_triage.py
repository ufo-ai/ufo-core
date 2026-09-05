"""Triage discipline: of a batch of notifications raised for one member, the Notification app
delivers what changes what they do today, in one message, and nothing else.

The producer suite (`notify_raise`) keeps noise out of the inbox; this one keeps what got in from
reaching the member. The member is a startup founder with Gmail, GitHub, and Stripe connected.
Three batches: twelve rows of which three merit a push (a churn spike taking the two largest
accounts, a deploy that failed on main and rolled back, an investor's data-room deadline going
unanswered) beside nine routine ones (green runs, dependabot merges, receipts, newsletters, a small
refund, a payout that landed as usual); nine routine rows alone; and one urgent row — production
down after a rollout — among eight routine ones. Pass is one `deliver` naming exactly the rows that
merit it and no `notify` (the app cannot raise a notification about its own batch; the allowlist
already withholds the tool, so the grader's check is a second reading of the same fence), or no
`deliver` at all on the routine batch.

The seed writes the rows the batch names, for the workspace's member, so `deliver` finds them; the
message is the drain's own rendering of that batch. Deterministic graders and one sample per case:
one push in three of a routine batch is exactly the noise this suite exists to catch."""

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
from ufo_ext_app_notification.deliver import DELIVER_ACTION_ID
from ufo_ext_app_notification.drain import drain_message
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME, NOTIFY_TOOL_NAME
from ufo_ext_app_notification.store import NOTIFICATION_KIND, Notification
from ufo_ext_app_notification.store import notification as notification_table

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    Grader,
)
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

NOTIFY_TRIAGE_NAMESPACE = UUID("6e0a4d1c-3b7f-4f52-9a8e-2c1d0b9f7e31")
RAISED_AT = datetime(2026, 9, 4, 6, 0, tzinfo=UTC)
PRODUCER = "assistant"

Entry = tuple[str, str, int]

CHURN: Entry = (
    "stripe/subscriptions-week-36",
    "Six subscriptions canceled this week against a four-week baseline of one: Northwind Robotics "
    "and Marrow & Finch are the two largest accounts, $3,500 of the $6,270 MRR that left.",
    1,
)
DEPLOY: Entry = (
    "github/deploy-8f21c0d",
    "The deploy of main (#412 billing rework) failed its readiness probe and rolled back; "
    "production is serving the previous release and the billing rework is not live.",
    1,
)
INVESTOR: Entry = (
    "gmail/thread-corvid-ic",
    "Priya at Corvid Capital asked on Aug 31 for the data room and August metrics before "
    "Thursday's IC; the thread is unanswered and Thursday is tomorrow.",
    1,
)
OUTAGE: Entry = (
    "github/rollout-8f21c0d",
    "Production has returned 502 on every request since the 14:07 rollout of 8f21c0d; the rollback "
    "job also failed and no release is serving.",
    2,
)
ROUTINE: tuple[Entry, ...] = (
    ("source/github", "Nightly: ci and deploy green on main twice; dependabot merged 3 PRs.", 1),
    ("source/gmail", "Twelve newsletters and two SaaS receipts arrived; nothing needs a reply.", 1),
    (
        "source/stripe",
        "One $12 starter refund at the customer's request; payout landed as usual.",
        1,
    ),
    (
        "scheduled_task/weekly-metrics",
        "Ran clean and posted the usual table to its conversation.",
        1,
    ),
    ("page/opportunity-0412", "Opportunity 0412 updated: last_modified timestamp moved.", 1),
    ("page/opportunity-0413", "Opportunity 0413 updated: last_modified timestamp moved.", 1),
    ("source/calendar", "Three meetings added for next week, all recurring standups.", 1),
    ("scheduled_task/inbox-sweep", "Swept 40 newsletters into the archive label as configured.", 1),
    ("page/readme", "README changed: a typo fix in the install section.", 1),
)


def _row(
    member_id: UUID, agent_id: UUID, subject: str, body: str, occurrences: int
) -> Notification:
    return Notification(
        id=uuid5(NOTIFY_TRIAGE_NAMESPACE, f"{subject}:{body}"),
        to_agent_id=agent_id,
        member_id=member_id,
        subject=subject,
        body=body,
        occurrences=occurrences,
        produced_by_agent_id=agent_id,
        produced_by_agent_name=PRODUCER,
        produced_by_turn_id=uuid5(NOTIFY_TRIAGE_NAMESPACE, f"turn:{subject}:{body}"),
        produced_in_conversation_id=uuid5(NOTIFY_TRIAGE_NAMESPACE, f"conversation:{subject}"),
        triaged_turn_id=None,
        triaged_at=None,
        delivered_turn_id=None,
        delivered_surface=None,
        last_raised_at=RAISED_AT,
        created_at=RAISED_AT,
        updated_at=RAISED_AT,
    )


def _ref(entry: Entry) -> str:
    subject, body, occurrences = entry
    return f"{NOTIFICATION_KIND}/{_row(uuid4(), uuid4(), subject, body, occurrences).name}"


async def _member_id() -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.id)
                .where(tables.member.c.workspace_id == ws_current().workspace_id)
                .order_by(tables.member.c.created_at, tables.member.c.id)
                .limit(1)
            )
        ).scalar_one()


def _seed_batch(entries: tuple[Entry, ...]) -> CapabilitySeed:
    """Replace the member's inbox with exactly these rows, so the refs the message names are the
    rows `deliver` finds and nothing from an earlier case stands beside them. Every subject in a
    batch is distinct, as the fold makes it in production: one open row per subject per lane is
    what the table's unique index holds, so a batch naming a subject twice could never exist."""
    subjects = [subject for subject, _, _ in entries]
    if len(set(subjects)) != len(subjects):
        raise ValueError(f"a batch names a subject twice: {sorted(subjects)}")

    async def seed(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        member_id = await _member_id()
        rows = tuple(
            _row(member_id, agent_id, subject, body, count) for subject, body, count in entries
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(notification_table).where(
                    notification_table.c.workspace_id == ws_current().workspace_id
                )
            )
            for row in rows:
                await connection.execute(
                    sa.insert(notification_table).values(
                        id=row.id,
                        workspace_id=ws_current().workspace_id,
                        to_agent_id=row.to_agent_id,
                        member_id=row.member_id,
                        subject=row.subject,
                        body=row.body,
                        occurrences=row.occurrences,
                        produced_by_agent_id=row.produced_by_agent_id,
                        produced_by_agent_name=row.produced_by_agent_name,
                        produced_by_turn_id=row.produced_by_turn_id,
                        produced_in_conversation_id=row.produced_in_conversation_id,
                        last_raised_at=row.last_raised_at,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                )

    return seed


def _message(entries: tuple[Entry, ...]) -> str:
    """The drain's rendering of the batch. Row ids derive from subject and body alone, so the
    message a case is built with names the rows its seed writes, whatever member the workspace
    holds."""
    placeholder = uuid4()
    rows = tuple(
        _row(placeholder, placeholder, subject, body, count) for subject, body, count in entries
    )
    return drain_message(rows)


def _delivers_exactly(merit: tuple[Entry, ...]) -> Grader:
    wanted = {_ref(entry) for entry in merit}

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if NOTIFY_TOOL_NAME in output.tools:
            return CapabilityVerdict(False, f"the app called {NOTIFY_TOOL_NAME} on its own batch")
        deliveries = [call for call in output.calls if call.call == DELIVER_ACTION_ID]
        if not wanted:
            if deliveries:
                return CapabilityVerdict(
                    False, f"delivered {len(deliveries)} time(s) on a routine batch"
                )
            return CapabilityVerdict(True, "delivered nothing")
        if len(deliveries) != 1:
            return CapabilityVerdict(
                False, f"{DELIVER_ACTION_ID} was called {len(deliveries)} times, not once"
            )
        refs = deliveries[0].arguments.get("refs")
        if not isinstance(refs, list):
            return CapabilityVerdict(False, "deliver carried no refs")
        named = {str(ref) for ref in refs}
        if named != wanted:
            return CapabilityVerdict(False, f"delivered {sorted(named)}, wanted {sorted(wanted)}")
        return CapabilityVerdict(True, f"delivered exactly {sorted(entry[0] for entry in merit)}")

    return grade


CASES = (
    CapabilityCase(
        name="three-of-twelve-merit-a-push",
        message=_message((CHURN, DEPLOY, INVESTOR, *ROUTINE)),
        grader=_delivers_exactly((CHURN, DEPLOY, INVESTOR)),
        seed=_seed_batch((CHURN, DEPLOY, INVESTOR, *ROUTINE)),
        digest_tag="notify-triage:three-of-twelve",
    ),
    CapabilityCase(
        name="a-routine-batch-delivers-nothing",
        message=_message(ROUTINE),
        grader=_delivers_exactly(()),
        seed=_seed_batch(ROUTINE),
        digest_tag="notify-triage:routine-batch",
    ),
    CapabilityCase(
        name="one-outage-among-routine",
        message=_message((*ROUTINE[:4], OUTAGE, *ROUTINE[4:])),
        grader=_delivers_exactly((OUTAGE,)),
        seed=_seed_batch((*ROUTINE, OUTAGE)),
        digest_tag="notify-triage:one-outage",
    ),
)

AGENT = NOTIFICATION_AGENT_NAME
