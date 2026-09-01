"""The report-digest seam: the skill parses into the registry, the job is declared with the
due-work read that paces it, the writing standard the job sends is the house register and the
skill's own words, and the entry a model returns is held to its bounds rather than refused — so a
provider that overruns a budget costs one clause, never the whole entry.

The bounds are what the standard cannot state and hold: a line that says what a line above it
already said is dropped, and a report the writer found no change in is recorded rather than
written, so the reader spends a row only on something new."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_report_digest.digest import (
    FINISH_TOOL,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    DigestEntry,
)
from ufo_ext_report_digest.manifest import (
    NAME,
    NOTHING_TO_REBUILD,
    REBUILD_ADMIN_ONLY,
    RebuildReportDigestInput,
    manifest,
)
from ufo_ext_report_digest.writer import (
    WINDOW,
    DigestRebuild,
    DigestWriter,
    Report,
    report_digest_entry,
    report_digest_unchanged,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import skill_registry
from ufo.runtime.ext.context import context_for
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.models import Message, ToolUseBlock

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


def test_the_skill_parses_into_the_registry() -> None:
    registry = skill_registry((manifest(),))
    assert "report-digest" in dict(registry.index())


def test_an_entry_over_its_budget_is_cut_rather_than_refused() -> None:
    entry = DigestEntry.model_validate(
        {"holds_a_change": True, "title": "word " * 60, "summary": "said " * 90, "points": []}
    )
    assert len(entry.title) <= MAX_TITLE_CHARS
    assert len(entry.summary) <= MAX_SUMMARY_CHARS
    assert not entry.title.endswith("wor")


"""The two summaries the ceiling is drawn between, both captured from the live feed: the longest
single clause a report has needed, and the shortest summary that joined two findings."""
LONGEST_ONE_CLAUSE = (
    "Skill loading, memory ingestion, and workflow handback failures dominate the latest run"
)
SHORTEST_TWO_CLAUSE = (
    "The latest sweep still failed, while 40 rotated-digest cases flipped and "
    "memory-ingestion passed 72%."
)


"""Nine findings that share no word, so nothing is dropped for restating and the cut is the only
thing left to measure."""
NINE_DISTINCT = (
    "Deploys slowed from 26 to 41 minutes",
    "Six invoices await approval",
    "Backups finished at 03:12",
    "Two seats expire on Friday",
    "Search fell behind by nine hours",
    "Card spending reached $4,200",
    "One vendor raised its renewal price",
    "Storage passed 80% full",
    "Payroll runs a day early",
)


def test_an_entry_holding_a_change_without_a_title_is_refused() -> None:
    with pytest.raises(ValueError):
        DigestEntry.model_validate({"holds_a_change": True, "summary": "s", "points": []})


"""The entry the member named, captured from the live feed: one instruction written as the summary
and again as the second line, with a distinct finding above it and another below."""
ONE_FACT_THREE_TIMES = {
    "holds_a_change": True,
    "title": "QuickBooks syncs blocked on missing realm",
    "summary": "Reconnect the affected QuickBooks account or apply its realm-specific binding",
    "points": [
        {"text": "Nine streams have had zero successful syncs since merge"},
        {"text": "QuickBooks account needs reconnection or realm-specific binding"},
        {"text": "Telemetry omits the unresolved key across 167 failures"},
    ],
}


def test_a_line_saying_what_a_line_above_it_said_is_dropped() -> None:
    """The line the member counted twice. It is dropped before the entry is cut to its budget, so
    the lower-ranked line that does carry a finding takes the row rather than being lost with it."""
    entry = DigestEntry.model_validate(ONE_FACT_THREE_TIMES)
    said = [point.text for point in entry.points]
    assert "QuickBooks account needs reconnection or realm-specific binding" not in said
    assert said == [
        "Nine streams have had zero successful syncs since merge",
        "Telemetry omits the unresolved key across 167 failures",
    ]


def test_a_summary_widening_the_title_is_dropped() -> None:
    entry = DigestEntry.model_validate(
        {
            "holds_a_change": True,
            "title": "QuickBooks syncs blocked on missing realm",
            "summary": "QuickBooks syncing is blocked on a missing realm",
        }
    )
    assert entry.summary == ""


"""A parameter is part of a test's id, so it is written down rather than generated: a `uuid4()`
here is evaluated once per collecting process and every xdist worker would name this test
something different, which the run refuses as a collection mismatch."""
A_MEMBER = "member:8c1f0d94-1f6a-4a0e-9a1e-2b7c5a0f3d41"


@pytest.mark.parametrize(
    ("audience", "email", "names_the_member"),
    [(A_MEMBER, "dana@acme.com", True), ("shared", None, False)],
)
def test_a_private_report_is_written_for_its_member_and_a_shared_one_for_the_workspace(
    audience: str, email: str | None, names_the_member: bool
) -> None:
    report = Report(
        turn_id=uuid4(),
        blob_key="k",
        agent_name="analyst",
        audience=audience,
        owner_email=email,
    )
    reader = DigestWriter._reader(None, report)  # type: ignore[arg-type]
    assert ("dana@acme.com" in reader) is names_the_member
    assert "analyst" in reader


async def _seed_workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="dana@example.com",
                timezone="UTC",
                seated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="analyst",
                prompt="Report.",
                model="auto",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                surface="web",
                queue_key=str(conversation_id),
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


async def _seed_run(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    seq: int,
    fired: datetime,
    status: str = "done",
    reports: tuple[str, ...] = ("report.md",),
    keys: tuple[str, ...] = (),
) -> tuple[UUID, tuple[str, ...]]:
    """One scheduled run. `reports` is in share order, one second apart, and `keys` pins the blob
    keys where a test needs share order and key order to disagree."""
    turn_id = uuid4()
    shared: list[str] = []
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="fired",
                admission_source="scheduled",
                terminal={"status": status, "text": "the run's own last word"},
                created_at=fired,
                updated_at=fired,
            )
        )
        for at, filename in enumerate(reports):
            key = keys[at] if keys else f"artifacts/{uuid4()}/{filename}"
            shared.append(key)
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    turn_id=turn_id,
                    blob_key=key,
                    filename=filename,
                    media_type="text/markdown",
                    size_bytes=32,
                    created_at=fired + timedelta(seconds=at),
                    updated_at=fired + timedelta(seconds=at),
                )
            )
    return turn_id, tuple(shared)


def _writer(workspace_id: UUID, blob: object, model: object) -> DigestWriter:
    ctx = SimpleNamespace(
        store=SimpleNamespace(workspace_id=workspace_id), transaction=workspace_tx
    )
    return DigestWriter(ctx=ctx, model=model, blob=blob)  # type: ignore[arg-type]


def _rebuild(workspace_id: UUID) -> DigestRebuild:
    ctx = SimpleNamespace(
        store=SimpleNamespace(workspace_id=workspace_id), transaction=workspace_tx
    )
    return DigestRebuild(ctx=ctx)  # type: ignore[arg-type]


async def test_a_reply_that_called_no_tool_is_dropped_rather_than_read_as_characters(
    db: None, tmp_path: Path
) -> None:
    """A provider that answered in prose carries its whole reply as one string. Iterating it yields
    characters, never a tool call, so the answer is dropped and the report keeps no entry."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    now = datetime.now(UTC)
    await _seed_run(workspace_id, agent_id, conversation_id, seq=1, fired=now)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))

    class Prose:
        model = "test-model"

        async def turn(self, request: object) -> Message:
            return Message(role="assistant", content="I would rather write it out.")

    with ws(workspace_id):
        async with workspace_tx() as connection:
            [key] = (
                (await connection.execute(sa.select(tables.shared_artifact.c.blob_key)))
                .scalars()
                .all()
            )
        await blob.put(key, b"# A report\n\nIt found something.\n")
        await _writer(workspace_id, blob, Prose()).run()
        async with workspace_tx() as connection:
            stored = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(report_digest_entry)
                )
            ).scalar_one()
    assert stored == 0


async def test_a_report_holding_no_change_is_recorded_rather_than_written(
    db: None, tmp_path: Path
) -> None:
    """The gate, both ends. A report that found what yesterday's found earns no row in the feed —
    the run still stands there on its task's name — and the writer records that it read it, so the
    same quiet report is never paid for a second time while it stands in the window."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    quiet, keys = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=1, fired=datetime.now(UTC)
    )
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    calls: list[str] = []

    class NothingMoved:
        model = "test-model"

        async def turn(self, request: object) -> Message:
            calls.append("call")
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="1", name=FINISH_TOOL, input={"holds_a_change": False})],
            )

    with ws(workspace_id):
        for key in keys:
            await blob.put(key, b"# Daily sweep\n\nEverything still passes.\n")
        writer = _writer(workspace_id, blob, NothingMoved())
        await writer.run()
        async with workspace_tx() as connection:
            entries = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(report_digest_entry)
                )
            ).scalar_one()
            recorded = (
                (await connection.execute(sa.select(report_digest_unchanged.c.turn_id)))
                .scalars()
                .all()
            )
        await writer.run()
    assert entries == 0
    assert list(recorded) == [quiet]
    assert len(calls) == 1


async def _entry(workspace_id: UUID, turn_id: UUID, title: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(report_digest_entry).values(
                workspace_id=workspace_id,
                turn_id=turn_id,
                title=title,
                summary=title,
                points=[],
                reader="dana@example.com",
                model="test",
                written_at=datetime.now(UTC),
            )
        )


async def _titles(workspace_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(report_digest_entry.c.title).where(
                        report_digest_entry.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_a_rebuild_makes_the_window_due_again_and_takes_the_quiet_notes_with_it(
    db: None,
) -> None:
    """What a rebuild reaches. The entries inside the window go, so the writer reads those reports
    again — and so do the notes saying it found no change, because a report held quiet under one
    standard is exactly the report a new one is meant to reach, and a note left behind would keep it
    out of the candidate read as firmly as an entry.

    A report the window has passed keeps its entry: the writer never reads that far back, so
    dropping it would empty that story rather than rewrite it."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    now = datetime.now(UTC)
    written, _ = await _seed_run(workspace_id, agent_id, conversation_id, seq=1, fired=now)
    quiet, _ = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=2, fired=now - timedelta(hours=1)
    )
    aged, _ = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=3, fired=now - WINDOW - timedelta(days=1)
    )
    await _entry(workspace_id, written, "inside the window")
    await _entry(workspace_id, aged, "aged out of the window")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(report_digest_unchanged).values(workspace_id=workspace_id, turn_id=quiet)
        )

    with ws(workspace_id):
        assert await _rebuild(workspace_id).run() == 2
        assert await _titles(workspace_id) == ["aged out of the window"]
        candidates = await _writer(workspace_id, None, None)._unwritten()
    assert [one.turn_id for one in candidates] == [written, quiet]


async def test_a_rebuild_leaves_another_workspaces_entries_where_they_stand(db: None) -> None:
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    other_id, _other_member, other_agent, other_conversation = await _seed_workspace()
    now = datetime.now(UTC)
    mine, _ = await _seed_run(workspace_id, agent_id, conversation_id, seq=1, fired=now)
    theirs, _ = await _seed_run(other_id, other_agent, other_conversation, seq=1, fired=now)
    await _entry(workspace_id, mine, "mine")
    await _entry(other_id, theirs, "theirs")

    with ws(workspace_id):
        assert await _rebuild(workspace_id).run() == 1
    assert await _titles(workspace_id) == []
    assert await _titles(other_id) == ["theirs"]


def _tool_ctx(workspace_id: UUID, member_id: UUID, agent_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="rebuild",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="auto"),
        spawn=None,  # type: ignore[arg-type]
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


async def _run_rebuild(workspace_id: UUID, member_id: UUID, agent_id: UUID) -> str:
    (tool,) = manifest().tools
    result = await tool.handler(
        _tool_ctx(workspace_id, member_id, agent_id),
        RebuildReportDigestInput(),
    )
    return result.content[0].text


async def test_the_rebuild_tool_makes_the_window_due_for_an_admin_and_refuses_everyone_else(
    db: None,
) -> None:
    """The tool is the whole act a portal button submits, so the gate on it is the gate on the
    button: a member who is not an admin is refused and every entry stands."""
    workspace_id, member_id, agent_id, conversation_id = await _seed_workspace()
    written, _ = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=1, fired=datetime.now(UTC)
    )
    await _entry(workspace_id, written, "inside the window")

    with ws(workspace_id):
        with pytest.raises(ValueError, match=REBUILD_ADMIN_ONLY):
            await _run_rebuild(workspace_id, member_id, agent_id)
        assert await _titles(workspace_id) == ["inside the window"]

        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == member_id)
                .values(is_admin=True)
            )
        assert "— 1 report." in await _run_rebuild(workspace_id, member_id, agent_id)
        assert await _titles(workspace_id) == []
        assert await _run_rebuild(workspace_id, member_id, agent_id) == NOTHING_TO_REBUILD


def test_a_title_holding_two_findings_keeps_the_one_the_writer_ranked_first() -> None:
    """Every title in a day of published entries joined two findings with a semicolon. The ceiling
    alone cut the words past 65, which left neither finding whole."""
    entry = DigestEntry(
        holds_a_change=True,
        title="Slack Code commoditizes coding agents; OneCLI pivots to team harnesses",
        summary="Coding agents are free on every plan.",
    )
    assert entry.title == "Slack Code commoditizes coding agents"
