"""The `memory_staleness` graders: what each one accepts is the behaviour the leaf claims to
measure. The durable dimension runs against a real workspace, because the grader reads the memory
rows the turn left rather than the tool calls it made."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_eval_env.manifest import GITHUB_ITEM_FIXTURE_PREFIX
from ufo_ext_eval_env.manifest import NAME as EVAL_ENV_NAME
from ufo_ext_memory.objects import MEMORY_KIND
from ufo_ext_memory.store import memory_item

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import SEMANTIC_JUDGE_MODEL, TASKS
from evals.suites.memory_staleness import (
    CASES,
    MERGED_412,
    MERGED_419,
    OPEN_418,
    UNREADABLE_77,
    PullRequest,
    answers,
    corrects,
    keeps,
    records_nothing,
    verified,
)
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.context import ScopedStore

pytestmark = pytest.mark.usefixtures("db")

CORRECTED_412 = (
    "evalco/atlas pull request 412, 'Retry the sync token refresh' — merged on 19 August 2026."
)


def _read(item: PullRequest, succeeded: bool = True) -> ToolInvocation:
    return ToolInvocation(
        name="call_external_tool",
        input={
            "tool_name": "get_pull_request",
            "source_id": "eval_github",
            "arguments": {"repository": item.repository, "number": item.number},
        },
        result=json.dumps(item.live),
        has_result=succeeded,
        is_error=not succeeded,
    )


def _wrote(body: str) -> ToolInvocation:
    return ToolInvocation(
        name="memory_update",
        input={"body": body, "item_class": "fact", "memory_kind": "fact"},
        result="Remembered (shared).",
        has_result=True,
    )


def _output(response: str, *calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def _workspace() -> tuple[UUID, UUID]:
    workspace_id = uuid4()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="member@evalco.test",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _bodies(workspace_id: UUID) -> tuple[str, ...]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.body).where(memory_item.c.workspace_id == workspace_id)
            )
        ).scalars()
    return tuple(rows.all())


async def _remember(workspace_id: UUID, body: str) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    subject="shared",
                    body=body,
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


async def test_an_answer_without_a_live_read_fails_the_verification_dimension() -> None:
    grader = verified(MERGED_412)
    assert not (await grader(_output("It merged on 19 August."))).passed
    read = await grader(_output("It merged on 19 August.", _read(MERGED_412)))
    assert read.passed
    assert read.evidence == {"liveReads": 1}


async def test_a_read_of_another_item_is_not_this_item_read() -> None:
    other = await verified(MERGED_412)(_output("It merged.", _read(MERGED_419)))
    assert not other.passed
    failed = await verified(MERGED_412)(_output("It merged.", _read(MERGED_412, succeeded=False)))
    assert not failed.passed


async def test_the_reply_must_state_the_live_state() -> None:
    grader = answers(MERGED_412)
    silent = await grader(_output("I looked at the pull request."))
    assert not silent.passed
    assert "no live status" in silent.reason
    stated = await grader(_output("Pull request 412 merged on 19 August 2026."))
    assert stated.passed
    drafted = await grader(
        _output("Standup lines:\n- 412 merged on 19 August.\n- Nothing is left to review.")
    )
    assert drafted.passed


async def test_naming_the_remembered_status_to_retract_it_keeps_the_reply_dimension() -> None:
    grader = answers(OPEN_418)
    assert (
        await grader(
            _output(
                "418 is still open with changes requested. My note saying it merged on "
                "14 August was wrong, and I have corrected it."
            )
        )
    ).passed


async def test_a_reply_denying_the_live_state_fails_the_reply_dimension() -> None:
    grader = answers(MERGED_419)
    for response in (
        "419 is not merged.",
        "419 is unmerged.",
        "419 is still not merged.",
        "419 has never merged.",
        "419 is no longer merged.",
    ):
        verdict = await grader(_output(response))
        assert not verdict.passed, response
        assert "no live status" in verdict.reason
    assert (await grader(_output("419 merged on 21 August, so nothing is left."))).passed


async def test_a_memory_row_denying_the_live_state_is_not_a_correction() -> None:
    workspace_id, _ = await _workspace()
    denial = "evalco/atlas pull request 412 — still not merged."
    await _remember(workspace_id, denial)
    with ws(workspace_id):
        verdict = await corrects(MERGED_412)(_output("412 merged.", _wrote(denial)))
    assert not verdict.passed
    assert "no memory row" in verdict.reason


def test_the_retraction_clause_is_judged_for_every_contradicted_item() -> None:
    judged = {case.name: case.rubric for case in CASES}
    for name in (
        "M01-stale-open-claim",
        "M02-stale-merged-claim",
        "M03-status-repeated-in-a-draft",
    ):
        assert any("out of date" in clause for clause in judged[name])


async def test_memory_carrying_the_live_state_closes_the_correction_dimension() -> None:
    workspace_id, _ = await _workspace()
    await _remember(workspace_id, MERGED_412.remembered)
    grader = corrects(MERGED_412)
    with ws(workspace_id):
        answered_only = await grader(_output("412 merged on 19 August."))
        assert not answered_only.passed
        assert "recorded no memory" in answered_only.reason

        wrote_nothing_durable = await grader(
            _output("412 merged on 19 August.", _wrote(CORRECTED_412))
        )
        assert not wrote_nothing_durable.passed
        assert "no memory row" in wrote_nothing_durable.reason

    await _remember(workspace_id, CORRECTED_412)
    with ws(workspace_id):
        corrected = await grader(_output("412 merged on 19 August.", _wrote(CORRECTED_412)))
    assert corrected.passed
    assert corrected.evidence["correctedRows"] == 1
    assert corrected.evidence["staleRowRetired"] is False


async def test_a_retired_stale_row_is_reported_beside_the_correction() -> None:
    workspace_id, _ = await _workspace()
    await _remember(workspace_id, CORRECTED_412)
    with ws(workspace_id):
        verdict = await corrects(MERGED_412)(
            _output(
                "412 merged on 19 August.",
                ToolInvocation(
                    name="object_action",
                    input={
                        "kind": MEMORY_KIND,
                        "action": "record_correction",
                        "input": {"body": CORRECTED_412, "corrects": str(uuid4())},
                    },
                    result="Remembered (shared).",
                    has_result=True,
                ),
            )
        )
    assert verdict.passed
    assert verdict.evidence["staleRowRetired"] is True


async def test_memory_that_agrees_with_live_state_is_left_alone() -> None:
    grader = keeps(MERGED_419)
    assert (await grader(_output("419 merged on 21 August."))).passed
    assert (
        await grader(_output("419 merged on 21 August.", _wrote("evalco/atlas 419 — merged.")))
    ).passed
    contradicted = await grader(
        _output("419 merged.", _wrote("evalco/atlas pull request 419 — still open."))
    )
    assert not contradicted.passed
    assert "contradicting" in contradicted.reason


async def test_an_unreadable_item_is_never_written_into_memory() -> None:
    grader = records_nothing(UNREADABLE_77)
    assert (await grader(_output("I cannot read northwind/atlas from this connection."))).passed
    claimed = await grader(
        _output(
            "77 is open.",
            _wrote("northwind/atlas pull request 77 — open and blocked on a failing check."),
        )
    )
    assert not claimed.passed
    assert "unreadable" in claimed.reason


async def test_the_seed_plants_the_stale_item_and_the_cleanup_removes_every_trace(
    tmp_path: Path,
) -> None:
    workspace_id, agent_id = await _workspace()
    case = next(item for item in CASES if item.name == "M01-stale-open-claim")
    assert case.seed is not None
    assert case.cleanup is not None
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        await case.seed(workspace_id, agent_id, blob)
        assert await _bodies(workspace_id) == (MERGED_412.remembered,)
        assert await ScopedStore(extension=EVAL_ENV_NAME).get(
            f"{GITHUB_ITEM_FIXTURE_PREFIX}{MERGED_412.repository}#{MERGED_412.number}"
        ) == dict(MERGED_412.live)
    await _remember(workspace_id, CORRECTED_412)
    with ws(workspace_id):
        await case.cleanup(workspace_id, agent_id, blob)
        assert await _bodies(workspace_id) == ()


def test_the_leaf_is_registered_with_a_judge_for_its_rubric_cases() -> None:
    task = next(task for task in TASKS if task.name == "memory_staleness")
    assert task.judge_model == SEMANTIC_JUDGE_MODEL
    assert task.cases == tuple(case.name for case in CASES)
    assert sum(1 for case in CASES if case.rubric) == 4
    assert all(case.seed is not None and case.cleanup is not None for case in CASES)


def test_every_case_owns_one_repository_item() -> None:
    tags = [case.digest_tag for case in CASES]
    assert len(set(tags)) == len(tags)
