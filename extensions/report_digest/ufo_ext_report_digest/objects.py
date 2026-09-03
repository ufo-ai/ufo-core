"""The `report` object kind: the scheduled runs a member's radar reads, as workspace objects.

One object per run — a terminal scheduled turn that published a report or failed trying — named by
its turn id, carrying where and when it fired, the task that fired it, the files it shared with
signed links, and the digest entry the writer's job composed from what it published. The fence is
the reader's own audiences, exactly the fence the runs read itself holds: a feed aggregates, and an
aggregate of what each row would refuse is still refused, so an admin reads no wider here than in
the conversation. Reports exist by running — create, update, and delete are refused; the rebuild
tool is the one act over this data and it stays a tool."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.sdk.authority import authority_member_id
from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    VerbNotSupported,
    object_agent_id,
    object_page,
)
from ufo.sdk.scheduled_fire import scheduled_fire_task_id
from ufo.sdk.surfaces import ScheduledRun
from ufo.sdk.tools import ToolContext
from ufo_ext_report_digest.writer import report_digest_entry

REPORT_KIND = "report"
REPORT_LIST_MAX = 200
"""How many of an agent's newest runs one listing read materializes — the pages behind it read
through the cursor, and a run older than the two-hundredth newest ages out of the feed the way it
ages out of the rebuild window."""
REPORTS_ARE_RUNS = "a report exists by a scheduled task running — schedule the task and let it fire"

scheduled_task = sa.table(
    "scheduled_task",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("name", sa.Text),
)


class ReportSpec(BaseModel):
    """A report has no applied configuration: it is the record of a run, produced by the fire and
    the digest job, so its spec carries nothing and every mutation is refused."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ReportObjects:
    """Read handlers over the scheduled runs the reader's audiences admit, joined with the digest
    entries this extension's job wrote and the names of the tasks that fired them. The runs read
    itself fences every row, so the kind adds projection and never authority."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        member_id = authority_member_id(ctx.authority)
        if member_id is None:
            return object_page((), query)
        return await self._page(
            _ext(ctx),
            member_id,
            agent_id=ctx.turn.agent_id,
            query=query,
            subjects=ctx.read_subjects,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None:
        member_id = authority_member_id(ctx.authority)
        if member_id is None:
            return None
        found = await self._one(
            _ext(ctx),
            name,
            member_id=member_id,
            subjects=ctx.read_subjects,
        )
        return None if found is None else found.detail

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        return await self._page(_ext(ext), member_id, agent_id=object_agent_id(), query=query)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[ReportSpec] | None:
        return await self._one(_ext(ext), name, member_id=member_id)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ReportSpec,
        old: ReportSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(REPORTS_ARE_RUNS)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(REPORTS_ARE_RUNS)

    async def _page(
        self,
        ext: ExtensionContext,
        member_id: UUID,
        *,
        agent_id: UUID,
        query: ObjectListQuery,
        subjects: frozenset[str] | None = None,
    ) -> ObjectPage:
        runs = await ext.scheduled_runs(
            member_id, agent_id=agent_id, limit=REPORT_LIST_MAX, subjects=subjects
        )
        rows = await self._rows(ext, runs)
        return object_page(rows, query)

    async def _one(
        self,
        ext: ExtensionContext,
        name: str,
        *,
        member_id: UUID,
        subjects: frozenset[str] | None = None,
    ) -> MemberObject[ReportSpec] | None:
        try:
            turn_id = UUID(name)
        except ValueError:
            return None
        runs = await ext.scheduled_runs(
            member_id, agent_id=None, limit=1, turn_id=turn_id, subjects=subjects
        )
        if not runs:
            return None
        (row,) = await self._rows(ext, runs)
        run = runs[0]
        return MemberObject(
            row=row,
            detail=ObjectDetail(
                spec=ReportSpec(),
                created_at=run.fired_at,
                updated_at=run.fired_at,
                links=(
                    ObjectLink(
                        relation="created_in",
                        target=ObjectRef(kind=CONVERSATION_KIND, name=str(run.conversation_id)),
                    ),
                ),
            ),
        )

    async def _rows(
        self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]
    ) -> tuple[ObjectRow, ...]:
        entries = await self._entries(ext, tuple(run.turn_id for run in runs))
        tasks = await self._task_names(
            ext,
            tuple(
                {
                    task_id
                    for run in runs
                    if run.idempotency_key is not None
                    and (task_id := scheduled_fire_task_id(run.idempotency_key)) is not None
                }
            ),
        )
        return tuple(self._row(ext, run, entries.get(run.turn_id), tasks) for run in runs)

    def _row(
        self,
        ext: ExtensionContext,
        run: ScheduledRun,
        entry: dict[str, JsonValue] | None,
        tasks: dict[UUID, str],
    ) -> ObjectRow:
        task_id = (
            None if run.idempotency_key is None else scheduled_fire_task_id(run.idempotency_key)
        )
        task = None if task_id is None else tasks.get(task_id)
        title = None if entry is None else entry["title"]
        fired = run.fired_at.strftime("%Y-%m-%d %H:%M")
        return ObjectRow(
            name=str(run.turn_id),
            summary=(
                str(title)
                if title
                else (f"{task}, {run.status}, {fired}" if task else f"{run.status} run, {fired}")
            ),
            fields={
                "conversation": str(run.conversation_id),
                "agent_id": str(run.agent_id),
                "fired_at": run.fired_at.isoformat(),
                "status": run.status,
                "task": task,
                "surface": run.surface,
                "source": run.source,
                "text": "" if run.status == "done" else run.text,
                "entry": entry,
                "artifacts": [
                    {
                        "filename": artifact.filename,
                        "subject": artifact.subject,
                        "media_type": artifact.media_type,
                        "size_bytes": artifact.size_bytes,
                        "url": ext.artifact_link(artifact),
                        "preview_url": ext.artifact_preview_link(artifact),
                    }
                    for artifact in run.artifacts
                ],
            },
        )

    async def _entries(
        self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]
    ) -> dict[UUID, dict[str, JsonValue]]:
        if not turn_ids:
            return {}
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        report_digest_entry.c.turn_id,
                        report_digest_entry.c.title,
                        report_digest_entry.c.summary,
                        report_digest_entry.c.points,
                    ).where(
                        report_digest_entry.c.workspace_id == ext.workspace_id,
                        report_digest_entry.c.turn_id.in_(turn_ids),
                    )
                )
            ).all()
        return {
            row.turn_id: {
                "title": row.title,
                "summary": row.summary,
                "points": [
                    {"text": point["text"], "actor": point["actor"]} for point in row.points
                ],
            }
            for row in rows
        }

    async def _task_names(
        self, ext: ExtensionContext, task_ids: tuple[UUID, ...]
    ) -> dict[UUID, str]:
        """Each firing task's object name by its id, read only for ids harvested off runs the
        reader already reads — a task reports into the very conversation its runs land in, so its
        name discloses nothing the admitted run has not. A since-deleted task resolves no name and
        its run reads on the conversation alone."""
        if not task_ids:
            return {}
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(scheduled_task.c.id, scheduled_task.c.name).where(
                        scheduled_task.c.workspace_id == ext.workspace_id,
                        scheduled_task.c.id.in_(task_ids),
                    )
                )
            ).all()
        return {row.id: row.name for row in rows}


def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext:
    if isinstance(carrier, ExtensionContext):
        return carrier
    if carrier is not None and carrier.ext is not None:
        return carrier.ext
    raise RuntimeError("the report kind dispatched without its ExtensionContext")


REPORT_OBJECT = ObjectKind(
    name=REPORT_KIND,
    description=(
        "One scheduled run the member's radar reads: when it fired, the task behind it, the "
        "files it shared, and the digest entry written from it. Read-only."
    ),
    guidance=(
        "The radar's rows: scheduled runs, named by turn id, newest first under "
        "order_by=fired_at desc. Each carries conversation, fired_at, status, task (the firing "
        "scheduled task's object name, null when deleted), surface, source, text (a failed run's "
        "terminal reply; empty when it ended well), the digest `entry` — title, summary, points; "
        "null until the writer's job reaches the run — and artifacts (the files it shared, each "
        "with a signed url and preview_url). Filter on conversation for one session's runs or on "
        "status for failures. Reads stay inside the reader's own audiences and never widen for an "
        "admin. Create, update, and delete are refused — a report exists by a scheduled task "
        "running, and rebuild_report_digest is the act that rewrites entries."
    ),
    spec_model=ReportSpec,
    store=ReportObjects(),
    list_fields=frozenset(
        {
            "conversation",
            "agent_id",
            "fired_at",
            "status",
            "task",
            "surface",
            "source",
            "text",
            "entry",
            "artifacts",
        }
    ),
)
