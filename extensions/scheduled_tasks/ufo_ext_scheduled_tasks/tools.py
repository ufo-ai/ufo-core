"""The `scheduled_task` object kind and the durable workflow pause.

A scheduled task is a workspace object (RFC 0017): the agent creates, updates, lists, and deletes
recurring tasks through the generic object verbs, and this module supplies the kind — spec model,
store handlers over the scoped `ScheduleStore`, cron validation on every apply. Creation binds the
applying turn's conversation and agent; updates preserve both, so every fire re-enters the original
conversation as its executor.

`pause_and_wait` is a plain tool, not a kind: a paused workflow is a thing happening rather than a
thing a member manages, so a pause is a row in this extension's own `pause` table and reaches no
object surface. It converges member ingress and timer expiry on one resume turn exactly as before —
the arbitration simply moved to where the race is, into the fire's `unless_member_since` guard."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ufo.sdk.authority import authority_member_id
from ufo.sdk.context import ExtensionContext
from ufo.sdk.o11y import log
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    AdminRequired,
    ConversationObjectGrant,
    GeneratedObjectOwner,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    OwnedRow,
    object_page,
    owner_emails,
)
from ufo.sdk.subjects import subject_shared
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron
from ufo_ext_scheduled_tasks.pauses import PauseStore
from ufo_ext_scheduled_tasks.schedules import ListedTask, ScheduledTask, ScheduleStore
from ufo_ext_scheduled_tasks.visibility import task_content_visible

SCHEDULED_TASK_KIND = "scheduled_task"
SUMMARY_MAX = 120
PRIVATE_PROMPT = "private member task"
SCHEDULE_MAX = 100
RESPONSE_EXCERPT_MAX = 400
PROMPT_EXCERPT_MAX = 400
SCHEDULE_GATE = (
    "only the task's creator may change its content; an admin may change cadence, expiry, or pause"
)
SCHEDULE_REQUESTER_GATE = "creating a scheduled task requires a member requester"
DELETE_GATE = "only the task's creator or a workspace admin may delete a scheduled task"
MAX_WAIT_MINUTES = 10_080
PAUSE_DIRECTIVE = (
    "Reply with `ai_response`, then end your turn. The workflow resumes when a new message arrives "
    "or the durable timer fires."
)


class ScheduledTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: str | None = Field(
        default=None,
        title="Schedule",
        max_length=SCHEDULE_MAX,
        examples=["0 9 * * 1-5"],
        description=(
            "A 5-field UTC cron schedule. Required on create; omitted on update preserves it."
        ),
    )
    prompt: str | None = Field(
        default=None,
        title="Prompt",
        examples=["Summarize what merged yesterday and post the list."],
        description="The prompt delivered on each fire. Required on create; omitted preserves it.",
    )
    description: str | None = Field(
        default=None,
        title="Description",
        examples=["Weekday morning engineering digest"],
        description="One listing line. Omitted on update preserves it; an empty string clears it.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="UTC expiry. Omit on update to preserve it; null clears it.",
    )
    paused: bool | None = Field(
        default=None,
        title="Paused",
        description=(
            "True stops the schedule from firing without losing the task; false resumes it from "
            "the next cron fire. Omitted on update preserves it; a new task defaults to running."
        ),
    )
    run_now: bool | None = Field(
        default=None,
        title="Run now",
        description=(
            "True fires the task once as soon as it is applied, on top of its schedule; the fire "
            "after that one is the schedule's own. It is an act rather than state: nothing is "
            "stored, and an apply that omits it leaves the next fire where the schedule puts it."
        ),
    )

    @field_validator("expires_at")
    @classmethod
    def validate_utc_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("expires_at must be a UTC timestamp")
        return value


class PauseAndWaitInput(BaseModel):
    ai_response: str = Field(description="Message to show while the workflow waits.")
    wait_minutes: int = Field(
        ge=1,
        le=MAX_WAIT_MINUTES,
        description="Minutes before the workflow resumes automatically.",
    )
    next_steps: str = Field(description="Instructions for the resumed turn.")
    reason: str = Field(description="Why the workflow is waiting.")
    metadata: dict[str, JsonValue] | None = Field(
        default=None, description="State the resumed turn needs."
    )


def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore:
    return ScheduleStore(_require_ext(ext))


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("scheduled tasks require the scheduled-tasks ExtensionContext")
    return ext


def _summary(task: ScheduledTask) -> str:
    return f"{task.schedule} — {task.description or task.prompt}"[:SUMMARY_MAX]


def _validate_future_fire(
    next_run_at: datetime, expires_at: datetime | None, *, paused: bool
) -> None:
    if not paused and expires_at is not None and expires_at <= next_run_at:
        raise ValueError("expires_at must be later than the next scheduled fire")


def _owner(listed: ListedTask) -> GeneratedObjectOwner:
    """Who a task belongs to and who else may see it. A task is shared exactly as far as the
    conversation it reports into is, so every surface listing the kind — the index, the section,
    and a conversation's own slot — answers one question the same way."""
    return GeneratedObjectOwner(
        member_id=listed.task.created_by_member_id,
        shared=subject_shared(listed.audience),
        generation=listed.task.id,
    )


@dataclass(frozen=True)
class ScheduledTaskObjects(MemberReadableObjects[ScheduledTaskSpec, GeneratedObjectOwner]):
    """The kind's handlers over `ScheduleStore`: a task is seen by whoever reads the conversation it
    reports into, plus its creator and a workspace admin — the gate is the base's, and this kind
    supplies only the `shared` fact it decides from. Deleting stays the creator's and an admin's,
    so a member reading a shared task is never a member who can change it. This kind supplies the
    task rows, their specs and status, and the create/update/cancel domain acts. Creation binds the
    applying turn's conversation and agent; updates preserve both, so a later fire re-enters that
    conversation as that agent, acting on behalf of the creator.

    Content editing is narrower than cadence management: an update keeps the original creator, so
    an admin may change schedule, expiry, or pause but never the prompt, the description, or a
    `run_now` fire that runs as that member against their private capabilities. Every new task
    requires an acting member."""

    kind_name: ClassVar[str] = SCHEDULED_TASK_KIND
    mutate_gate: ClassVar[str] = SCHEDULE_GATE
    delete_gate: ClassVar[str] = DELETE_GATE

    def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool:
        """Cadence management — schedule, expiry, pause — is an admin's; content is the
        creator's. `run_now` counts as content: it fires the prompt at once under the creator's
        authority, which is the creator's own act to ask for."""
        return not {"prompt", "description", "run_now"}.intersection(spec.model_fields_set)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        conversation = query.filters.get("conversation")
        if not isinstance(conversation, str):
            return await super().member_page(ext, member_id=member_id, admin=admin, query=query)
        try:
            conversation_id = UUID(conversation)
        except ValueError:
            return object_page((), query)
        rows = tuple(
            ObjectRow(name=row.name, summary=row.summary, fields=row.fields)
            for row in await self._rows(
                ext, member_id=member_id, conversation_id=conversation_id, prompt_max=None
            )
            if self._visible(row.owner, member_id, admin)
        )
        return object_page(rows, query)

    async def member_conversation_rows(
        self,
        ext: ExtensionContext | None,
        conversation_id: UUID,
        *,
        member_id: UUID,
        admin: bool,
        limit: int,
    ) -> tuple[ConversationObjectGrant, ...]:
        tasks = await _require_scheduler(ext).list_reported(
            conversation_id=conversation_id, limit=limit
        )
        return tuple(
            ConversationObjectGrant(
                name=listed.task.name,
                generation=listed.task.id,
                content_visible=task_content_visible(listed, member_id),
            )
            for listed in tasks
            if self._visible(_owner(listed), member_id, admin)
        )

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        return await self._rows(ext, member_id=member_id, prompt_max=None)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        """The rows a turn reads. One `object_list` puts a whole page of these in the model's
        context and `ScheduledTaskSpec.prompt` is unbounded, so a turn reads each prompt as an
        excerpt. The member's own read carries it whole: a cut arrives at a reader
        indistinguishable from a prompt that ended, and the screen that draws it cannot undo it."""
        return await self._rows(
            ctx.ext, member_id=authority_member_id(ctx.authority), prompt_max=PROMPT_EXCERPT_MAX
        )

    async def _rows(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID | None,
        prompt_max: int | None,
        conversation_id: UUID | None = None,
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        scheduler = _require_scheduler(ext)
        listed_rows = await scheduler.list_reported(conversation_id=conversation_id)
        emails = await owner_emails(row.task.created_by_member_id for row in listed_rows)
        inspections = await scheduler.inspect_many(tuple(listed.task for listed in listed_rows))
        # A task that has never fired, or whose turn is compacted away, states no ending rather
        # than one it cannot know.
        endings = {task_id: found.last_turn_status for task_id, found in inspections.items()}
        return tuple(
            OwnedRow(
                name=listed.task.name,
                summary=(
                    _summary(listed.task)
                    if task_content_visible(listed, member_id)
                    else f"{listed.task.schedule} — {PRIVATE_PROMPT}"
                ),
                owner=_owner(listed),
                fields={
                    "id": str(listed.task.id),
                    "conversation": str(listed.task.conversation_id),
                    "next_run_at": listed.task.next_run_at.isoformat(),
                    "last_run_at": (
                        None
                        if listed.task.last_run_at is None
                        else listed.task.last_run_at.isoformat()
                    ),
                    "last_run_status": endings.get(listed.task.id),
                    "paused": listed.task.paused,
                    "owner_email": emails.get(listed.task.created_by_member_id),
                    "origin": listed.surface_label or "Portal",
                    "mine": listed.task.created_by_member_id == member_id,
                    "prompt": (
                        listed.task.prompt[:prompt_max]
                        if task_content_visible(listed, member_id)
                        else PRIVATE_PROMPT
                    ),
                },
            )
            for listed in listed_rows
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[ScheduledTaskSpec] | None:
        listed = await self._find(ext, name)
        if listed is None or listed.task.id != owner.generation:
            return None
        task = listed.task
        return ObjectDetail(
            spec=ScheduledTaskSpec(
                schedule=task.schedule,
                prompt=task.prompt,
                description=task.description,
                expires_at=task.expires_at,
                paused=task.paused,
            ),
            created_at=task.created_at,
            updated_at=task.updated_at,
            links=(
                ObjectLink(
                    relation="reports_to",
                    target=ObjectRef(kind=CONVERSATION_KIND, name=str(task.conversation_id)),
                ),
            ),
            spec_visible=task_content_visible(listed, member_id),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        listed = await self._find(ctx.ext, name)
        if listed is None or listed.task.id != owner.generation:
            return None
        task = listed.task
        inspection = await _require_scheduler(ctx.ext).inspect(task)
        if inspection is None:
            return None
        last_run: dict[str, JsonValue] | None = None
        if inspection.last_turn_id is not None:
            last_run = {
                "turn_id": str(inspection.last_turn_id),
                "turn_status": inspection.last_turn_status,
            }
            if task_content_visible(listed, authority_member_id(ctx.authority)):
                last_run["response"] = (
                    None
                    if inspection.last_response is None
                    else inspection.last_response[:RESPONSE_EXCERPT_MAX]
                )
        return {
            "paused": task.paused,
            "next_run_at": inspection.next_run_at.isoformat(),
            "last_run_at": (
                None if inspection.last_run_at is None else inspection.last_run_at.isoformat()
            ),
            "expires_at": (
                None if inspection.expires_at is None else inspection.expires_at.isoformat()
            ),
            "last_run": last_run,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ScheduledTaskSpec,
        old: ScheduledTaskSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        validated_schedule = None if spec.schedule is None else validate_cron(spec.schedule)
        acting_member = authority_member_id(ctx.authority)
        if acting_member is None:
            raise AdminRequired(SCHEDULE_REQUESTER_GATE)
        found = await self._find(ctx.ext, name)
        existing = None if found is None else found.task
        scheduler = _require_scheduler(ctx.ext)
        now = datetime.now(UTC)
        if owner is None:
            if existing is not None:
                raise ValueError(f"scheduled task {name!r} changed while editing")
            if old is not None:
                raise ValueError(f"scheduled task {name!r} changed while editing")
            if validated_schedule is None or spec.prompt is None:
                raise ValueError("creating a scheduled task requires schedule and prompt")
            # `run_now` puts the first fire in the past-or-present, so the next runner tick claims
            # it and the fire after that is the cron's own.
            next_run_at = now if spec.run_now else next_fire(validated_schedule, now)
            paused = bool(spec.paused)
            _validate_future_fire(next_run_at, spec.expires_at, paused=paused)
            await scheduler.create(
                conversation_id=ctx.turn.conversation_id,
                name=name,
                schedule=validated_schedule,
                prompt=spec.prompt,
                description=spec.description or "",
                next_run_at=next_run_at,
                created_by_member_id=acting_member,
                expires_at=spec.expires_at,
                paused=paused,
            )
            return
        if existing is None or existing.id != owner.generation or old is None:
            raise ValueError(f"scheduled task {name!r} changed while editing")
        schedule = validated_schedule or existing.schedule
        if existing.created_by_member_id is None:
            if not await ctx.speaker_is_admin():
                raise AdminRequired(SCHEDULE_GATE)
        elif existing.created_by_member_id != acting_member and not await ctx.speaker_is_admin():
            raise AdminRequired(SCHEDULE_GATE)
        next_run_at = now if spec.run_now else next_fire(schedule, now)
        if spec.run_now:
            log(
                "scheduled_task.run_now",
                task=name,
                requested_by=acting_member,
                created_by=existing.created_by_member_id,
            )
        expires_at = (
            spec.expires_at if "expires_at" in spec.model_fields_set else existing.expires_at
        )
        paused = existing.paused if spec.paused is None else spec.paused
        _validate_future_fire(next_run_at, expires_at, paused=paused)
        await scheduler.update(
            expected=existing,
            schedule=schedule,
            prompt=spec.prompt or existing.prompt,
            description=existing.description if spec.description is None else spec.description,
            next_run_at=next_run_at,
            expires_at=expires_at,
            paused=paused,
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        listed = await self._find(ctx.ext, name)
        if listed is None or listed.task.id != owner.generation:
            raise ValueError(f"scheduled task {name!r} changed while cancelling")
        await _require_scheduler(ctx.ext).cancel(listed.task)

    async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None:
        return next(
            (
                listed
                for listed in await _require_scheduler(ext).list_reported()
                if listed.task.name == name
            ),
            None,
        )


SCHEDULED_TASK_OBJECT = ObjectKind(
    name=SCHEDULED_TASK_KIND,
    description=(
        "A recurring task on a UTC cron schedule: it re-invokes the agent with its prompt and "
        "reports into the conversation that created it. Only its creator may change it."
    ),
    guidance=(
        "Apply a manifest to schedule a recurring task for yourself: give a 5-field cron "
        "schedule and the task prompt. The platform materializes a locked-down recurring task "
        "that searches memory, then invokes you on that schedule in the conversation the task "
        "was created from; re-applying an existing name updates the definition in place and "
        "never moves where it reports. A task is as visible as the conversation it reports into: "
        "one reporting into a shared conversation is listed and read by every member, one "
        "reporting into a member's own conversation by that member alone, and a private or "
        "externally-shared channel's tasks by nobody else at all. Seeing a task is not changing "
        "it — only its creator may edit or delete it, and an admin may inspect or change another "
        "member's cadence, expiry, or pause, or delete it, but cannot alter its prompt or "
        "description. The main agent may name another agent only when updating that agent's "
        "existing task; creation requires the executor's own conversation. A fire acts as the "
        "creator and uses the creator's private connections, but "
        "recalls only the memory its reporting conversation can see (shared-only in a channel). "
        "Listing returns each task's name, schedule, description, creator (`owner_email`), and "
        "`origin` — the surface label of the conversation it reports into, else `Portal` — "
        "plus the latest fire as `last_run_at` and `last_run_status`, and filters and orders on "
        "`next_run_at`, `last_run_at`, `paused`, and `mine` — order by `next_run_at` asc for "
        "what fires next, "
        "filter `paused: true` for what is stopped, or `mine: true` for the caller's own; get "
        "shows the latest run's response and a "
        "`reports_to` link naming the conversation it posts into. A run's per-run output is not "
        "durable memory — it belongs "
        "in the reply the run posts, not in a saved fact; keep in-task state in files or todo "
        "items. Applying `paused: true` stops fires without losing the task; false resumes from "
        "the next cron fire. One-shot scheduling is not supported. Load the task-scheduling "
        "skill before scheduling."
    ),
    spec_model=ScheduledTaskSpec,
    store=ScheduledTaskObjects(),
    list_fields=frozenset(
        {
            "id",
            "conversation",
            "next_run_at",
            "last_run_at",
            "last_run_status",
            "paused",
            "owner_email",
            "origin",
            "mine",
            "prompt",
        }
    ),
    agent_target_verbs=frozenset({"list", "get", "update", "delete"}),
)


async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult:
    """Arm the conversation's durable pause and hand the turn its directive.

    The arm is unconditional. Whether a member has spoken since is not asked here — a message
    landing between this write and the timer would make any answer stale — so the row records where
    the conversation had got to and the fire asks admission, under the conversation lock, in the one
    moment the answer cannot change underneath it.

    Two marks in two counter spaces, because one cannot answer the question. A member message that
    folds into the arming turn leaves that turn's sequence unchanged, so turn sequence alone cannot
    separate a fold the agent had already read from one that landed after it armed — and those two
    must end opposite ways. The arrival watermark is what separates them."""
    resume_at = datetime.now(UTC) + timedelta(minutes=args.wait_minutes)
    wakeup = {
        "resuming": "timer",
        "reason": args.reason,
        "next_steps": args.next_steps,
        "metadata": args.metadata,
    }
    ext = _require_ext(ctx.ext)
    await PauseStore(ext).arm(
        conversation_id=ctx.turn.conversation_id,
        agent_id=ctx.turn.agent_id,
        resume_at=resume_at,
        origin_seq=ctx.turn.seq,
        origin_arrival_seq=await ext.conversation_arrival_seq(ctx.turn.conversation_id),
        prompt="Resume the paused workflow.\n" + json.dumps(wakeup),
        created_by_member_id=authority_member_id(ctx.authority),
    )
    payload = {
        "awaiting": "timer",
        "ai_response": args.ai_response,
        "resume_at": resume_at.isoformat(),
        "next_steps": args.next_steps,
        "reason": args.reason,
        "metadata": args.metadata,
    }
    return ToolResult(content=(TextContent(text=f"{PAUSE_DIRECTIVE}\n{json.dumps(payload)}"),))


PAUSE_AND_WAIT_TOOL = ToolDef(
    name="pause_and_wait",
    description=(
        "Pause this workflow until a new message arrives or a durable timer expires. Use for "
        "verification emails, manual approvals, and external cooldowns. The resumed turn "
        "receives `next_steps` and `metadata`."
    ),
    input_model=PauseAndWaitInput,
    handler=pause_and_wait,
    side_effecting=True,
)
