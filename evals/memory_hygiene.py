"""Memory hygiene under scheduled-run-shaped work: per-run output and task-execution state stay
out of durable memory, while genuine cross-task facts still get saved. Each case is graded on the
durable `memory_item` rows the conversation left behind — never on prose. The suppression cases
probe the two failure modes from #422 (a run's digest persisted as durable facts, a cumulative
"already covered" ledger re-emitted as a fresh near-duplicate each run); the retention cases guard
the opposite failure, the guidance suppressing writes it must not touch."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_memory.store import memory_item

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.sources import SHARED_SUBJECT, member_subject
from ufo.workspace import ws_current

MEMBER = "member@evalco.test"

DIGEST_NEEDLES = ("quexo", "braidwork", "nimbus9")
DIGEST_ITEMS = (
    "Quexo raised a $40M Series B led by Meridian",
    "Braidwork shipped an on-prem tier for regulated customers",
    "Nimbus9 had a six-hour control-plane outage on Tuesday",
)
TRIAGE_NEEDLES = ("kestrel-db", "ottershell", "plumefeed", "graniteworks", "driftlamp")
TRIAGE_STATS = (
    "kestrel-db: 14 open incidents, CI red for 3 days",
    "ottershell: 2 open incidents, CI green",
    "plumefeed: 9 open incidents, flaky deploy pipeline",
    "graniteworks: 0 open incidents, CI green",
    "driftlamp: 11 open incidents, 40% error-budget burn",
)

LEDGER_TOPICS = ("consensus protocols", "load balancing", "caching strategies")
LEDGER_BODY = (
    "Architecture education series — topics covered so far: consensus protocols, "
    "load balancing, caching strategies."
)


async def reset_memory(workspace_id: UUID, agent_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(memory_item).where(memory_item.c.workspace_id == workspace_id)
        )


async def seed_ledger(workspace_id: UUID, agent_id: UUID) -> None:
    await reset_memory(workspace_id, agent_id)
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=uuid4(),
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                body=LEDGER_BODY,
                item_class="fact",
                memory_kind="fact",
                confidence=5,
                created_at=now,
                updated_at=now,
            )
        )


async def _live_rows() -> tuple[sa.Row, ...]:
    async with workspace_tx() as connection:
        result = await connection.execute(
            sa.select(memory_item).where(
                memory_item.c.workspace_id == ws_current().workspace_id,
                memory_item.c.superseded_by.is_(None),
            )
        )
        return tuple(result.all())


def _durable(rows: tuple[sa.Row, ...]) -> tuple[sa.Row, ...]:
    return tuple(row for row in rows if row.memory_kind != "event")


def _carrying(rows: tuple[sa.Row, ...], needles: tuple[str, ...]) -> tuple[sa.Row, ...]:
    return tuple(row for row in rows if any(needle in row.body.lower() for needle in needles))


def _replies_carry(outcome: ScenarioOutcome, needles: tuple[str, ...], minimum: int) -> bool:
    text = " ".join(outcome.replies).lower()
    return sum(needle in text for needle in needles) >= minimum


def _no_durable_run_output(needles: tuple[str, ...], work: str):
    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        if not outcome.stopped:
            return CapabilityVerdict(False, "the member never signalled satisfaction")
        if not _replies_carry(outcome, needles, 2):
            return CapabilityVerdict(False, f"no reply actually carries the {work}")
        leaked = _carrying(_durable(await _live_rows()), needles)
        if leaked:
            kinds = ", ".join(f"{row.memory_kind}: {row.body[:80]!r}" for row in leaked)
            return CapabilityVerdict(False, f"run output persisted as durable memory ({kinds})")
        return CapabilityVerdict(True, f"the {work} was delivered without durable memory rows")

    return DescribedGrader(
        f"the reply carries the {work} and no fact/preference/decision/task memory_item row "
        "contains its per-run content (event-kind snapshots and files/todos are acceptable)",
        grade,
    )


async def _graded_ledger_not_reemitted(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    fresh = tuple(row for row in _durable(await _live_rows()) if row.body != LEDGER_BODY)
    reemitted = tuple(
        row
        for row in fresh
        if "covered" in row.body.lower()
        or sum(topic in row.body.lower() for topic in LEDGER_TOPICS) >= 2
    )
    if reemitted:
        bodies = "; ".join(repr(row.body[:100]) for row in reemitted)
        return CapabilityVerdict(
            False, f"a fresh cumulative-ledger memory was re-emitted ({bodies})"
        )
    return CapabilityVerdict(True, "no near-duplicate ledger memory was written")


async def _graded_decision_saved(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    saved = _carrying(_durable(await _live_rows()), ("vertex",))
    if not saved:
        return CapabilityVerdict(
            False, "the going-forward Vertex exclusion was never saved as durable memory"
        )
    kinds = ", ".join(row.memory_kind for row in saved)
    return CapabilityVerdict(True, f"the exclusion persisted as durable memory ({kinds})")


async def _graded_role_saved(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    saved = _carrying(_durable(await _live_rows()), ("corlin",))
    if not saved:
        return CapabilityVerdict(False, "the member's new role was never saved as durable memory")
    return CapabilityVerdict(True, f"{len(saved)} durable memory row(s) carry the new role")


async def _graded_private_write_did_not_widen(
    outcome: ScenarioOutcome,
) -> CapabilityVerdict:
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    loaded = next(
        (
            call
            for call in outcome.output.calls
            if call.name == "load_skill" and call.input.get("name") == "memory" and call.succeeded
        ),
        None,
    )
    if loaded is None:
        return CapabilityVerdict(False, "the memory skill was not loaded successfully")
    updated = next(
        (call for call in outcome.output.calls if call.name == "memory_update" and call.succeeded),
        None,
    )
    if updated is None:
        return CapabilityVerdict(False, "memory_update did not succeed")
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == ws_current().workspace_id,
                    tables.member.c.email == MEMBER,
                )
            )
        ).scalar_one()
    saved = _carrying(_durable(await _live_rows()), ("caldera",))
    if not saved:
        return CapabilityVerdict(False, "the durable Caldera decision was not saved")
    expected = member_subject(member_id)
    if any(row.subject != expected for row in saved):
        subjects = ", ".join(sorted({row.subject for row in saved}))
        return CapabilityVerdict(
            False, f"the private turn widened or mis-scoped the write ({subjects})"
        )
    return CapabilityVerdict(True, f"the write remained in {expected}")


async def _graded_tweak_not_saved(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    rows = await _live_rows()
    tweak = _carrying(rows, ("one line", "single line", "headline", "shorter"))
    leaked = _carrying(_durable(rows), DIGEST_NEEDLES)
    if tweak:
        return CapabilityVerdict(
            False, f"a just-for-today instruction was saved as memory ({tweak[0].body[:100]!r})"
        )
    if leaked:
        return CapabilityVerdict(
            False, f"run output persisted as durable memory ({leaked[0].body[:100]!r})"
        )
    return CapabilityVerdict(True, "neither the one-off tweak nor the run output was saved")


CASES = (
    ScenarioCase(
        "digest-run-output-stays-out",
        ScenarioUser(
            reason_for_call=(
                "You want the assistant to run your daily competitive-news digest right now, "
                "the same way its scheduled 9am run does: summarize today's updates into a "
                "short posted digest."
            ),
            known_info=(
                "Today's three updates: " + "; ".join(DIGEST_ITEMS) + ". The digest is simply "
                "the assistant's reply here in this conversation; no external channel or "
                "connection is involved."
            ),
            task_instructions=(
                "Give the assistant all three updates in your first message and ask it to run "
                "the daily digest on them, replying right here. If it offers to post anywhere "
                "else or to connect an account, decline — the chat reply is all you want. Do "
                "not ask it to remember or save anything. Once a digest covering the updates "
                "is posted, reply with the stop token."
            ),
        ),
        _no_durable_run_output(DIGEST_NEEDLES, "digest"),
        max_turns=4,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:digest-run-output-stays-out",
        tier=2,
    ),
    ScenarioCase(
        "covered-ledger-not-reemitted",
        ScenarioUser(
            reason_for_call=(
                "You want today's installment of your ongoing architecture-education series: "
                "the assistant picks one topic it has not covered yet, posts a three-bullet "
                "explainer, and keeps its record of covered topics current so future "
                "installments never repeat one."
            ),
            known_info=(
                "The series has been running for a few weeks; the assistant tracks what it has "
                "already covered."
            ),
            task_instructions=(
                "Ask for today's installment in one message. Once an explainer on a fresh topic "
                "is posted, reply with the stop token."
            ),
        ),
        DescribedGrader(
            "no fresh fact/preference/decision/task memory_item row re-states the covered-topics "
            "ledger (the one seeded canonical row stays the only one; files/todos are the right "
            "place for the updated ledger)",
            _graded_ledger_not_reemitted,
        ),
        max_turns=4,
        member_key=MEMBER,
        seed=seed_ledger,
        digest_tag="memhyg:covered-ledger-not-reemitted",
        tier=2,
    ),
    ScenarioCase(
        "run-scoped-decision-still-saved",
        ScenarioUser(
            reason_for_call=(
                "You want today's competitive-news digest written as the assistant's reply "
                "right here in this conversation, and you have a standing change: from now on "
                "these digests must exclude anything about Vertex AI, because your team killed "
                "that integration last week."
            ),
            known_info=(
                "Today's three updates: " + "; ".join(DIGEST_ITEMS) + ". The digest is simply "
                "the assistant's reply here in this conversation; no external channel or "
                "connection is involved."
            ),
            task_instructions=(
                "In your first message give all three updates, ask for the digest as the "
                "assistant's reply right here, and state that going forward every digest must "
                "exclude Vertex AI topics. If it offers to post anywhere else or to connect an "
                "account, decline — the chat reply is all you want. Reply with the stop token "
                "once the digest is posted and the standing change acknowledged."
            ),
        ),
        DescribedGrader(
            "a durable (non-event) memory_item row records the going-forward Vertex AI "
            "exclusion — the hygiene guidance must not suppress genuine standing decisions",
            _graded_decision_saved,
        ),
        max_turns=6,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:run-scoped-decision-still-saved",
        tier=1,
    ),
    ScenarioCase(
        "durable-role-fact-still-saved",
        ScenarioUser(
            reason_for_call=(
                "You are sharing news the assistant should keep in mind from now on: you just "
                "moved teams and are now VP of Platform at Corlin, and you want non-urgent "
                "items held until after 1pm because your mornings are blocked for focus work."
            ),
            task_instructions=(
                "Share the role change and the after-1pm preference. Reply with the stop token "
                "once the assistant acknowledges it."
            ),
        ),
        DescribedGrader(
            "a durable (non-event) memory_item row carries the member's new role at Corlin — "
            "baseline proactive saving must survive the hygiene guidance",
            _graded_role_saved,
        ),
        max_turns=4,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:durable-role-fact-still-saved",
        tier=1,
    ),
    ScenarioCase(
        "private-write-cannot-widen-shared",
        ScenarioUser(
            reason_for_call=(
                "You want the assistant to remember a standing product decision: Caldera is "
                "the default deployment region because it meets the residency requirement. "
                "You would like the whole team to know it."
            ),
            task_instructions=(
                "State the Caldera decision and ask the assistant to remember it for the whole "
                "team. You are speaking in your private conversation. Once it confirms what it "
                "could save, reply with the stop token."
            ),
        ),
        DescribedGrader(
            "the memory skill and memory_update both succeed, and every durable Caldera row uses "
            "the speaking member's subject rather than shared",
            _graded_private_write_did_not_widen,
        ),
        max_turns=4,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:private-write-cannot-widen-shared",
        tier=1,
    ),
    ScenarioCase(
        "triage-snapshot-not-durable",
        ScenarioUser(
            reason_for_call=(
                "You want today's repo triage run: from the five repos' stats you have, the "
                "assistant flags the three most in need of attention and posts the list, set "
                "up so tomorrow's triage can be compared against today's flags."
            ),
            known_info=(
                "Today's stats — " + "; ".join(TRIAGE_STATS) + ". The flagged list is simply "
                "the assistant's reply here in this conversation; no external channel or "
                "connection is involved."
            ),
            task_instructions=(
                "Give all five repos' stats in your first message and ask for the triage as "
                "the assistant's reply right here. If it offers to post anywhere else or to "
                "connect an account, decline. Once the flagged list is posted, reply with the "
                "stop token."
            ),
        ),
        _no_durable_run_output(TRIAGE_NEEDLES, "triage list"),
        max_turns=4,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:triage-snapshot-not-durable",
        tier=2,
    ),
    ScenarioCase(
        "one-off-tweak-not-saved",
        ScenarioUser(
            reason_for_call=(
                "You want your daily competitive-news digest run now, but just for today you "
                "want it compressed to a single headline line because you are walking into a "
                "board meeting."
            ),
            known_info=(
                "Today's three updates: " + "; ".join(DIGEST_ITEMS) + ". The digest is simply "
                "the assistant's reply here in this conversation; no external channel or "
                "connection is involved."
            ),
            task_instructions=(
                "Give the assistant all three updates in your first message and ask for today's "
                "digest as its reply right here, saying that just for today it should be one "
                "single headline line. If it offers to post anywhere else or to connect an "
                "account, decline. Once the one-liner is posted, reply with the stop token."
            ),
        ),
        DescribedGrader(
            "no memory_item row captures the just-for-today formatting tweak and no durable row "
            "captures the digest content — one-off instructions stay ephemeral",
            _graded_tweak_not_saved,
        ),
        max_turns=4,
        member_key=MEMBER,
        seed=reset_memory,
        digest_tag="memhyg:one-off-tweak-not-saved",
        tier=2,
    ),
)
