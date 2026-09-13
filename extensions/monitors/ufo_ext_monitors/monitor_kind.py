"""The `monitor` object kind: what is being watched, read back and stopped.

Arming needs a live turn — the baseline is seeded by a probe run inside it — so `apply` refuses and
names the tool. What the kind supplies is the register: every armed watch, its probe and deadline,
the counters the runner keeps, and the delete that disarms one. A monitor is seen by whoever reads
the conversation it watches, the `scheduled_task` kind's rule, read live off that conversation."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.audience import Audience
from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    GeneratedObjectOwner,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectRef,
    OwnedRow,
    VerbNotSupported,
    owner_emails,
)
from ufo.sdk.tools import ToolContext
from ufo_ext_monitors.monitors import MONITOR_KIND, Monitor, MonitorStore

BASELINE_EXCERPT_MAX = 400
SUMMARY_MAX = 120
ARM_REFUSAL = (
    "arming a monitor happens in chat — the kind's `monitor` action validates the probe and seeds "
    "its baseline"
)
DELETE_GATE = "only the monitor's creator or a workspace admin may stop a monitor"


class MonitorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(
        title="Command",
        description="The shell command probed in the conversation's sandbox each interval.",
    )
    interval_minutes: int = Field(title="Interval", description="Minimum minutes between probes.")
    deadline_at: datetime = Field(description="UTC instant the monitor fires at whatever happened.")
    reason: str = Field(title="Reason", description="What is being watched and why.")
    connections: tuple[UUID, ...]
    internet_access: Literal[False] | None


def _owner(row: Monitor, audience: Audience) -> GeneratedObjectOwner:
    return GeneratedObjectOwner(
        member_id=row.created_by_member_id,
        audience=audience,
        generation=row.id,
    )


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the monitor kind requires the monitors ExtensionContext")
    return ext


@dataclass(frozen=True)
class MonitorObjects(MemberReadableObjects[MonitorSpec, GeneratedObjectOwner]):
    """The kind's handlers over `MonitorStore`. A monitor is seen by whoever reads the conversation
    it watches, plus its creator and a workspace admin — the gate is the base's, and this kind
    supplies only the audience it decides from, read off the conversation on every listing so a
    conversation shared after the arm shares its monitors. Deleting disarms, and stays the
    creator's and an admin's."""

    kind_name: ClassVar[str] = MONITOR_KIND
    mutate_gate: ClassVar[str] = ARM_REFUSAL
    delete_gate: ClassVar[str] = DELETE_GATE

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        context = _require_ext(ext)
        rows = await MonitorStore(context).armed()
        facts = await context.conversation_facts(tuple({row.conversation_id for row in rows}))
        emails = await owner_emails(row.created_by_member_id for row in rows)
        return tuple(
            OwnedRow(
                name=row.name,
                summary=f"{row.command} — {row.reason}"[:SUMMARY_MAX],
                owner=_owner(row, facts[row.conversation_id].audience),
                fields={
                    "conversation": str(row.conversation_id),
                    "next_probe_at": row.next_probe_at.isoformat(),
                    "deadline_at": row.deadline_at.isoformat(),
                    "owner_email": emails.get(row.created_by_member_id),
                    "mine": row.created_by_member_id == member_id,
                },
            )
            for row in rows
            if row.conversation_id in facts
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[MonitorSpec] | None:
        row = await self._find(ext, name)
        if row is None or row.id != owner.generation:
            return None
        return ObjectDetail(
            spec=MonitorSpec(
                command=row.command,
                interval_minutes=row.interval_minutes,
                deadline_at=row.deadline_at,
                reason=row.reason,
                connections=row.connections,
                internet_access=row.internet_access,
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            links=(
                ObjectLink(
                    relation="reports_to",
                    target=ObjectRef(kind=CONVERSATION_KIND, name=str(row.conversation_id)),
                ),
            ),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        row = await self._find(ctx.ext, name)
        if row is None or row.id != owner.generation:
            return None
        return {
            "armed_at": row.created_at.isoformat(),
            "deadline_at": row.deadline_at.isoformat(),
            "last_probe_at": (None if row.last_probe_at is None else row.last_probe_at.isoformat()),
            "probes_run": row.probes_run,
            "quiet_streak": row.quiet_streak,
            "failure_streak": row.failure_streak,
            "skipped": row.skipped,
            "baseline": row.baseline[:BASELINE_EXCERPT_MAX],
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: MonitorSpec,
        old: MonitorSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        raise VerbNotSupported(ARM_REFUSAL)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        row = await self._find(ctx.ext, name)
        if row is None or row.id != owner.generation:
            raise ValueError(f"monitor {name!r} changed while stopping")
        if not await MonitorStore(_require_ext(ctx.ext)).disarm(row):
            raise ValueError(f"monitor {name!r} changed while stopping")

    async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None:
        return next(
            (row for row in await MonitorStore(_require_ext(ext)).armed() if row.name == name),
            None,
        )


MONITOR_OBJECT = ObjectKind(
    name=MONITOR_KIND,
    description=(
        "One armed watch: a shell command probed in a conversation's sandbox, which fires the "
        "agent once and then retires. Its creator or an admin may delete it."
    ),
    guidance=(
        "Each monitor is named for the conversation it watches and the slug the watch was armed "
        "under, so two conversations can each watch a `ci-run` and every name still addresses "
        "exactly one. List to see what is currently being watched and when each next probes; "
        "get to read one "
        "monitor's command, interval, and deadline beside its status — how many probes have run, "
        "how many found nothing changed, how many failed, how many could not run at all, and an "
        "excerpt of the baseline the next probe is compared against. Listing filters and orders on "
        "`next_probe_at`, `deadline_at`, `conversation`, `owner_email`, and `mine`; get shows a "
        "`reports_to` link naming the conversation the fire lands in. Applying a manifest is "
        "refused: arm through the `monitor` action this kind lists, which runs the probe once and "
        "records what it returned. "
        "When a member says to stop watching something, delete the monitor by name — that is what "
        "disarms it, and a monitor never fires again after its one fire anyway. A monitor fires "
        "on a change in the probe's output, on three consecutive probe failures, or at "
        "`deadline_at`, and is as visible as the conversation it watches: one on a "
        "workspace-shared conversation is read by every member, one on a member's own "
        "conversation by that member alone."
    ),
    spec_model=MonitorSpec,
    store=MonitorObjects(),
    list_fields=frozenset({"conversation", "next_probe_at", "deadline_at", "owner_email", "mine"}),
    agent_target_verbs=frozenset({"list", "get", "delete"}),
)
