"""Object-verb cases: the agent drives durable objects through the five generic tools.

`CASES` are single-turn capability probes on a shared workspace, each grader scoped to its own
subject so no case depends on another's leftovers. Scheduled-task cases grade database rows and
structured tool trajectories: the cron a cadence resolves to, the fires an expiry admits, the
run-once ask that must create nothing, and the cadence a vague word resolves to out of a remembered
preference. The user-skill case grades skill loading, persistence, and agent-scoped confirmation.
The shared-conversation case grades an act that needs a bound member where the model alone can bind
one: the archive lands only if the call names its `requested_by`, on the first try or after the
refusal tells it how. `SCENARIOS` are seeded multi-turn conversations whose trials reset and seed
their scheduled tasks through the real `ScheduleStore` — the workspace's first task among them,
where the first-run opening line must route to `competitive-intel` and end on one bounded daily
row."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    MemoryKind,
    body_digest,
    memory_item,
)
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron
from ufo_ext_scheduled_tasks.manifest import NAME as SCHEDULED_TASKS_NAME
from ufo_ext_scheduled_tasks.runner import FINAL_FIRE_INSTRUCTION
from ufo_ext_scheduled_tasks.schedules import ScheduledTask, ScheduleStore, scheduled_task

from evals.driver import EVAL_SURFACE
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    grading_statement,
)
from evals.harness.harness import JsonObject
from evals.harness.scenario import ScenarioCase, ScenarioGrader, ScenarioOutcome, ScenarioUser
from evals.harness.scorers import combine, required_tools_scorer, skill_scorer
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import AUTO_MODEL
from ufo.host.kinds.member_permissions import MEMBER_PERMISSION_KIND
from ufo.runtime.access.member_authorization import AuthorizationEffect
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.schema import tables
from ufo.schema.ids import uuid7

KIND = "scheduled_task"
ENVELOPE_KEYS = frozenset({"kind", "name", "spec"})
WEEKDAY_FIELDS = frozenset({"1-5", "1,2,3,4,5", "mon-fri"})
DIGEST_NAME = "investor-digest"
DIGEST_HOUR = "9"
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
CHANGELOG_WEEKDAYS = frozenset({1, 3})
CHANGELOG_CLOCK = (18, 30)
CHANGELOG_HORIZON_FIRES = 4
SINGLE_RUN_FIRES = 1
EXPLICIT_DAILY_FIRES = 10
FEW_FIRES_MIN = 2
FEW_FIRES_MAX = 5
REMEMBERED_HOUR = "8"
PREFERENCE_KIND: MemoryKind = "preference"
REMEMBERED_CADENCE = (
    'The member\'s standing cadence preference: "regularly" means every weekday at 08:00 UTC.'
)
FIRST_TASK_CASE = "S08-first-task-competitive-intel"
FIRST_TASK_COMPETITORS = ("Pentagram", "Koto", "DesignStudio")
FIRST_TASK_NAMED_MINIMUM = 2
FIRST_RUN_OPENING = (
    "I just set up this workspace. My business: Bright Signal, a two-person brand design "
    "studio. Set up my first task: a daily competitive analysis."
)
SATISFIED_INSTRUCTION = (
    "Accept the assistant's first reasonable confirmation; do not add new requests."
)
RUN_ONCE_REFUSAL_MARKERS = (
    "not supported",
    "aren't supported",
    "isn't supported",
    "can't schedule",
    "cannot schedule",
    "not something i can schedule",
)
RUN_ONCE_SCHEDULED_CLAIM = re.compile(
    r"\b(?:i(?:(?: have|['\u2019]ve))? (?:scheduled|created|set up)|i went ahead and "
    r"(?:scheduled|created|set up)|(?:it|the (?:task|run|workaround)) "
    r"(?:(?:has|had) been|(?:is|was|'s)) (?:scheduled|created|set up))\b"
)
ROW_TERMS: dict[str, tuple[str, ...]] = {
    "O01-one-shot-refusal": ("bank",),
    "O04-create-weekday-digest": ("investor",),
    "O05-yaml-hostile-prompt": ("degraded",),
    "O06-timezone-conversion": ("sales", "metrics"),
    "O07-cadence-restraint": ("signup",),
    "O08-bounded-daily-check-in": ("mccarren", "park"),
    "O09-operational-task-stays-open": ("credential", "synchronization"),
    "O12-cron-syntax-two-weekdays": ("changelog",),
    "O13-run-once-not-emulated": ("board", "deck"),
    "O14-explicit-single-run": ("shipping", "backlog"),
    "O15-explicit-ten-runs": ("huddle",),
    "O16-a-few-runs": ("warehouse", "temperature"),
    "O17-remembered-cadence": ("staging", "deploy"),
    FIRST_TASK_CASE: ("competitive",),
}
"""The terms each case's grader reads its own durable rows by. The capability cases run
concurrently against one workspace and `_rows_about` matches a term anywhere in a row's text, so a
term that appears in another case's ask reads that case's row and grades the wrong turn: every term
here must stay out of every other ask its own family makes."""


def _schedule_store() -> ScheduleStore:
    """The relocated store, which now reads its workspace and object agent off the context the
    extension's own callers hand it."""
    return ScheduleStore(context_for(SCHEDULED_TASKS_NAME, frozenset()))


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


def _manifest_spec(document: dict[str, object]) -> dict[str, object]:
    spec = document.get("spec")
    return spec if isinstance(spec, dict) else {}


def _no_jargon() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if "cron" in output.response.lower():
            return CapabilityVerdict(False, "the reply says 'cron' to the member")
        return CapabilityVerdict(True, "no scheduler jargon")

    return DescribedGrader("the reply never says 'cron'", grade)


def _terms_for(case: str) -> tuple[str, ...]:
    """The terms `case` reads its rows by. A grader names the case that owns the terms, never a
    term, so a term arriving here is a call site to repair and not a row that is missing."""
    if case not in ROW_TERMS:
        raise KeyError(f"{case!r} is not an object-tools case name; ROW_TERMS is keyed by case")
    return ROW_TERMS[case]


async def _rows_about(case: str) -> tuple[ScheduledTask, ...]:
    terms = _terms_for(case)
    rows = await _schedule_store().list()
    return tuple(
        row
        for row in rows
        if any(term in f"{row.name} {row.prompt} {row.description}".lower() for term in terms)
    )


def _weekday_at(schedule: str, hour: str) -> bool:
    fields = schedule.split()
    return (
        len(fields) == 5
        and fields[0] == "0"
        and fields[1] == hour
        and fields[2] == "*"
        and fields[3] == "*"
        and fields[4].lower() in WEEKDAY_FIELDS
    )


def _first_fire(row: ScheduledTask) -> datetime:
    return (
        row.next_run_at
        if row.next_run_at.tzinfo is not None
        else row.next_run_at.replace(tzinfo=UTC)
    )


def _permitted_fires(
    row: ScheduledTask, expires_at: datetime, ceiling: int
) -> tuple[int, datetime]:
    """How many fires the expiry admits — counted one past `ceiling`, so an unbounded run reads as
    more than the case asks for — and the fire the expiry stops."""
    fire = _first_fire(row)
    permitted = 0
    while fire < expires_at and permitted <= ceiling:
        permitted += 1
        fire = next_fire(row.schedule, fire)
    return permitted, fire


async def _graded_create(output: CapabilityOutput) -> CapabilityVerdict:
    manifests = [m for m in _task_manifests(output) if m.get("kind") == KIND]
    if not manifests:
        return CapabilityVerdict(
            False, "no successful object_apply carried a valid scheduled_task manifest"
        )
    rows = await _rows_about("O04-create-weekday-digest")
    if not rows:
        return CapabilityVerdict(False, "no durable scheduled_task row mentions investors")
    row = rows[-1]
    if not _weekday_at(row.schedule, DIGEST_HOUR):
        return CapabilityVerdict(False, f"schedule {row.schedule!r} is not weekday 9am")
    return CapabilityVerdict(True, f"{row.name}: {row.schedule}")


async def _graded_cron_syntax(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("O12-cron-syntax-two-weekdays")
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the changelog task")
    row = rows[-1]
    try:
        validate_cron(row.schedule)
    except ValueError as invalid:
        return CapabilityVerdict(False, str(invalid))
    fire = _first_fire(row)
    fires: list[datetime] = []
    for _ in range(CHANGELOG_HORIZON_FIRES):
        fires.append(fire)
        fire = next_fire(row.schedule, fire)
    stray = [
        moment.isoformat()
        for moment in fires
        if moment.weekday() not in CHANGELOG_WEEKDAYS
        or (moment.hour, moment.minute) != CHANGELOG_CLOCK
    ]
    if stray:
        return CapabilityVerdict(False, f"{row.schedule!r} fires at {', '.join(stray)}")
    if {moment.weekday() for moment in fires} != CHANGELOG_WEEKDAYS:
        return CapabilityVerdict(
            False, f"{row.schedule!r} covers only one of the two requested days"
        )
    return CapabilityVerdict(True, f"{row.schedule} fires Tuesday and Thursday at 18:30 UTC")


async def _graded_no_emulated_run_once(output: CapabilityOutput) -> CapabilityVerdict:
    specs: list[dict[str, object]] = []
    for document in _task_manifests(output):
        spec = document.get("spec")
        if document.get("kind") == KIND and isinstance(spec, dict):
            specs.append(spec)
    bounded = [spec for spec in specs if spec.get("expires_at") is not None]
    if bounded:
        return CapabilityVerdict(
            False, f"a run-once ask became a cron schedule bounded by an expiry: {bounded[-1]}"
        )
    if specs:
        return CapabilityVerdict(False, f"a run-once ask created a recurring task: {specs[-1]}")
    rows = await _rows_about("O13-run-once-not-emulated")
    if rows:
        return CapabilityVerdict(
            False, f"a run-once ask created {', '.join(row.name for row in rows)}"
        )
    return _graded_run_once_reply(output.response)


def _graded_run_once_reply(response: str) -> CapabilityVerdict:
    reply = response.casefold()
    if not any(marker in reply for marker in RUN_ONCE_REFUSAL_MARKERS):
        return CapabilityVerdict(False, "the reply does not say one-time scheduling is unsupported")
    if RUN_ONCE_SCHEDULED_CLAIM.search(reply):
        return CapabilityVerdict(
            False, "the reply claims the one-time workaround is already set up"
        )
    return CapabilityVerdict(
        True,
        "no task was created, no expiry emulated the single run, and the reply refuses one-time "
        "scheduling",
    )


def _graded_run_count(count: int, case: str) -> Grader:
    subject = " or ".join(_terms_for(case))

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        rows = await _rows_about(case)
        if not rows:
            return CapabilityVerdict(False, f"no durable row is about {subject}")
        row = rows[-1]
        if row.expires_at is None:
            return CapabilityVerdict(False, f"{row.name} has no expires_at and never stops")
        permitted, _ = _permitted_fires(row, row.expires_at, count)
        if permitted != count:
            return CapabilityVerdict(
                False,
                f"{row.schedule!r} until {row.expires_at.isoformat()} permits {permitted} fires, "
                f"not {count}",
            )
        return CapabilityVerdict(True, f"{row.name}: {permitted} fires, then expiry")

    fires = "fire" if count == SINGLE_RUN_FIRES else "fires"
    return DescribedGrader(f"the durable row permits exactly {count} {fires}", grade)


async def _graded_a_few_runs(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("O16-a-few-runs")
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the warehouse temperature task")
    row = rows[-1]
    if row.expires_at is None:
        return CapabilityVerdict(False, "'a few runs' became a task that never stops")
    permitted, _ = _permitted_fires(row, row.expires_at, FEW_FIRES_MAX)
    if not FEW_FIRES_MIN <= permitted <= FEW_FIRES_MAX:
        return CapabilityVerdict(
            False,
            f"{row.schedule!r} until {row.expires_at.isoformat()} permits {permitted} fires, "
            f"outside the {FEW_FIRES_MIN}-{FEW_FIRES_MAX} that 'a few' names",
        )
    return CapabilityVerdict(True, f"'a few' became {permitted} fires")


async def _graded_remembered_cadence(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("O17-remembered-cadence")
    if not rows:
        return CapabilityVerdict(False, "no durable row is about the staging deploy pipeline")
    row = rows[-1]
    if not _weekday_at(row.schedule, REMEMBERED_HOUR):
        return CapabilityVerdict(
            False, f"schedule {row.schedule!r} is not the remembered weekday 08:00 UTC"
        )
    searched = any(call.name == "memory_search" and call.succeeded for call in output.calls)
    return CapabilityVerdict(True, f"{row.name}: {row.schedule}", {"memory_search": searched})


async def _graded_one_shot(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("O01-one-shot-refusal")
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
    rows = await _rows_about("O05-yaml-hostile-prompt")
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
    rows = await _rows_about("O06-timezone-conversion")
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
    rows = await _rows_about("O07-cadence-restraint")
    if rows:
        return CapabilityVerdict(
            False, f"an unscoped ask created {', '.join(row.name for row in rows)}"
        )
    return CapabilityVerdict(True, "no row was created")


async def _graded_bounded_daily(output: CapabilityOutput) -> CapabilityVerdict:
    rows = await _rows_about("O08-bounded-daily-check-in")
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the McCarren Park task")
    row = rows[-1]
    if row.expires_at is None:
        return CapabilityVerdict(False, "the daily informational task has no expires_at")
    allowed_fires, fire = _permitted_fires(row, row.expires_at, BOUNDED_INFORMATIONAL_FIRES)
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
    rows = await _rows_about("O09-operational-task-stays-open")
    if not rows:
        return CapabilityVerdict(False, "no durable credential-refresh task was created")
    if rows[-1].expires_at is not None:
        return CapabilityVerdict(False, "the operational task has an expiry")
    return CapabilityVerdict(True, "the operational task stays open-ended")


async def _remember_cadence(workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
    """The cadence preference as a durable memory row. The row before it goes by the store's own
    written-fact key, so a re-run reseeds the one row instead of piling up copies, and the
    un-embedded row is recallable through the tail leg without the index job."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(memory_item).where(
                memory_item.c.workspace_id == workspace_id,
                memory_item.c.subject == SHARED_SUBJECT,
                memory_item.c.item_class == FACT,
                memory_item.c.body_digest == body_digest(REMEMBERED_CADENCE),
                memory_item.c.created_from_page_uid.is_(None),
            )
        )
        await connection.execute(
            sa.insert(memory_item).values(
                id=uuid7(),
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                body=REMEMBERED_CADENCE,
                body_digest=body_digest(REMEMBERED_CADENCE),
                item_class=FACT,
                memory_kind=PREFERENCE_KIND,
                confidence=DEFAULT_CONFIDENCE,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _mccarren_final_fire(_output: CapabilityOutput) -> str | None:
    rows = await _rows_about("O08-bounded-daily-check-in")
    if not rows or rows[-1].expires_at is None:
        return None
    fire = _first_fire(rows[-1])
    for _ in range(BOUNDED_INFORMATIONAL_FIRES - 1):
        fire = next_fire(rows[-1].schedule, fire)
    scheduled_fire = fire.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return (
        f"<scheduled_task>\nscheduled_fire: {scheduled_fire}\n</scheduled_task>\n"
        f"{rows[-1].prompt}\n"
        f"<scheduled_task_instruction>\n{FINAL_FIRE_INSTRUCTION}\n"
        "</scheduled_task_instruction>"
    )


SHARED_ARCHIVE_APP = "stale-standup-digest"
PERMISSION_CALL = "publish_quarterly_packet"
PERMISSION_EFFECT = AuthorizationEffect(
    call=PERMISSION_CALL,
    arguments={"path": "/workspace/quarterly-packet.pdf", "audience": "workspace"},
)


def _shared_conversation_seed() -> CapabilitySeed:
    async def seed(workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
        async with workspace_tx() as connection:
            owner = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.is_admin.is_(True),
                    )
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()

            existing = (
                await connection.execute(
                    sa.select(tables.agent.c.id)
                    .where(
                        tables.agent.c.workspace_id == workspace_id,
                        sa.or_(
                            tables.agent.c.name == SHARED_ARCHIVE_APP,
                            tables.agent.c.archived_name == SHARED_ARCHIVE_APP,
                        ),
                    )
                    .order_by(
                        (tables.agent.c.name == SHARED_ARCHIVE_APP).desc(),
                        tables.agent.c.updated_at.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            values = dict(
                name=SHARED_ARCHIVE_APP,
                archived_name=None,
                archived_at=None,
                prompt="You post yesterday's standup notes every morning.",
                purpose="Posts the standup digest.",
                model=AUTO_MODEL,
                visibility="workspace",
                owner_member_id=owner,
                updated_at=sa.func.now(),
            )
            if existing is not None:
                await connection.execute(
                    sa.update(tables.agent).where(tables.agent.c.id == existing).values(**values)
                )
            else:
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        created_at=sa.func.now(),
                        **values,
                    )
                )

    return seed


async def _graded_shared_archive(output: CapabilityOutput) -> CapabilityVerdict:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.agent.c.archived_at).where(
                    tables.agent.c.archived_name == SHARED_ARCHIVE_APP,
                    tables.agent.c.archived_at.is_not(None),
                )
            )
        ).all()
    deletes = [call for call in output.calls if call.name == "object_delete"]
    evidence: JsonObject = {
        "archived": len(rows),
        "object_delete_calls": len(deletes),
        "object_delete_errors": sum(1 for call in deletes if call.is_error),
        "retry_hints": sum(1 for error in output.tool_errors if "requested_by" in error),
    }
    if len(rows) != 1:
        return CapabilityVerdict(False, "the app was not archived", evidence)
    return CapabilityVerdict(True, "the app is archived", evidence)


async def _permission_seed(workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id)
                .where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.is_admin.is_(True),
                )
                .order_by(tables.member.c.created_at)
                .limit(1)
            )
        ).scalar_one()
        await connection.execute(
            sa.delete(tables.member_permission).where(
                tables.member_permission.c.workspace_id == workspace_id,
                tables.member_permission.c.member_id == member_id,
                tables.member_permission.c.agent_id == agent_id,
                tables.member_permission.c.call == PERMISSION_CALL,
            )
        )
        await connection.execute(
            sa.insert(tables.member_permission).values(
                id=uuid7(),
                workspace_id=workspace_id,
                member_id=member_id,
                agent_id=agent_id,
                call=PERMISSION_CALL,
                effect_digest=PERMISSION_EFFECT.digest,
                effect=PERMISSION_EFFECT.stored(),
                granted_by=uuid4(),
                revoked_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _permission_cleanup(workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.member_permission).where(
                tables.member_permission.c.workspace_id == workspace_id,
                tables.member_permission.c.agent_id == agent_id,
                tables.member_permission.c.call == PERMISSION_CALL,
            )
        )


async def _graded_permission_revocation(output: CapabilityOutput) -> CapabilityVerdict:
    async with workspace_tx() as connection:
        active = (
            await connection.execute(
                sa.select(tables.member_permission.c.id).where(
                    tables.member_permission.c.call == PERMISSION_CALL,
                    tables.member_permission.c.revoked_at.is_(None),
                )
            )
        ).all()
    deletes = [
        call
        for call in output.calls
        if call.name == "object_delete" and call.input.get("kind") == MEMBER_PERMISSION_KIND
    ]
    evidence: JsonObject = {
        "activePermissions": len(active),
        "permissionDeletes": len(deletes),
        "successfulDeletes": sum(call.succeeded for call in deletes),
    }
    if active:
        return CapabilityVerdict(False, "the standing permission remains active", evidence)
    if not any(call.succeeded for call in deletes):
        return CapabilityVerdict(False, "no successful permission delete was recorded", evidence)
    return CapabilityVerdict(True, "the member found and revoked the standing permission", evidence)


CASES = (
    CapabilityCase(
        "O11-archive-in-a-shared-conversation",
        f"Archive the {SHARED_ARCHIVE_APP} app. It has not posted anything useful in weeks. "
        "Go ahead; do not ask me to confirm again.",
        DescribedGrader(
            "in a shared conversation the archive lands, named to the member who asked",
            _graded_shared_archive,
        ),
        shared_audience=True,
        seed=_shared_conversation_seed(),
        cleanup=_shared_conversation_seed(),
        digest_tag="object-tools:shared-archive",
    ),
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
        "O18-revoke-standing-permission",
        "Show me my standing permissions. Revoke the one for publishing the quarterly packet.",
        DescribedGrader(
            "the member lists their standing permissions and revokes the "
            "quarterly-packet permission",
            _graded_permission_revocation,
        ),
        seed=_permission_seed,
        cleanup=_permission_cleanup,
        digest_tag="object-tools:revoke-standing-permission",
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
    CapabilityCase(
        "O12-cron-syntax-two-weekdays",
        "Every Tuesday and Thursday at 18:30 UTC, post our changelog highlights here. Set it up "
        "now — no need to confirm.",
        combine(
            DescribedGrader(
                "the durable row carries a valid 5-field cron whose next four fires are Tuesday "
                "and Thursday at 18:30 UTC",
                _graded_cron_syntax,
            ),
            _no_jargon(),
        ),
        digest_tag="object-tools:cron-syntax-two-weekdays",
    ),
    CapabilityCase(
        "O13-run-once-not-emulated",
        "Post the Q3 board deck link here once, tomorrow at 09:00 UTC. That single run is all I "
        "want — nothing after it.",
        DescribedGrader(
            "no scheduled_task is applied, no expiry emulates the single run, no row about the "
            "board deck exists, and the reply refuses one-time scheduling without claiming it is "
            "already set up",
            _graded_no_emulated_run_once,
        ),
        digest_tag="object-tools:run-once-not-emulated",
    ),
    CapabilityCase(
        "O14-explicit-single-run",
        "You cannot run something once, so use the workaround I am asking for: a daily 06:00 UTC "
        "task that reports the shipping queue backlog here, expiring after its first run. Set it "
        "up exactly that way now — that is explicitly what I want.",
        combine(
            _graded_run_count(SINGLE_RUN_FIRES, "O14-explicit-single-run"),
            _no_jargon(),
        ),
        digest_tag="object-tools:explicit-single-run",
    ),
    CapabilityCase(
        "O15-explicit-ten-runs",
        "Post our engineering huddle summary here every weekday at 13:00 UTC for the next ten "
        "runs, then stop. Set it up now — no need to confirm.",
        combine(
            _graded_run_count(EXPLICIT_DAILY_FIRES, "O15-explicit-ten-runs"),
            _no_jargon(),
        ),
        digest_tag="object-tools:explicit-ten-runs",
    ),
    CapabilityCase(
        "O16-a-few-runs",
        "Check the warehouse temperature log every morning at 05:00 UTC and post anything out of "
        "range — just a few runs, not forever. Set it up now — no need to confirm.",
        combine(
            DescribedGrader(
                f"the durable row permits {FEW_FIRES_MIN} to {FEW_FIRES_MAX} fires and then "
                "expires",
                _graded_a_few_runs,
            ),
            _no_jargon(),
        ),
        rubric=("The reply states how many runs the task will make before it stops.",),
        digest_tag="object-tools:a-few-runs",
    ),
    CapabilityCase(
        "O17-remembered-cadence",
        "Regularly check our staging deploy pipeline and post anything red here. Set it up now — "
        "you already know the cadence I mean by 'regularly'.",
        combine(
            DescribedGrader(
                "the durable row runs weekdays at 08:00 UTC, the cadence the remembered "
                "preference defines for 'regularly'",
                _graded_remembered_cadence,
            ),
            _no_jargon(),
        ),
        seed=_remember_cadence,
        digest_tag="object-tools:remembered-cadence",
    ),
)


def _seeded(*names: str):
    async def seed(workspace_id: UUID, agent_id: UUID) -> None:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(scheduled_task).where(
                    scheduled_task.c.workspace_id == workspace_id,
                    scheduled_task.c.agent_id == agent_id,
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
        with agent(agent_id):
            store = _schedule_store()
            for name in names:
                prompt, schedule = SEEDED[name]
                await store.create(
                    conversation_id=conversation_id,
                    name=name,
                    schedule=schedule,
                    prompt=prompt,
                    description=prompt,
                    next_run_at=next_fire(schedule, datetime.now(UTC)),
                )

    return seed


async def _graded_update(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = await _schedule_store().list()
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
    rows = await _schedule_store().list()
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
        and call.input.get("ref") == f"{KIND}/{DIGEST_NAME}"
    ]
    if not gets:
        return CapabilityVerdict(False, f"no successful object_get on {DIGEST_NAME!r}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "inspected the digest through object_get")


async def _graded_scoped_delete(outcome: ScenarioOutcome) -> CapabilityVerdict:
    names = {row.name for row in await _schedule_store().list()}
    if DIGEST_NAME in names:
        return CapabilityVerdict(False, f"{DIGEST_NAME!r} still exists")
    if WATCH_NAME not in names:
        return CapabilityVerdict(False, f"{WATCH_NAME!r} was deleted too")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, f"deleted {DIGEST_NAME!r}, kept {WATCH_NAME!r}")


async def _graded_ambiguous_update(outcome: ScenarioOutcome) -> CapabilityVerdict:
    rows = {row.name: row for row in await _schedule_store().list()}
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
    rows = await _schedule_store().list()
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
    rows = await _schedule_store().list()
    if rows:
        return CapabilityVerdict(False, f"a delete ask created rows: {[row.name for row in rows]}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "nothing was created and the member ended satisfied")


async def _graded_first_task(output: CapabilityOutput) -> CapabilityVerdict:
    manifests = [document for document in _task_manifests(output) if document.get("kind") == KIND]
    if not manifests:
        return CapabilityVerdict(
            False, "no successful object_apply carried a valid scheduled_task manifest"
        )
    if any(_manifest_spec(document).get("run_now") is True for document in manifests):
        return CapabilityVerdict(
            False, "the first task carries `run_now`, so the brief just written runs again"
        )
    rows = await _rows_about(FIRST_TASK_CASE)
    if not rows:
        return CapabilityVerdict(False, "no durable row carries the competitive analysis")
    row = rows[-1]
    fields = row.schedule.split()
    if (
        len(fields) != 5
        or not fields[0].isdigit()
        or not fields[1].isdigit()
        or fields[2:] != ["*", "*", "*"]
    ):
        return CapabilityVerdict(False, f"schedule {row.schedule!r} is not one fire a day")
    if row.expires_at is None:
        return CapabilityVerdict(False, "the daily informational task has no expires_at")
    permitted, fire = _permitted_fires(row, row.expires_at, BOUNDED_INFORMATIONAL_FIRES)
    if permitted != BOUNDED_INFORMATIONAL_FIRES or fire != row.expires_at:
        return CapabilityVerdict(
            False,
            f"expires_at bounds {permitted} fires and does not equal fire "
            f"{BOUNDED_INFORMATIONAL_FIRES + 1}",
        )
    named = [name for name in FIRST_TASK_COMPETITORS if name.lower() in row.prompt.lower()]
    if len(named) < FIRST_TASK_NAMED_MINIMUM:
        return CapabilityVerdict(
            False,
            f"the stored prompt names {len(named)} competitor(s), fewer than "
            f"{FIRST_TASK_NAMED_MINIMUM}",
        )
    recorded = any(
        call.name == "memory_update"
        and call.succeeded
        and any(
            competitor.lower() in str(call.input.get("body", "")).lower()
            for competitor in FIRST_TASK_COMPETITORS
        )
        for call in output.calls
    )
    if not recorded:
        return CapabilityVerdict(False, "no successful memory_update recorded a competitor")
    return CapabilityVerdict(
        True, f"{row.name}: {row.schedule}, {permitted} fires, names {', '.join(named)}"
    )


def _first_task_grader() -> ScenarioGrader:
    """The trajectory scorers read a `CapabilityOutput`; a scenario hands its grader the outcome,
    whose `output` is that trajectory reconstructed from every turn of the conversation."""
    graded = combine(
        skill_scorer("competitive-intel", "task-scheduling"),
        DescribedGrader(
            "the first brief is written in the conversation and the daily task the member then "
            "asks for lands as one scheduled_task with no immediate fire, bounded at ten fires, "
            "its prompt naming the confirmed competitors, with a competitor written to memory",
            _graded_first_task,
        ),
        _no_jargon(),
    )

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        return await graded(outcome.output)

    return DescribedGrader(grading_statement(graded), grade)


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
            reason_for_call="You want the inbox-checking investor digest deleted for good — you "
            "will not need it again, so pausing is not enough; the competitor watch must keep "
            "running.",
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
            "must stay exactly as it is. Times you give are UTC — say so if asked about a "
            "timezone.",
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
    ScenarioCase(
        FIRST_TASK_CASE,
        ScenarioUser(
            reason_for_call="The workspace first run just ended and sent your opening line for "
            f"you, so your first message is exactly this, word for word: {FIRST_RUN_OPENING}",
            known_info="Your competitors are Pentagram, Koto, and DesignStudio. You want the "
            "report here in this conversation. You are on UTC, and 8am is fine.",
            task_instructions="Confirm the competitor list the assistant proposes by naming "
            "Pentagram, Koto, and DesignStudio. When it asks whether the brief should repeat, "
            "say you want it every day. Accept every default it offers. " + SATISFIED_INSTRUCTION,
        ),
        _first_task_grader(),
        seed=_seeded(),
        digest_tag="object-tools:first-task-competitive-intel",
    ),
)
