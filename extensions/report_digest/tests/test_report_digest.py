"""The report-digest seam: the skill parses into the registry, the job is declared with the
due-work read that paces it, the writing standard the job sends is the skill's own words, and the
entry a model returns is held to its bounds rather than refused — so a provider that overruns a
budget costs one clause, never the whole entry."""

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
    DigestEntry,
    writing_standard,
)
from ufo_ext_report_digest.manifest import JOB_NAME, manifest
from ufo_ext_report_digest.writer import (
    WINDOW,
    DigestWriter,
    Report,
    report_digest_entry,
)

from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.schema import tables
from ufo.sdk.models import Message, ToolUseBlock
from ufo.workspace import ws


def test_the_skill_parses_into_the_registry() -> None:
    registry = skill_registry((manifest(),))
    assert "report-digest" in dict(registry.index())


def test_the_job_is_declared_with_a_schedule_and_due_work() -> None:
    [job] = manifest().jobs
    assert job.name == JOB_NAME
    assert job.schedule is not None
    assert job.candidates is not None


def test_the_writing_standard_is_the_skill_without_its_frontmatter() -> None:
    standard = writing_standard()
    assert "name: report-digest" not in standard
    assert "description:" not in standard.split("\n\n", 1)[0]
    for rule in ("Title the findings", "Name who did it", "One line, carrying its own number"):
        assert rule in standard


def test_an_entry_over_its_budget_is_cut_rather_than_refused() -> None:
    entry = DigestEntry.model_validate(
        {"title": "word " * 60, "summary": "said " * 90, "points": []}
    )
    assert len(entry.title) <= MAX_TITLE_CHARS
    assert len(entry.summary) <= MAX_SUMMARY_CHARS
    assert not entry.title.endswith("wor")


def test_only_the_first_points_are_kept() -> None:
    entry = DigestEntry.model_validate(
        {
            "title": "t",
            "summary": "s",
            "points": [{"text": f"line {at}", "actor": ""} for at in range(9)],
        }
    )
    assert len(entry.points) == MAX_POINTS


def test_points_arriving_as_json_text_are_decoded() -> None:
    entry = DigestEntry.model_validate(
        {"title": "t", "summary": "s", "points": '[{"text":"one","actor":"Dana Okafor"}]'}
    )
    assert entry.points[0].actor == "Dana Okafor"


def test_an_entry_missing_its_title_is_refused() -> None:
    with pytest.raises(ValueError):
        DigestEntry.model_validate({"summary": "s", "points": []})


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


async def test_the_migration_lands_the_table_the_writer_reads(db: None) -> None:
    """The entry table exists on a migrated database with the columns the job writes and the portal
    reads — a rename on either side breaks here rather than in a member's feed."""
    async with workspace_tx() as connection:
        columns = await connection.run_sync(
            lambda sync: sa.inspect(sync).get_columns("report_digest_entry")
        )
    assert {column["name"] for column in columns} == {
        "workspace_id",
        "turn_id",
        "title",
        "summary",
        "points",
        "reader",
        "model",
        "written_at",
    }


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
                            "title": "It found something",
                            "summary": "So it did.",
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
