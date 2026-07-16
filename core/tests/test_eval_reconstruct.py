"""Reconstruction proof: a run recorded without evidence is rebuilt from the durable
conversation, turn, and blob records into a diagnostic copy, while the original run file stays
byte-identical. The workspace rows and transcripts are real; only the historical run JSON is
synthesized in the scrubbed shape the stale recorder produced."""

from datetime import UTC, datetime, timedelta
from json import loads
from uuid import UUID, uuid4

import sqlalchemy as sa

from evals.harness.harness import EvalCaseResult, EvalReport, JsonObject
from evals.harness.target import COMPACTION_SUMMARY_KEY_TEMPLATE
from evals.harness.viewer import EvalRun, load_runs
from evals.reconstruct import RunReconstruction, write_reconstruction
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.loop.transcript import Transcript
from ufo.models.interface import Message, ToolResultBlock, ToolUseBlock
from ufo.schema import tables
from ufo.transcript import Conversation
from ufo.workspace import ws

MODEL = "claude-opus-4-8"
CASE_NAME = "hle_gold.sandbox_compute.abc123"
UNMATCHED_CASE_NAME = "hle_gold.sandbox_compute.fff999"


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="You are a helpful assistant.",
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _seed_case_conversation(
    workspace_id: UUID, agent_id: UUID, blob: FilesystemBlobStore
) -> tuple[UUID, UUID]:
    conversation_id = uuid4()
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="eval",
                queue_key=f"eval:{CASE_NAME}:{conversation_id}",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="sentinel inbound prompt",
                terminal={
                    "status": "done",
                    "text": "Done.",
                    "model": MODEL,
                    "tokens": 55,
                    "cost_micro_usd": 4,
                },
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/solution.py",
                workspace_id=workspace_id,
                filename="solution.py",
                subject=None,
                media_type="application/octet-stream",
                size_bytes=3,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await Transcript(blob=blob, conversation_id=conversation_id).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="sentinel inbound prompt"),
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            id="b1", name="bash", input={"command": "sentinel durable command"}
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="b1", content="sentinel durable result"),),
                ),
                Message(role="assistant", content="sentinel durable response"),
            ),
        )
    )
    await blob.put(
        COMPACTION_SUMMARY_KEY_TEMPLATE.format(conversation_id=conversation_id, index=1),
        b"summary",
    )
    return conversation_id, turn_id


def _scrubbed_case(name: str) -> EvalCaseResult:
    evidence: JsonObject = {
        "message": None,
        "grading": "sentinel grading criteria",
        "rubric": ["sentinel rubric criterion"],
        "selectedAttempt": 0,
        "attempts": [
            {
                "passed": True,
                "reason": "passed",
                "response": None,
                "calls": [
                    {
                        "name": "bash",
                        "input": {},
                        "result": "",
                        "hasResult": True,
                        "isError": False,
                    }
                ],
                "toolErrors": [],
                "artifacts": [],
                "artifactError": None,
                "tokens": 55,
                "costMicroUsd": 4,
                "log": None,
                "compactions": 1,
                "grader": {"answer": True, "confidence": 40},
                "trajectory": None,
            }
        ],
    }
    return EvalCaseResult(name=name, passed=True, reason="passed", evidence=evidence)


async def test_reconstruction_rebuilds_evidence_without_mutating_the_original_run(
    db: None, tmp_path
) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path / "blob")
    with ws(workspace_id):
        conversation_id, turn_id = await _seed_case_conversation(workspace_id, agent_id, blob)
    open_case = EvalCaseResult(
        name="capability.open",
        passed=True,
        reason="public reason",
        evidence={"message": "public question", "rubric": [], "selectedAttempt": 0, "attempts": []},
    )
    run = EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC) + timedelta(hours=1),
        label="historic",
        agent="assistant",
        ufo_version="0.1.0",
        revision="stale123",
        reports=(
            EvalReport(
                name="hle_gold.sandbox_compute",
                suite="hle_gold",
                digest="sha256:a",
                cases=(_scrubbed_case(CASE_NAME), _scrubbed_case(UNMATCHED_CASE_NAME)),
            ),
            EvalReport(
                name="capability", suite="capability", digest="sha256:b", cases=(open_case,)
            ),
        ),
    )
    out = tmp_path / "archive"
    original = out / "runs" / f"{run.id}.json"
    original.parent.mkdir(parents=True)
    original.write_text(run.model_dump_json(indent=2, by_alias=True, exclude_none=True))
    before = original.read_bytes()

    with ws(workspace_id):
        rebuilt_run = await RunReconstruction(
            workspace_id=workspace_id, blob=blob, run=run
        ).reconstruct()
    record, page = write_reconstruction(out, rebuilt_run)

    assert original.read_bytes() == before
    assert load_runs(out) == (run,)
    assert record == out / "reconstructions" / f"{run.id}.json"
    body = record.read_text()
    for expected in (
        "sentinel inbound prompt",
        "sentinel durable response",
        "sentinel durable command",
        "sentinel durable result",
        str(conversation_id),
        str(turn_id),
        '"reconstructed": true',
        '"confidence": 40',
        '"tokens": 55',
        '"compactions": 1',
        "solution.py",
        "public question",
    ):
        assert expected in body
    rebuilt = EvalRun.model_validate_json(body)
    assert rebuilt.id == run.id
    assert "reconstructed" in rebuilt.label
    matched, unmatched = rebuilt.reports[0].cases
    assert matched.passed and matched.reason == "passed"
    attempts = matched.evidence["attempts"]
    assert isinstance(attempts, list)
    attempt = attempts[0]
    assert isinstance(attempt, dict)
    assert attempt["response"] == "sentinel durable response"
    assert matched.evidence["grading"] == "sentinel grading criteria"
    assert matched.evidence["rubric"] == ["sentinel rubric criterion"]
    calls = attempt["calls"]
    assert isinstance(calls, list) and isinstance(calls[0], dict)
    assert calls[0]["input"] == {"command": "sentinel durable command"}
    assert unmatched.evidence["reconstructed"] is True
    unmatched_attempts = unmatched.evidence["attempts"]
    assert isinstance(unmatched_attempts, list) and isinstance(unmatched_attempts[0], dict)
    assert unmatched_attempts[0]["response"] is None
    assert rebuilt.reports[1].cases == (open_case,)
    html = page.read_text()
    assert '"reconstructed":true' in html
    assert "Diagnostic reconstruction" in html
    assert "sentinel durable response" in html
    assert '"response":null' in html
    assert "function attemptState" in html
    assert "could not be reconstructed" in html
    payload = loads(body)
    trajectory = payload["reports"][0]["cases"][0]["evidence"]["attempts"][0]["trajectory"]
    assert trajectory["conversation_id"] == str(conversation_id)
    assert trajectory["turn_id"] == str(turn_id)
    assert trajectory["status"] == "done"
