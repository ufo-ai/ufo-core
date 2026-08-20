"""Writing the digest entry for each report a scheduled run published.

A periodic job fed only by its own interval, never by the run that published the report, so it can
never fire on an entry it just wrote. One report costs one model call and one row; a report already
carrying a row is never read again, so a backlog drains rather than being rewritten."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext, ModelAccess
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.terminal import BlobNotFound, BlobStore
from ufo_ext_report_digest.digest import (
    FINISH_DESCRIPTION,
    FINISH_TOOL,
    MAX_OUTPUT_TOKENS,
    MAX_REPORT_CHARS,
    DigestEntry,
    bounded,
    writing_standard,
)

SCHEDULED_ADMISSION = "scheduled"
DONE = "done"
MARKDOWN_MEDIA_TYPE = "text/markdown"
MEMBER_SUBJECT_PREFIX = "member:"

"""How many reports one tick writes. A tick that drained the whole backlog would spend a workspace's
balance in one burst on the day the extension is switched on; the interval is what paces it."""
BATCH = 8

"""How far back a report is still worth an entry. The feed is read for what happened lately, and the
window is also what bounds the job: a report the writer can never finish — its blob deleted, its
body something the model will not answer — is retried only while it stands in the window and then
ages out of the candidate read, so it can never hold a batch slot for good."""
WINDOW = timedelta(days=7)

"""How much of a report's blob is read. `bounded` cuts the characters the payload is billed for;
this cuts the bytes that reach the process at all, so a 300MB file named `.md` never lands in a
loop that serves every other surface."""
READ_BYTES = 4 * MAX_REPORT_CHARS

report_digest_entry = sa.table(
    "report_digest_entry",
    sa.column("workspace_id", sa.Uuid),
    sa.column("turn_id", sa.Uuid),
    sa.column("title", sa.Text),
    sa.column("summary", sa.Text),
    sa.column("points", sa.JSON),
    sa.column("reader", sa.Text),
    sa.column("model", sa.Text),
    sa.column("written_at", sa.DateTime(timezone=True)),
)

_turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("conversation_id", sa.Uuid),
    sa.column("agent_id", sa.Uuid),
    sa.column("admission_source", sa.Text),
    sa.column("status", sa.Text),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

_conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid),
    sa.column("audience", sa.Text),
    sa.column("member_id", sa.Uuid),
)

_agent = sa.table("agent", sa.column("id", sa.Uuid), sa.column("name", sa.Text))

_member = sa.table("member", sa.column("id", sa.Uuid), sa.column("email", sa.Text))

_shared_artifact = sa.table(
    "shared_artifact",
    sa.column("turn_id", sa.Uuid),
    sa.column("blob_key", sa.Text),
    sa.column("media_type", sa.Text),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


@dataclass(frozen=True)
class Report:
    turn_id: UUID
    blob_key: str
    agent_name: str
    audience: str
    owner_email: str | None


@dataclass(frozen=True)
class DigestWriter:
    """One tick: every report published since the last one that has no entry yet, written and
    stored, oldest first so a member's feed fills from the bottom rather than in gaps."""

    ctx: ExtensionContext
    model: ModelAccess
    blob: BlobStore

    async def run(self) -> None:
        for report in await self._unwritten():
            try:
                await self._digest(report)
            except Exception:
                """One report's failure is its own. A provider that refused, a blob the store lost,
                a body the model would not answer — each leaves that report without an entry and
                the rest of the batch untouched, because a raise here would put the same report at
                the head of every tick from now on and no later report would ever be read."""
                continue

    async def _digest(self, report: Report) -> None:
        body = await self._body(report)
        if body is None:
            return
        reader = self._reader(report)
        entry = await self._written(body, reader)
        if entry is not None:
            await self._store(report, entry, reader)

    async def _unwritten(self) -> tuple[Report, ...]:
        """Every scheduled run inside the window that ended well, published a markdown report and
        has no entry, newest first — the whole of the job's due-work definition.

        A run that did not end well is not a candidate. Its report is whatever it had written when
        it stopped, and what the member needs from it is the reason it stopped — which the feed
        says itself, and which an entry drawn over the partial report would hide.

        A run that shared several markdown files is one row, not several: the report is the first
        it shared, and a turn selected twice would be digested twice and stored once, since an
        entry is keyed by its turn. First-shared rather than first by name because a blob key opens
        on a fresh uuid — ordering by the key orders by that uuid, which is to say by nothing — and
        because share order is what core itself reads a turn's files in. Newest first because a
        member reads the feed for what
        happened lately, and because it is what keeps a report the writer cannot finish from
        standing at the head of every tick until someone notices."""
        written = sa.select(report_digest_entry.c.turn_id).where(
            report_digest_entry.c.workspace_id == self.ctx.store.workspace_id
        )
        first_report = (
            sa.select(_shared_artifact.c.blob_key)
            .where(
                _shared_artifact.c.turn_id == _turn.c.id,
                _shared_artifact.c.media_type == MARKDOWN_MEDIA_TYPE,
            )
            .order_by(_shared_artifact.c.created_at, _shared_artifact.c.blob_key)
            .limit(1)
            .correlate(_turn)
            .scalar_subquery()
        )
        query = (
            sa.select(
                _turn.c.id,
                _shared_artifact.c.blob_key,
                _agent.c.name,
                _conversation.c.audience,
                _member.c.email,
            )
            .select_from(
                _turn.join(_conversation, _turn.c.conversation_id == _conversation.c.id)
                .join(_agent, _turn.c.agent_id == _agent.c.id)
                .join(_shared_artifact, _shared_artifact.c.turn_id == _turn.c.id)
                .outerjoin(_member, _conversation.c.member_id == _member.c.id)
            )
            .where(
                _turn.c.workspace_id == self.ctx.store.workspace_id,
                _turn.c.admission_source == SCHEDULED_ADMISSION,
                _turn.c.status == DONE,
                _shared_artifact.c.blob_key == first_report,
                _turn.c.created_at >= datetime.now(UTC) - WINDOW,
                _turn.c.id.not_in(written),
            )
            .order_by(_turn.c.created_at.desc())
            .limit(BATCH)
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            Report(
                turn_id=row[0],
                blob_key=row[1],
                agent_name=row[2],
                audience=row[3],
                owner_email=row[4],
            )
            for row in rows
        )

    async def _body(self, report: Report) -> str | None:
        """The report's own characters, cut at the read ceiling before they reach the process and
        again at the payload ceiling before they reach a provider. A blob the store no longer holds
        leaves the run without an entry rather than raising: nothing will put its bytes back, and
        the window is what stops it being asked for again forever."""
        read = bytearray()
        try:
            async for chunk in self.blob.get_stream(report.blob_key):
                read += chunk
                if len(read) >= READ_BYTES:
                    break
        except BlobNotFound:
            return None
        return bounded(bytes(read[:READ_BYTES]).decode(errors="replace"))

    def _reader(self, report: Report) -> str:
        """Who the entry is written for. A run reporting into a member's own conversation has
        exactly one reader and is written for them by name; a workspace-shared run is written for
        the workspace, because every seated member reads that one row."""
        if report.audience.startswith(MEMBER_SUBJECT_PREFIX) and report.owner_email:
            return (
                f"{report.owner_email}, who owns the {report.agent_name} app and the standing "
                "order that filed this report."
            )
        return f"The workspace, reading what the {report.agent_name} app files on its own."

    async def _written(self, body: str, reader: str) -> DigestEntry | None:
        """The model's own answer as arguments the provider decoded, never structured data sliced
        out of prose — a provider that answered in prose rather than calling the tool carries its
        whole reply as one string, and that is an answer to drop, not to read characters off. An
        answer that does not match the schema leaves the run without an entry; the next tick reads
        it again while the report stands in the window."""
        reply = await self.model.turn(
            ModelRequest(
                model=self.model.model,
                system=writing_standard(),
                messages=(
                    Message(
                        role="user",
                        content=json.dumps(
                            {"report": body, "reader": reader}, separators=(",", ":")
                        ),
                    ),
                ),
                max_tokens=MAX_OUTPUT_TOKENS,
                conversation_cache_ttl="5m",
                tools=(
                    ToolSchema(
                        name=FINISH_TOOL,
                        description=FINISH_DESCRIPTION,
                        input_schema=DigestEntry.model_json_schema(),
                    ),
                ),
                tool_choice=FINISH_TOOL,
                reasoning="off",
            )
        )
        if isinstance(reply.content, str):
            return None
        for block in reply.content:
            match block:
                case ToolUseBlock() if block.name == FINISH_TOOL:
                    return DigestEntry.model_validate(block.input)
        return None

    async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.insert(report_digest_entry).values(
                    workspace_id=self.ctx.store.workspace_id,
                    turn_id=report.turn_id,
                    title=entry.title,
                    summary=entry.summary,
                    points=[point.model_dump() for point in entry.points],
                    reader=reader,
                    model=self.model.model,
                    written_at=datetime.now(UTC),
                )
            )


def undigested_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces holding a report inside the window, from a run that ended well, with no entry.
    The job's own due-work test, folded into the candidate read so a quiet workspace costs the tick
    nothing, and cut off at the
    same window the writer works to — the read is over `turn`, and one without a floor would grow
    with every turn the deploy has ever run."""
    written = sa.select(report_digest_entry.c.turn_id).where(
        report_digest_entry.c.workspace_id == _turn.c.workspace_id
    )
    return (
        sa.select(_turn.c.workspace_id)
        .select_from(_turn.join(_shared_artifact, _shared_artifact.c.turn_id == _turn.c.id))
        .where(
            _turn.c.admission_source == SCHEDULED_ADMISSION,
            _turn.c.status == DONE,
            _shared_artifact.c.media_type == MARKDOWN_MEDIA_TYPE,
            _turn.c.created_at >= datetime.now(UTC) - WINDOW,
            _turn.c.id.not_in(written),
        )
        .group_by(_turn.c.workspace_id)
    )
