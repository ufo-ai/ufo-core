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
    MAX_POINTS,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    SKILL_DIR,
    DigestEntry,
    writing_standard,
)
from ufo_ext_report_digest.manifest import (
    JOB_NAME,
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
from ufo.ext.context import context_for
from ufo.ext.loader import skill_registry
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.delivery_register import DELIVERY_REGISTER_BLOCK
from ufo.sdk.models import Message, ToolUseBlock
from ufo.tools.context import ToolContext
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws


def test_the_skill_parses_into_the_registry() -> None:
    registry = skill_registry((manifest(),))
    assert "report-digest" in dict(registry.index())


def test_the_job_is_declared_with_a_schedule_and_due_work() -> None:
    [job] = manifest().jobs
    assert job.name == JOB_NAME
    assert job.schedule is not None
    assert job.candidates is not None


def test_the_writing_standard_carries_the_register_once_and_then_the_skill() -> None:
    """The job's writer is a direct model call, so the house register reaches it here or nowhere.
    A member's agent carries the register in its own shell and loads these same skill words with
    `load_skill`, so one copy of each rule reaches both writers."""
    standard = writing_standard()
    assert "name: report-digest" not in standard
    assert "description:" not in standard.split("\n\n", 1)[0]
    assert standard.count(DELIVERY_REGISTER_BLOCK) == 1
    for rule in ("An entry is a change", "The order is the ranking", "Say each fact once"):
        assert rule in standard


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


def test_the_summary_ceiling_takes_one_clause_whole_and_cuts_two() -> None:
    one = DigestEntry.model_validate(
        {"holds_a_change": True, "title": "Nightly sweep failed", "summary": LONGEST_ONE_CLAUSE}
    )
    two = DigestEntry.model_validate(
        {"holds_a_change": True, "title": "Nightly sweep failed", "summary": SHORTEST_TWO_CLAUSE}
    )
    assert one.summary == LONGEST_ONE_CLAUSE
    assert len(two.summary) < len(SHORTEST_TWO_CLAUSE)


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


def test_the_points_are_cut_from_the_end() -> None:
    """Position is the rank the standard asks for, so the lines that survive the cut are the ones
    written first and the one dropped is the weakest finding the report held."""
    entry = DigestEntry.model_validate(
        {
            "holds_a_change": True,
            "title": "Nine things moved this week",
            "summary": "",
            "points": [{"text": text, "actor": ""} for text in NINE_DISTINCT],
        }
    )
    assert [point.text for point in entry.points] == list(NINE_DISTINCT[:MAX_POINTS])


def test_points_arriving_as_json_text_are_decoded() -> None:
    entry = DigestEntry.model_validate(
        {
            "holds_a_change": True,
            "title": "Folder sync landed",
            "summary": "",
            "points": '[{"text":"Markdown notes reach memory","actor":"Dana Okafor"}]',
        }
    )
    assert entry.points[0].actor == "Dana Okafor"


def test_an_entry_holding_a_change_without_a_title_is_refused() -> None:
    with pytest.raises(ValueError):
        DigestEntry.model_validate({"holds_a_change": True, "summary": "s", "points": []})


def test_an_entry_holding_no_change_carries_no_words() -> None:
    """The whole of what a quiet report costs the model to answer, and the whole of what the writer
    needs to leave the feed alone."""
    entry = DigestEntry.model_validate({"holds_a_change": False})
    assert entry.title == ""
    assert entry.points == ()


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


def test_the_standards_worked_example_is_what_the_code_produces() -> None:
    """The pair the standard prints as its good example, put through the bounds it teaches. The
    defect this replaces was a rule whose own good example broke it, so the example is held here:
    it survives untouched, and the same report written the old way is repaired above."""
    good = {
        "holds_a_change": True,
        "title": "QuickBooks has not synced since Tuesday",
        "summary": "Nine data feeds are stalled. Reconnect the account.",
    }
    entry = DigestEntry.model_validate(good)
    assert entry.title == good["title"]
    assert entry.summary == good["summary"]
    assert good["title"] in (SKILL_DIR / "SKILL.md").read_text()
    assert good["summary"] in (SKILL_DIR / "SKILL.md").read_text()


def test_a_summary_widening_the_title_is_dropped() -> None:
    entry = DigestEntry.model_validate(
        {
            "holds_a_change": True,
            "title": "QuickBooks syncs blocked on missing realm",
            "summary": "QuickBooks syncing is blocked on a missing realm",
        }
    )
    assert entry.summary == ""


def test_a_line_carrying_its_own_number_is_kept() -> None:
    """The guard on the rule above: three captured lines that each hang a number on a finding the
    title named. A rule that dropped a line for sharing words with the title would take these, and
    the reader would lose every magnitude the report stated."""
    entry = DigestEntry.model_validate(
        {
            "holds_a_change": True,
            "title": "Assistants API shuts down in five days",
            "summary": "",
            "points": [
                {"text": "Slack expands code channels into marketing, legal, and IT"},
                {"text": "OneCLI hosted pricing starts at $499 monthly"},
            ],
        }
    )
    assert len(entry.points) == 2


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


async def test_the_migrations_land_the_tables_the_writer_reads_and_writes(db: None) -> None:
    """The entry table exists on a migrated database with the columns the job writes and the portal
    reads — a rename on either side breaks here rather than in a member's feed — and beside it the
    table that records a report the writer read and found no change in."""
    async with workspace_tx() as connection:
        entry_columns = await connection.run_sync(
            lambda sync: sa.inspect(sync).get_columns("report_digest_entry")
        )
        unchanged_columns = await connection.run_sync(
            lambda sync: sa.inspect(sync).get_columns("report_digest_unchanged")
        )
    assert {column["name"] for column in entry_columns} == {
        "workspace_id",
        "turn_id",
        "title",
        "summary",
        "points",
        "reader",
        "model",
        "written_at",
    }
    assert {column["name"] for column in unchanged_columns} == {"workspace_id", "turn_id"}


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


async def test_a_run_that_shared_two_reports_is_digested_from_the_one_it_shared_first(
    db: None,
) -> None:
    """`shared_artifact` is keyed by file, so a run that shared two markdown files joins twice — and
    an entry is keyed by its turn, so a candidate read returning both would digest the run twice and
    fail storing the second.

    Which of the two is the report is decided by share order, never by the blob key: a key opens on
    a fresh uuid, so ordering by it picks a file at random. The keys here are pinned so the report
    the run shared first sorts last, and a read ordered by key would take the appendix."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    now = datetime.now(UTC)
    report = "artifacts/ffffffff-0000-4000-8000-000000000000/report.md"
    appendix = "artifacts/00000000-0000-4000-8000-000000000000/appendix.md"
    turn_id, _shared = await _seed_run(
        workspace_id,
        agent_id,
        conversation_id,
        seq=1,
        fired=now,
        reports=("report.md", "appendix.md"),
        keys=(report, appendix),
    )
    with ws(workspace_id):
        candidates = await _writer(workspace_id, None, None)._unwritten()
    assert [one.turn_id for one in candidates] == [turn_id]
    assert candidates[0].blob_key == report
    assert appendix == min(report, appendix)


async def test_the_candidate_read_holds_the_window_the_ending_and_what_is_written(
    db: None,
) -> None:
    """What the job will and will not read: a run older than the window has aged out, a run that
    did not end well says why itself rather than being digested over, and a run already carrying an
    entry is never read twice. What remains stands newest first."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    now = datetime.now(UTC)
    recent, _ = await _seed_run(workspace_id, agent_id, conversation_id, seq=1, fired=now)
    older, _ = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=2, fired=now - timedelta(hours=2)
    )
    await _seed_run(
        workspace_id, agent_id, conversation_id, seq=3, fired=now - WINDOW - timedelta(days=1)
    )
    await _seed_run(workspace_id, agent_id, conversation_id, seq=4, fired=now, status="failed")
    done, _ = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=5, fired=now - timedelta(hours=1)
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(report_digest_entry).values(
                workspace_id=workspace_id,
                turn_id=done,
                title="already said",
                summary="already said",
                points=[],
                reader="dana@example.com",
                model="test",
                written_at=now,
            )
        )
    with ws(workspace_id):
        candidates = await _writer(workspace_id, None, None)._unwritten()
    assert [one.turn_id for one in candidates] == [recent, older]


async def test_one_report_the_writer_cannot_finish_does_not_hold_up_the_rest(
    db: None, tmp_path: Path
) -> None:
    """A provider that refuses one report leaves that report without an entry and every other
    report in the batch written — a raise would put the same report at the head of every tick from
    then on, and nothing behind it would ever be digested."""
    workspace_id, _member, agent_id, conversation_id = await _seed_workspace()
    now = datetime.now(UTC)
    refused, refused_keys = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=1, fired=now
    )
    written, written_keys = await _seed_run(
        workspace_id, agent_id, conversation_id, seq=2, fired=now - timedelta(hours=1)
    )
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(workspace_id):
        for key in (*refused_keys, *written_keys):
            await blob.put(key, b"# A report\n\nIt found something.\n")

    calls: list[str] = []

    class OneRefusal:
        """A provider that refuses the first report it is asked for and answers the rest."""

        model = "test-model"

        async def turn(self, request: object) -> Message:
            calls.append("call")
            if len(calls) == 1:
                raise RuntimeError("the provider refused")
            return Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="1",
                        name=FINISH_TOOL,
                        input={
                            "holds_a_change": True,
                            "title": "It found something",
                            "summary": "",
                            "points": [],
                        },
                    )
                ],
            )

    with ws(workspace_id):
        await _writer(workspace_id, blob, OneRefusal()).run()
        async with workspace_tx() as connection:
            stored = (
                (await connection.execute(sa.select(report_digest_entry.c.turn_id))).scalars().all()
            )
    assert set(stored) == {written}
    assert refused not in stored


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
        RebuildReportDigestInput(user_description="write the radar entries again"),
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
