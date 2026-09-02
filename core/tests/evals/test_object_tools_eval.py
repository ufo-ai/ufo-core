"""The `object_tools` capability cases run concurrently against one workspace and each grader finds
its own durable rows by term, so a term that appears in another case's ask grades that case's row
and flips the verdict with the order the cases finish. The scenarios are the second family, on a
workspace of their own, and hold to the same rule among themselves."""

import ast
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from evals.suites.object_tools import (
    CASES,
    ROW_TERMS,
    SCENARIOS,
    SHARED_ARCHIVE_APP,
    SINGLE_RUN_FIRES,
    _graded_run_count,
    _graded_run_once_reply,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = ("core", "extensions", "packs", "evals")
TERM_READERS = frozenset({"_rows_about", "_graded_run_count"})
VENDORED = frozenset({"node_modules", ".venv"})
PROSPECTIVE_RUN_ONCE_REPLIES = (
    (
        "One-off runs aren't supported — only recurring scheduled tasks. The closest I can get "
        "is a recurring task at 09:00 UTC daily that expires right after tomorrow's run. Two "
        "things before I set that up: are you okay with that workaround, and what is the link?"
    ),
    (
        "One-off scheduled posts aren't supported, so I haven't set anything up: only recurring "
        "tasks can be scheduled. The nearest option is a daily task bounded to expire after "
        "tomorrow's run. Say the word and I'll set that up."
    ),
    (
        "One-time reminders are not something I can schedule — only recurring tasks are "
        "supported. If you want a workaround, I can create a task that fires tomorrow and expires "
        "right after that one run, but only say the word and I'll set that up."
    ),
)


def _families() -> tuple[dict[str, str], ...]:
    """What each case asks for, grouped by the workspace its family runs against: the capability
    cases share one and run concurrently, the scenarios share another."""
    return (
        {case.name: case.message.lower() for case in CASES},
        {case.name: case.user.scenario_block().lower() for case in SCENARIOS},
    )


def test_every_row_term_belongs_to_a_case_and_is_matchable() -> None:
    asks = {name: ask for family in _families() for name, ask in family.items()}

    assert set(ROW_TERMS) <= set(asks)
    assert [term for terms in ROW_TERMS.values() for term in terms if term != term.lower()] == []
    orphans = [
        (name, term)
        for name, terms in ROW_TERMS.items()
        for term in terms
        if term not in asks[name]
    ]
    assert orphans == []


def test_no_row_term_matches_another_cases_ask() -> None:
    collisions = [
        (owner, term, other)
        for family in _families()
        for owner, terms in ROW_TERMS.items()
        if owner in family
        for term in terms
        for other, ask in family.items()
        if other != owner and term in ask
    ]

    assert collisions == []


def test_a_row_term_where_a_case_name_belongs_fails_at_the_call_site() -> None:
    term = ROW_TERMS["O14-explicit-single-run"][0]

    with pytest.raises(KeyError) as raised:
        _graded_run_count(SINGLE_RUN_FIRES, term)

    assert term in str(raised.value)
    assert "case name" in str(raised.value)


@pytest.mark.parametrize("response", PROSPECTIVE_RUN_ONCE_REPLIES)
def test_run_once_reply_accepts_refusal_with_a_prospective_workaround(response: str) -> None:
    assert _graded_run_once_reply(response).passed


@pytest.mark.parametrize("prospect", ("can create", "will create"))
def test_run_once_reply_accepts_prospective_creation(prospect: str) -> None:
    response = (
        f"One-time runs are not supported. After you confirm, I {prospect} a recurring workaround."
    )

    assert _graded_run_once_reply(response).passed


@pytest.mark.parametrize(
    "claim",
    (
        "I've scheduled a recurring workaround.",
        "I\u2019ve scheduled a recurring workaround.",
        "I created a recurring workaround.",
        "I've created a recurring workaround.",
        "I went ahead and set up a recurring workaround.",
        "The task has been scheduled.",
        "The task was scheduled.",
    ),
)
def test_run_once_reply_rejects_a_scheduled_claim(claim: str) -> None:
    verdict = _graded_run_once_reply(f"One-time runs are not supported. {claim}")

    assert not verdict.passed
    assert "already set up" in verdict.reason


async def test_shared_conversation_cleanup_preserves_completed_turn_agent(
    db: None, tmp_path: Path
) -> None:
    case = next(case for case in CASES if case.name == "O11-archive-in-a-shared-conversation")
    assert case.seed is not None and case.cleanup is not None
    workspace_id, main_agent_id, member_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id}@eval.test",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=main_agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(workspace_id):
        await case.seed(workspace_id, main_agent_id, blob)
        async with workspace_tx() as connection:
            fixture_agent_id = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == SHARED_ARCHIVE_APP,
                    )
                )
            ).scalar_one()
            conversation_id = uuid4()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=fixture_agent_id,
                    surface="eval",
                    queue_key=f"eval:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=fixture_agent_id,
                    seq=1,
                    status="done",
                    inbound="archive the stale app",
                    terminal={"status": "done", "text": "Archived."},
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == fixture_agent_id)
                .values(
                    name=f"archived-{fixture_agent_id}",
                    archived_name=SHARED_ARCHIVE_APP,
                    archived_at=sa.func.now(),
                )
            )

        await case.cleanup(workspace_id, main_agent_id, blob)

    async with workspace_tx() as connection:
        restored = (
            await connection.execute(
                sa.select(
                    tables.agent.c.id,
                    tables.agent.c.name,
                    tables.agent.c.archived_name,
                    tables.agent.c.archived_at,
                ).where(tables.agent.c.id == fixture_agent_id)
            )
        ).one()
    assert restored.id == fixture_agent_id
    assert restored.name == SHARED_ARCHIVE_APP
    assert restored.archived_name is None
    assert restored.archived_at is None


def _case_arguments() -> list[tuple[str, str]]:
    named: list[tuple[str, str]] = []
    for root in SOURCE_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            if VENDORED & set(path.parts):
                continue
            source = path.read_text(encoding="utf-8")
            if not any(reader in source for reader in TERM_READERS):
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in TERM_READERS:
                    continue
                named.extend(
                    (str(path.relative_to(ROOT)), argument.value)
                    for argument in node.args
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                )
    return named


def test_every_term_reader_call_site_names_a_case() -> None:
    """mypy excludes the extension test trees, so a call site that passes a row term instead of a
    case name is caught by this walk of every caller in the repository."""
    sites = _case_arguments()

    assert sites
    assert [(path, name) for path, name in sites if name not in ROW_TERMS] == []
