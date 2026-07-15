import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

from evals.gdpval_100.grading import (
    MAX_CLI_REQUEST_BYTES,
    ArtifactView,
    CalibrationJudge,
    CalibrationRequest,
    ParserOutput,
    RubricCriterion,
    RubricItemVerdict,
    SubmissionVerdict,
    SubmissionView,
    TaskGrade,
)
from evals.gdpval_100.models import RubricItem, SnapshotCase
from evals.gdpval_100.snapshot import GDPVAL_UPSTREAM, write_snapshot

DIGESTS = tuple(f"sha256:{index:064x}" for index in range(1, 20))
TASK_ID = str(UUID(int=1))
RUBRIC_ID = str(UUID(int=2))
JUDGE_PROGRAM = """
import json
import sys

wire = json.load(sys.stdin)
payload = json.loads(wire["prompt"])
preferred = sys.argv[1]
a_wins = payload["submission_A"]["digest"] == preferred

def scored(score):
    return {
        "structurally_valid": True,
        "items": [
            {"criterion_id": item["id"], "score": score, "reason": "checked artifact"}
            for item in payload["rubric"]
        ],
    }

json.dump(
    {
        "a": scored(0.9 if a_wins else 0.4),
        "b": scored(0.4 if a_wins else 0.9),
        "preference": "A" if a_wins else "B",
        "pairwise_reason": "preferred artifact is stronger",
    },
    sys.stdout,
)
"""


@dataclass
class PanelLeg:
    name: str
    preferred_digest: str
    malformed: bool = False
    prompts: list[str] = field(default_factory=list)

    async def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.malformed:
            return "not json"
        payload = json.loads(prompt)
        a = payload["submission_A"]
        preference = "A" if a["digest"] == self.preferred_digest else "B"
        return json.dumps(
            {
                "a": {
                    "structurally_valid": True,
                    "items": [
                        {
                            "criterion_id": "correct",
                            "score": 0.9 if preference == "A" else 0.4,
                            "reason": "inspected rendered evidence",
                        },
                        {
                            "criterion_id": "usable",
                            "score": 0.8 if preference == "A" else 0.5,
                            "reason": "deliverable is usable",
                        },
                    ],
                },
                "b": {
                    "structurally_valid": True,
                    "items": [
                        {
                            "criterion_id": "correct",
                            "score": 0.4 if preference == "A" else 0.9,
                            "reason": "inspected rendered evidence",
                        },
                        {
                            "criterion_id": "usable",
                            "score": 0.5 if preference == "A" else 0.8,
                            "reason": "deliverable is usable",
                        },
                    ],
                },
                "preference": preference,
                "pairwise_reason": "preferred submission better satisfies the rubric",
            }
        )


def _artifact(name: str, digest: str, evidence: str) -> ArtifactView:
    return ArtifactView(
        name=name,
        media_type="application/pdf",
        digest=digest,
        parser_outputs=(
            ParserOutput(kind="text", content="extracted text", digest=DIGESTS[8]),
            ParserOutput(kind="render", content=evidence, digest=DIGESTS[9]),
        ),
    )


def _submissions() -> tuple[SubmissionView, SubmissionView]:
    return (
        SubmissionView(
            digest=DIGESTS[0],
            response="submitted report",
            artifacts=(_artifact("report.pdf", DIGESTS[2], "rendered page alpha"),),
        ),
        SubmissionView(
            digest=DIGESTS[1],
            response="submitted report",
            artifacts=(_artifact("report.pdf", DIGESTS[3], "rendered page beta"),),
        ),
    )


def _rubric() -> tuple[RubricCriterion, ...]:
    return (
        RubricCriterion(id="correct", description="Facts and calculations are correct", weight=2),
        RubricCriterion(id="usable", description="Deliverable is professionally usable", weight=1),
    )


def _request() -> CalibrationRequest:
    left, right = _submissions()
    return CalibrationRequest(
        randomization_seed="operator-seed",
        task_id=TASK_ID,
        references=(),
        left=left,
        right=right,
    )


def _snapshot(tmp_path: Path) -> Path:
    rubric = (
        RubricItem(
            score=2,
            criterion="The artifact answers the pinned request.",
            rubric_item_id=RUBRIC_ID,
            tags=("true",),
        ),
    )
    cases = tuple(
        SnapshotCase(
            task_id=str(UUID(int=index + 1)),
            sector=f"Sector {index // 25}",
            occupation=f"Occupation {index // 5}",
            prompt=(
                "Produce the requested report from the pinned snapshot"
                if index == 0
                else f"Produce artifact {index}"
            ),
            references=(),
            deliverables=(),
            rubric=rubric,
        )
        for index in range(220)
    )
    staging = tmp_path / "snapshot-staging"
    staging.mkdir()
    return write_snapshot(staging, tmp_path / "snapshots", GDPVAL_UPSTREAM, cases, (TASK_ID,))


def _commands(preferred_digest: str) -> list[dict[str, object]]:
    return [
        {
            "name": f"judge-{index}",
            "argv": [sys.executable, "-c", JUDGE_PROGRAM, preferred_digest],
            "timeout_seconds": 10,
        }
        for index in range(3)
    ]


async def test_three_leg_artifact_grade_is_blind_randomized_and_aggregated() -> None:
    left, right = _submissions()
    for seed_index in range(100):
        legs = tuple(
            PanelLeg(name, left.digest) for name in ("judge-one", "judge-two", "judge-three")
        )
        judge = CalibrationJudge(legs, f"seed-{seed_index}")
        grade = await judge.grade(
            "task-1",
            "Produce the requested report",
            (_artifact("reference.pdf", DIGESTS[4], "reference rendered page"),),
            _rubric(),
            left,
            right,
        )
        if len({leg.a_digest for leg in grade.legs}) == 2:
            break
    assert grade.valid
    assert grade.outcome == "left"
    assert grade.judge_agreement == 1
    assert grade.left_score == (0.9 * 2 + 0.8) / 3
    assert grade.right_score == (0.4 * 2 + 0.5) / 3
    assert {leg.a_digest for leg in grade.legs} == {left.digest, right.digest}
    assert all(len(leg.left_items) == 2 for leg in grade.legs)
    assert all("left" not in leg.prompts[0] and "right" not in leg.prompts[0] for leg in legs)
    assert all("reference rendered page" in leg.prompts[0] for leg in legs)


async def test_any_invalid_judge_leg_fails_the_task_closed() -> None:
    left, right = _submissions()
    legs = (
        PanelLeg("judge-one", left.digest),
        PanelLeg("judge-two", left.digest, malformed=True),
        PanelLeg("judge-three", left.digest),
    )
    grade = await CalibrationJudge(legs, "seed").grade(
        "task-1", "Produce the requested report", (), _rubric(), left, right
    )
    assert not grade.valid
    assert grade.outcome == "invalid"
    assert grade.left_score == 0
    assert grade.judge_agreement == 0
    assert grade.failure_reasons[0].startswith("judge-two:")
    assert len(grade.legs) == 3


async def test_invalid_task_boundary_does_not_call_judges() -> None:
    left, right = _submissions()
    legs = tuple(PanelLeg(name, left.digest) for name in ("judge-one", "judge-two", "judge-three"))
    grade = await CalibrationJudge(legs, "seed").grade(
        "task-1", "Produce the requested report", (), (), left, right
    )
    assert not grade.valid
    assert grade.failure_reasons == ("rubric must not be empty",)
    assert all(not leg.prompts for leg in legs)


def test_signed_penalty_reduces_the_positive_weight_score() -> None:
    rubric = (
        RubricCriterion(id="quality", description="The artifact is correct", weight=2),
        RubricCriterion(id="penalty", description="The artifact contains an error", weight=-1),
    )
    verdict = SubmissionVerdict(
        structurally_valid=True,
        items=(
            RubricItemVerdict(criterion_id="quality", score=1, reason="correct"),
            RubricItemVerdict(criterion_id="penalty", score=1, reason="error present"),
        ),
    )
    legs = tuple(PanelLeg(name, DIGESTS[0]) for name in ("one", "two", "three"))

    score = CalibrationJudge(legs, "seed")._normalized_score(verdict, rubric)

    assert score == 0.5


async def test_operator_cli_runs_three_external_judges_and_writes_grade(tmp_path: Path) -> None:
    request = _request()
    snapshot = _snapshot(tmp_path)
    request_path = tmp_path / "request.json"
    judges_path = tmp_path / "judges.json"
    output_path = tmp_path / "grade.json"
    request_path.write_text(request.model_dump_json())
    judges_path.write_text(json.dumps(_commands(request.left.digest)))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "evals.gdpval_100.grading",
        "--snapshot",
        str(snapshot),
        "--request",
        str(request_path),
        "--judges",
        str(judges_path),
        "--out",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout, stderr)
    grade = TaskGrade.model_validate_json(output_path.read_bytes())
    assert grade.valid
    assert grade.outcome == "left"
    assert grade.left_score == 0.9
    assert grade.right_score == pytest.approx(0.4)
    assert {leg.judge for leg in grade.legs} == {"judge-0", "judge-1", "judge-2"}
    assert {item.criterion_id for leg in grade.legs for item in leg.left_items} == {RUBRIC_ID}


async def test_operator_cli_fails_closed_when_one_judge_command_fails(tmp_path: Path) -> None:
    request = _request()
    snapshot = _snapshot(tmp_path)
    commands = _commands(request.left.digest)
    commands[1] = {
        "name": "judge-1",
        "argv": [sys.executable, "-c", "raise SystemExit(7)"],
        "timeout_seconds": 10,
    }
    request_path = tmp_path / "request.json"
    judges_path = tmp_path / "judges.json"
    output_path = tmp_path / "grade.json"
    request_path.write_text(request.model_dump_json())
    judges_path.write_text(json.dumps(commands))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "evals.gdpval_100.grading",
        "--snapshot",
        str(snapshot),
        "--request",
        str(request_path),
        "--judges",
        str(judges_path),
        "--out",
        str(output_path),
    )
    assert await process.wait() == 1
    grade = TaskGrade.model_validate_json(output_path.read_bytes())
    assert not grade.valid
    assert grade.outcome == "invalid"
    assert grade.failure_reasons[0].startswith("judge-1:")


async def test_operator_cli_requires_exactly_three_judge_commands(tmp_path: Path) -> None:
    request = _request()
    snapshot = _snapshot(tmp_path)
    request_path = tmp_path / "request.json"
    judges_path = tmp_path / "judges.json"
    output_path = tmp_path / "grade.json"
    request_path.write_text(request.model_dump_json())
    judges_path.write_text(json.dumps(_commands(request.left.digest)[:2]))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "evals.gdpval_100.grading",
        "--snapshot",
        str(snapshot),
        "--request",
        str(request_path),
        "--judges",
        str(judges_path),
        "--out",
        str(output_path),
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 2
    assert b"exactly three judge commands are required" in stderr
    assert not output_path.exists()


async def test_operator_cli_rejects_an_oversized_request_before_judging(tmp_path: Path) -> None:
    request = _request()
    snapshot = _snapshot(tmp_path)
    request_path = tmp_path / "request.json"
    judges_path = tmp_path / "judges.json"
    output_path = tmp_path / "grade.json"
    request_path.write_bytes(b" " * (MAX_CLI_REQUEST_BYTES + 1))
    judges_path.write_text(json.dumps(_commands(request.left.digest)))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "evals.gdpval_100.grading",
        "--snapshot",
        str(snapshot),
        "--request",
        str(request_path),
        "--judges",
        str(judges_path),
        "--out",
        str(output_path),
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 2
    assert b"calibration request exceeds byte limit" in stderr
    assert not output_path.exists()


async def test_operator_cli_fails_closed_on_oversized_judge_output(tmp_path: Path) -> None:
    request = _request()
    snapshot = _snapshot(tmp_path)
    commands = _commands(request.left.digest)
    commands[1] = {
        "name": "judge-1",
        "argv": [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * (2 * 1024 * 1024 + 1))",
        ],
        "timeout_seconds": 10,
    }
    request_path = tmp_path / "request.json"
    judges_path = tmp_path / "judges.json"
    output_path = tmp_path / "nested" / "grade.json"
    request_path.write_text(request.model_dump_json())
    judges_path.write_text(json.dumps(commands))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "evals.gdpval_100.grading",
        "--snapshot",
        str(snapshot),
        "--request",
        str(request_path),
        "--judges",
        str(judges_path),
        "--out",
        str(output_path),
    )
    assert await process.wait() == 1
    grade = TaskGrade.model_validate_json(output_path.read_bytes())
    assert not grade.valid
    assert grade.failure_reasons == (
        "judge-1: invalid structured verdict: judge output exceeds byte limit",
    )
    assert not tuple(output_path.parent.glob("*.tmp"))
