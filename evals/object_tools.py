"""Object-verb cases: the agent drives durable objects through the five generic tools.

`CASES` are single-turn capability probes on a shared workspace, each grader scoped to its own
subject so no case depends on another's leftovers. Scheduled-task cases grade database rows and
structured tool trajectories. The user-skill case grades skill loading, persistence, and
agent-scoped confirmation. `SCENARIOS` are seeded multi-turn conversations whose trials reset and
seed their scheduled tasks through the real `ScheduleStore`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from ufo_ext_scheduled_tasks.cron import next_fire
from ufo_ext_scheduled_tasks.runner import FINAL_FIRE_INSTRUCTION

from evals.driver import EVAL_SURFACE
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.harness.scorers import combine, required_tools_scorer, skill_scorer
from ufo.db import workspace_tx
from ufo.scheduling import ScheduledTask, ScheduleStore
from ufo.schema import tables

KIND = "scheduled_task"
ENVELOPE_KEYS = frozenset({"kind", "name", "spec"})
WEEKDAY_FIELDS = frozenset({"1-5", "1,2,3,4,5", "mon-fri"})
DIGEST_NAME = "investor-digest"
DIGEST_SCHEDULE = "0 9 * * 1-5"
WATCH_NAME = "competitor-watch"
WATCH_SCHEDULE = "0 7 * * *"
METRICS_NAME = "weekly-metrics"
METRICS_SCHEDULE = "0 17 * * 1"
OUTREACH_NAME = "investor-outreach"
OUTREACH_SCHEDULE = "0 8 * * *"
BOUNDED_INFORMATIONAL_FIRES = 10
EVAL_OWNER_EMAIL = "evals@localhost"
SEEDED = {
    DIGEST_NAME: ("Summarize new investor replies from the inbox.", DIGEST_SCHEDULE),
    WATCH_NAME: ("Check competitor pricing pages for changes.", WATCH_SCHEDULE),
    METRICS_NAME: ("Compile the weekly sales metrics summary.", METRICS_SCHEDULE),
    OUTREACH_NAME: ("Draft outreach emails to prospective investors.", OUTREACH_SCHEDULE),
}
HOSTILE_MARKERS = ('"status: degraded"', "#ops-alerts")
PACIFIC_MONDAY_HOURS = frozenset({"16", "17"})
MONDAY_FIELDS = frozenset({"1", "mon"})
SATISFIED_INSTRUCTION = (
    "Accept the assistant's first reasonable confirmation; do not add new requests."
)


def _task_manifests(output: CapabilityOutput) -> tuple[dict[str, object], ...]:
    documents: list[dict[str, object]] = []
    for call in output.calls:
        if call.name != "object_apply" or not call.succeeded:
            continue
        try:
            document = yaml.safe_load(str(call.input.get("manifest", "")))
        except yaml.YAMLError:
            continue
        if isinstance(document, dict) and set(document) == ENVELOPE_KEYS:
            documents.append(document)
    return tuple(documents)


def _no_jargon() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if "cron" in output.response.lower():
            return CapabilityVerdict(False, "the reply says 'cron' to the member")
        return CapabilityVerdict(True, "no scheduler jargon")

    return DescribedGrader("the reply never says 'cron'", grade)


async def _rows_about(*terms: str) -> tuple[ScheduledTask, ...]:
    rows = await ScheduleStore().list()
    return tuple(
        row
        for row in rows
        if any(term in f"{row.name} {row.prompt} {row.description}".lower() for term in terms)
    )


def _weekday_nine(schedule: str) -> bool:
    fields = schedule.split()
    return (
        len(fields) == 5
        and fields[0] == "0"
        and fields[1] == "9"
        and fields[2] == "*"
        and fields[3] == "*"
        and fields[4].lower() in WEEKDAY_FIELDS
    )


async def _graded_create(output: CapabilityOutput) -> CapabilityVerdict:
    manifests = [m for m in _task_manifests(output) if m.get("kind") == KIND]
    if not manifests:
        return CapabilityVerdict(
            False, "no successful object_apply carried a valid scheduled_task manifest"
        )
    rows = await _rows_about("investor")
    if not rows:
        return CapabilityVerdict(False, "no durable scheduled_task row mentions investors")
    row = rows[-1]
    if not _weekday_nine(row.schedule):
        return CapabilityVerdict(False, f"schedule {row.schedule!r} is not weekday 9am")
    return CapabilityVerdict(True, f"{row.name}: {row.schedule}")


async def _graded_one_shot(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("bank")
    if rows:
        return CapabilityVerdict(
            False, f"a one-shot request created {', '.join(row.name for row in rows)}"
        )
    return CapabilityVerdict(True, "no row was created")


async def _graded_hostile_prompt(output: CapabilityOutput) -> CapabilityVerdict:
    if not any(m.get("kind") == KIND for m in _task_manifests(output)):
        return CapabilityVerdict(
            False, "no successful object_apply carried a valid scheduled_task manifest"
        )
    rows = await _rows_about("degraded")
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the quoted status text")
    row = rows[-1]
    lost = [marker for marker in HOSTILE_MARKERS if marker not in row.prompt]
    if lost:
        return CapabilityVerdict(False, f"the stored prompt lost: {', '.join(lost)}")
    if row.schedule.split()[:2] != ["0", "8"]:
        return CapabilityVerdict(False, f"schedule {row.schedule!r} is not daily 8am")
    return CapabilityVerdict(True, "YAML-hostile text survived into the stored prompt intact")


async def _graded_timezone(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("sales", "metrics")
    if not rows:
        return CapabilityVerdict(False, "no durable row is about the sales metrics")
    fields = rows[-1].schedule.split()
    if len(fields) != 5 or fields[0] != "0" or fields[1] not in PACIFIC_MONDAY_HOURS:
        return CapabilityVerdict(
            False, f"schedule {rows[-1].schedule!r} is not 9am Pacific as UTC (16 or 17)"
        )
    if fields[4].lower() not in MONDAY_FIELDS:
        return CapabilityVerdict(False, f"schedule {rows[-1].schedule!r} is not Mondays")
    return CapabilityVerdict(True, f"converted to UTC: {rows[-1].schedule}")


async def _graded_restraint(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("signup")
    if rows:
        return CapabilityVerdict(
            False, f"an unscoped ask created {', '.join(row.name for row in rows)}"
        )
    return CapabilityVerdict(True, "no row was created")


async def _graded_bounded_daily(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("mccarren", "park")
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the McCarren Park task")
    row = rows[-1]
    if row.expires_at is None:
        return CapabilityVerdict(False, "the daily informational task has no expires_at")
    fire = row.next_run_at
    if fire.tzinfo is None:
        fire = fire.replace(tzinfo=UTC)
    allowed_fires = 0
    while fire < row.expires_at and allowed_fires <= BOUNDED_INFORMATIONAL_FIRES:
        allowed_fires += 1
        fire = next_fire(row.schedule, fire)
    if allowed_fires != BOUNDED_INFORMATIONAL_FIRES or fire != row.expires_at:
        return CapabilityVerdict(
            False,
            f"expires_at bounds {allowed_fires} fires and does not equal fire "
            f"{BOUNDED_INFORMATIONAL_FIRES + 1}",
        )
    embedded_mechanics = [
        term
        for term in (
            "scheduled_fire",
            "scheduled fire",
            "ask_user",
            "continue same cadence",
            "change cadence",
            "check-in",
        )
        if term in row.prompt.lower()
    ]
    if embedded_mechanics:
        return CapabilityVerdict(
            False,
            f"the durable task prompt embeds {', '.join(embedded_mechanics)}",
        )
    return CapabilityVerdict(True, "ten fires end with a runtime check-in before expiry")


async def _graded_final_fire_result_and_check_in(
    output: CapabilityOutput,
) -> CapabilityVerdict:
    applies = [index for index, call in enumerate(output.calls) if call.name == "object_apply"]
    final_calls = output.calls[applies[-1] + 1 :] if applies else ()
    asks = [call for call in final_calls if call.name == "ask_user"]
    if len(asks) != 1 or final_calls[-1] != asks[0]:
        return CapabilityVerdict(False, "ask_user was not the final fire's final tool")
    ask = asks[0]
    if not ask.succeeded:
        return CapabilityVerdict(False, "the final fire did not call ask_user successfully")
    if not any(call.name == "search_web" for call in final_calls):
        return CapabilityVerdict(False, "the final fire did not attempt the scheduled search")
    if not output.response.lower().startswith("mccarren park today:"):
        return CapabilityVerdict(False, "the closing response omitted the scheduled result")
    question = json.dumps(ask.input).lower()
    missing = [choice for choice in ("continue", "change", "stop") if choice not in question]
    if missing:
        return CapabilityVerdict(
            False, f"the check-in question lacks choices for {', '.join(missing)}"
        )
    return CapabilityVerdict(
        True,
        "the final fire reports its result and asks whether to continue, change, or stop",
    )


async def _graded_operational_task_stays_open(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("credential", "synchronization")
    if not rows:
        return CapabilityVerdict(False, "no durable credential-refresh task was created")
    if rows[-1].expires_at is not None:
        return CapabilityVerdict(False, "the operational task has an expiry")
    return CapabilityVerdict(True, "the operational task stays open-ended")


async def _mccarren_final_fire(_output: CapabilityOutput) -> str | None:
    rows = await _rows_about("mccarren", "park")
    if not rows or rows[-1].expires_at is None:
        return None
    fire = rows[-1].next_run_at
    if fire.tzinfo is None:
        fire = fire.replace(tzinfo=UTC)
    for _ in range(BOUNDED_INFORMATIONAL_FIRES - 1):
        fire = next_fire(rows[-1].schedule, fire)
    scheduled_fire = fire.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return (
        f"<scheduled_task>\nscheduled_fire: {scheduled_fire}\n</scheduled_task>\n"
        f"{rows[-1].prompt}\n"
        f"<scheduled_task_instruction>\n{FINAL_FIRE_INSTRUCTION}\n"
        "</scheduled_task_instruction>"
    )


CASES = (
    CapabilityCase(
        "O01-one-shot-refusal",
        "Remind me once next Tuesday at 3pm to call the bank.",
        DescribedGrader("no scheduled_task row about the bank exists", _graded_one_shot),
        rubric=(
            "The reply tells the member that one-off reminders are not supported and that only "
            "recurring tasks can be scheduled.",
        ),
        digest_tag="object-tools:one-shot-refusal",
    ),
    CapabilityCase(
        "O02-sub-minute-floor",
        "Watch our status page every 30 seconds and tell me the moment it changes — go ahead, "
        "no need to confirm.",
        _no_jargon(),
        rubric=(
            "The reply explains that checks cannot run more often than once per minute (or "
            "otherwise names the minute-level granularity limit).",
        ),
        digest_tag="object-tools:sub-minute-floor",
    ),
    CapabilityCase(
        "O03-kind-discovery",
        "What kinds of things can you configure or manage for me in this workspace?",
        required_tools_scorer(("object_list",)),
        rubric=(
            "The reply mentions that recurring or scheduled tasks are among the things that can "
            "be managed.",
        ),
        digest_tag="object-tools:kind-discovery",
    ),
    CapabilityCase(
        "O04-create-weekday-digest",
        "Every weekday at 9am UTC, check my inbox for new investor replies and summarize them "
        "here. Go ahead and set it up now — no need to confirm or ask which inbox.",
        combine(
            DescribedGrader(
                "a successful object_apply carries a valid scheduled_task manifest and the "
                "durable row runs weekdays at 9am",
                _graded_create,
            ),
            _no_jargon(),
        ),
        digest_tag="object-tools:create-weekday-digest",
    ),
    CapabilityCase(
        "O05-yaml-hostile-prompt",
        "Every day at 8am UTC, check https://status.example.com and post the result here. The "
        'task prompt must contain this instruction word for word: "status: degraded" means '
        "page #ops-alerts immediately; don't wait for the next run. Set it up exactly as "
        "stated — everything you need is here, no need to confirm or ask for more detail.",
        DescribedGrader(
            "the quoted, colon- and hash-laden instruction survives verbatim into the stored "
            "prompt of a daily-8am row",
            _graded_hostile_prompt,
        ),
        digest_tag="object-tools:yaml-hostile-prompt",
    ),
    CapabilityCase(
        "O06-timezone-conversion",
        "Every Monday at 9am Pacific, compile our sales metrics and post them here. We are on "
        "US Pacific time; schedules run in UTC. Go ahead and set it up — no need to confirm.",
        combine(
            DescribedGrader("the durable row runs Mondays at 16:00 or 17:00 UTC", _graded_timezone),
            _no_jargon(),
        ),
        digest_tag="object-tools:timezone-conversion",
    ),
    CapabilityCase(
        "O07-cadence-restraint",
        "Can you keep an eye on our signups from time to time?",
        DescribedGrader("no scheduled_task row about signups is created", _graded_restraint),
        rubric=(
            "The reply asks the member how often (or when) to check, rather than committing to "
            "a cadence the member never gave.",
        ),
        digest_tag="object-tools:cadence-restraint",
    ),
    CapabilityCase(
        "O08-bounded-daily-check-in",
        "Send me a DM every day at 9am Eastern with events happening in McCarren Park. Start each "
        'update with "McCarren Park today:". Set it up now — 9am works, so don\'t ask me anything '
        "else.",
        combine(
            DescribedGrader(
                "the durable task permits ten fires, adds a continuation check-in to fire 10, "
                "and expires at fire 11",
                _graded_bounded_daily,
            ),
            DescribedGrader(
                "the actual runtime final-fire input searches, reports the final result, then "
                "calls ask_user with continue, change, and stop",
                _graded_final_fire_result_and_check_in,
            ),
            _no_jargon(),
        ),
        digest_tag="object-tools:bounded-daily-check-in",
        member_key=EVAL_OWNER_EMAIL,
        followup=_mccarren_final_fire,
    ),
    CapabilityCase(
        "O09-operational-task-stays-open",
        "At the top of every hour, refresh our production integration credentials so downstream "
        "synchronization never loses access. Set it up now — don't ask me anything else.",
        combine(
            DescribedGrader(
                "the credential-refresh scheduled_task is created without expires_at",
                _graded_operational_task_stays_open,
            ),
            _no_jargon(),
        ),
        digest_tag="object-tools:operational-task-stays-open",
    ),
    CapabilityCase(
        "O10-create-agent-skill",
        "Create and save a reusable custom skill named concise-brief for this agent. It should "
        "load when I ask for a concise project brief and produce Summary, Risks, and Next steps. "
        "Do it now, then tell me whether another agent in this workspace will receive it.",
        combine(
            skill_scorer("create-skill", "coding"),
            required_tools_scorer(("object_apply",), orderings=(("load_skill", "object_apply"),)),
        ),
        rubric=(
            "The reply confirms the skill was saved for the current agent and clearly says another "
            "agent in the same workspace will not receive or load it.",
        ),
        digest_tag="object-tools:create-agent-skill",
    ),
)


def _seeded(*names: str):
    async def seed(workspace_id: UUID, agent_id: UUID) -> None:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == workspace_id
                )
            )
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-object-tools-seed:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        store = ScheduleStore()
        for name in names:
            prompt, schedule = SEEDED[name]
            await store.create(
                conversation_id=conversation_id,
                agent_id=agent_id,
                name=name,
                schedule=schedule,
                prompt=prompt,
                description=prompt,
                next_run_at=next_fire(schedule, datetime.now(UTC)),
            )

    return seed


async def _graded_update(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = await ScheduleStore().list()
    if len(rows) != 1 or rows[0].name != DIGEST_NAME:
        return CapabilityVerdict(
            False, f"expected exactly {DIGEST_NAME!r}, found {[row.name for row in rows]}"
        )
    fields = rows[0].schedule.split()
    if len(fields) != 5 or fields[0] != "0" or fields[1] != "10":
        return CapabilityVerdict(False, f"schedule {rows[0].schedule!r} is not 10am")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, f"updated in place to {rows[0].schedule}")


LISTED_NAMES = (DIGEST_NAME, WATCH_NAME, METRICS_NAME)


def _spoken(text: str) -> str:
    return text.lower().replace("-", " ")


async def _graded_listing(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = await ScheduleStore().list()
    if {row.name for row in rows} != set(LISTED_NAMES):
        return CapabilityVerdict(
            False, f"a read-only ask changed the rows: {[row.name for row in rows]}"
        )
    replies = _spoken(" ".join(outcome.replies))
    missing = [name for name in LISTED_NAMES if _spoken(name) not in replies]
    if missing:
        return CapabilityVerdict(False, f"the replies never name: {', '.join(missing)}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "all three tasks reported, rows untouched")


async def _graded_inspect(outcome: ScenarioOutcome) -> CapabilityVerdict:
    gets = [
        call
        for call in outcome.output.calls
        if call.name == "object_get"
        and call.succeeded
        and call.input.get("kind") == KIND
        and call.input.get("name") == DIGEST_NAME
    ]
    if not gets:
        return CapabilityVerdict(False, f"no successful object_get on {DIGEST_NAME!r}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "inspected the digest through object_get")


async def _graded_scoped_delete(outcome: ScenarioOutcome) -> CapabilityVerdict:
    names = {row.name for row in await ScheduleStore().list()}
    if DIGEST_NAME in names:
        return CapabilityVerdict(False, f"{DIGEST_NAME!r} still exists")
    if WATCH_NAME not in names:
        return CapabilityVerdict(False, f"{WATCH_NAME!r} was deleted too")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, f"deleted {DIGEST_NAME!r}, kept {WATCH_NAME!r}")


async def _graded_ambiguous_update(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = {row.name: row for row in await ScheduleStore().list()}
    if set(rows) != {DIGEST_NAME, OUTREACH_NAME}:
        return CapabilityVerdict(False, f"expected both investor tasks, found {sorted(rows)}")
    digest_fields = rows[DIGEST_NAME].schedule.split()
    if len(digest_fields) != 5 or digest_fields[:2] != ["0", "10"]:
        return CapabilityVerdict(
            False, f"the digest schedule {rows[DIGEST_NAME].schedule!r} is not 10am"
        )
    if rows[OUTREACH_NAME].schedule != OUTREACH_SCHEDULE:
        return CapabilityVerdict(
            False, f"the outreach task was touched: {rows[OUTREACH_NAME].schedule!r}"
        )
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "moved the digest, left the similarly-named outreach alone")


MORNING_BRIEF_ASKS = (
    ("status", lambda fields: fields[0] == "0" and fields[1:] == ["*", "*", "*", "*"]),
    ("competitor", lambda fields: fields[:2] == ["0", "7"] and fields[4] == "*"),
    ("metrics", lambda fields: fields[:2] == ["0", "17"] and fields[4].lower() in MONDAY_FIELDS),
)


async def _graded_multi_create(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = await ScheduleStore().list()
    for term, accepts in MORNING_BRIEF_ASKS:
        matched = [row for row in rows if term in f"{row.name} {row.prompt}".lower()]
        if not matched:
            return CapabilityVerdict(False, f"no row is about {term!r}")
        fields = matched[-1].schedule.split()
        if len(fields) != 5 or not accepts(fields):
            return CapabilityVerdict(
                False, f"the {term!r} schedule {matched[-1].schedule!r} is wrong"
            )
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "all three asks landed with the right cadences")


async def _graded_missing_delete(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = await ScheduleStore().list()
    if rows:
        return CapabilityVerdict(False, f"a delete ask created rows: {[row.name for row in rows]}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "nothing was created and the member ended satisfied")


SCENARIOS = (
    ScenarioCase(
        "S01-update-in-place",
        ScenarioUser(
            reason_for_call="You want your existing investor digest to run at 10am instead of 9am.",
            known_info="The digest task is called the investor digest and runs weekday mornings.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "exactly one investor-digest row survives, moved to 10am, and the member ends "
            "satisfied",
            _graded_update,
        ),
        seed=_seeded(DIGEST_NAME),
        digest_tag="object-tools:update-in-place",
    ),
    ScenarioCase(
        "S02-list-and-report",
        ScenarioUser(
            reason_for_call="You want to know which recurring tasks are currently set up.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "the replies name all three seeded tasks and the rows are unchanged",
            _graded_listing,
        ),
        seed=_seeded(DIGEST_NAME, WATCH_NAME, METRICS_NAME),
        digest_tag="object-tools:list-and-report",
    ),
    ScenarioCase(
        "S03-inspect-next-run",
        ScenarioUser(
            reason_for_call="You want to know when the investor digest will next run.",
            known_info="The task is called investor-digest.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader("a successful object_get reads the investor digest", _graded_inspect),
        seed=_seeded(DIGEST_NAME),
        digest_tag="object-tools:inspect-next-run",
    ),
    ScenarioCase(
        "S04-scoped-delete",
        ScenarioUser(
            reason_for_call="You want the assistant to stop checking your inbox for investor "
            "replies; the competitor watch must keep running.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "the investor digest is deleted and the competitor watch survives",
            _graded_scoped_delete,
        ),
        seed=_seeded(DIGEST_NAME, WATCH_NAME),
        digest_tag="object-tools:scoped-delete",
    ),
    ScenarioCase(
        "S06-ambiguous-update",
        ScenarioUser(
            reason_for_call="You want your investor digest — the task that summarizes replies "
            "from your inbox — moved to 10am. Nothing else should change.",
            known_info="There is also a separate task that drafts investor outreach emails; it "
            "must stay exactly as it is.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "the reply-summarizing digest moves to 10am while the similarly-named outreach "
            "task is untouched",
            _graded_ambiguous_update,
        ),
        seed=_seeded(DIGEST_NAME, OUTREACH_NAME),
        digest_tag="object-tools:ambiguous-update",
    ),
    ScenarioCase(
        "S07-multi-create",
        ScenarioUser(
            reason_for_call="You want three checks set up in one go: an hourly status-page "
            "check, a daily 7am UTC competitor-pricing check, and a Monday 5pm UTC sales "
            "metrics summary.",
            task_instructions="Authorize creation up front; if asked anything, repeat that "
            "everything is authorized as stated. Do not accept a wrap-up or end the "
            "conversation until the assistant confirms all three tasks exist.",
        ),
        DescribedGrader(
            "three rows land: hourly status, daily 7am competitor, Monday 5pm metrics",
            _graded_multi_create,
        ),
        seed=_seeded(),
        digest_tag="object-tools:multi-create",
    ),
    ScenarioCase(
        "S05-missing-delete",
        ScenarioUser(
            reason_for_call="You want the competitor-watch task deleted.",
            unknown_info="You do not know it was never created.",
            task_instructions=(
                "If the assistant says no such task exists, accept that and end the conversation."
            ),
        ),
        DescribedGrader(
            "the absence is discovered and reported; nothing is created", _graded_missing_delete
        ),
        seed=_seeded(),
        rubric=(
            "The assistant tells the member the task does not exist or cannot be found, and "
            "never claims to have deleted anything.",
        ),
        digest_tag="object-tools:missing-delete",
    ),
)
