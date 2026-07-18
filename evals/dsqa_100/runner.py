import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, ValidationError

from evals.dsqa_100.models import SUBSET_SIZE, SnapshotCase
from evals.dsqa_100.snapshot import load_snapshot
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    run_capability_case,
)
from evals.harness.harness import EvalMetric, EvalReport, Json, digest_payload
from evals.harness.judge import JudgeLeg
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget
from ufo.sdk.models import Message

type Workflow = Literal["general", "research"]
type Capability = Literal["core", "search", "browser"]
type Outcome = Literal[
    "fully_correct",
    "fully_incorrect",
    "partially_correct",
    "correct_with_extraneous",
]

DSQA_JUDGE_MODEL = "google/gemini-2.5-flash"
DSQA_JUDGE_REVISION = "deepsearchqa-kaggle-v4-starter-openrouter"
DSQA_JUDGE_MAX_TOKENS = 8_192
DSQA_JUDGE_REASONING: Literal["off"] = "off"
MAX_PROBLEM_CHARS = 12_000
MAX_GOLD_ANSWER_CHARS = 4_000
MAX_CANDIDATE_ANSWER_CHARS = 24_000
DATA_DIR = Path(__file__).parent / "data"
JUDGE_PROMPT_FILE = DATA_DIR / "DEEPSEARCHQA_JUDGE_PROMPT.txt"
OFFICIAL_JUDGE_PROMPT = JUDGE_PROMPT_FILE.read_text().removesuffix("\n")
DSQA_JUDGE_PROMPT_DIGEST = f"sha256:{hashlib.sha256(OFFICIAL_JUDGE_PROMPT.encode()).hexdigest()}"

RESEARCH_WORKFLOW = (
    "Use the available web research capabilities to answer the question. Verify every required "
    "item against sources and check the answer for completeness before responding."
)


class AnswerCorrectness(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    explanation: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
    ] = Field(alias="Explanation")
    correctness_details: dict[str, StrictBool] = Field(alias="Correctness Details", min_length=1)
    excessive_answers: tuple[str, ...] = Field(
        default=(), alias="Excessive Answers", max_length=200
    )


class JudgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer_correctness: AnswerCorrectness = Field(alias="Answer Correctness")


@dataclass(frozen=True)
class DSQAScore:
    correct: int
    expected: int
    excessive: int
    precision: float
    recall: float
    f1: float
    outcome: Outcome


@dataclass(frozen=True)
class DSQAGrader:
    case: SnapshotCase
    judge: JudgeLeg

    @property
    def grading(self) -> str:
        return (
            "the official DeepSearchQA judge rates the answer against the gold answer "
            f"({self.case.answer_type}); passes only when fully correct — every expected "
            "answer present and none excessive"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        metadata: dict[str, Json] = {
            "category": self.case.category,
            "pressureBand": self.case.pressure_band,
            "toolPressureScore": self.case.tool_pressure_score,
            "goldAnswer": self.case.answer,
            "answerType": self.case.answer_type,
        }
        if not output.response.strip():
            return CapabilityVerdict(
                False,
                "candidate answer is empty",
                {**metadata, "judgeStatus": "empty_candidate"},
            )
        if len(output.response) > MAX_CANDIDATE_ANSWER_CHARS:
            return CapabilityVerdict(
                False,
                f"candidate answer exceeds {MAX_CANDIDATE_ANSWER_CHARS} characters",
                {**metadata, "judgeStatus": "candidate_too_long"},
            )
        prompt = self._judge_prompt(output.response)
        try:
            raw = await self.judge.complete("", (Message(role="user", content=prompt),))
        except Exception as error:
            return CapabilityVerdict(
                False,
                f"DeepSearchQA judge failed with {type(error).__name__}",
                {**metadata, "judgeStatus": "error"},
            )
        try:
            response = self._parse_judge_response(raw)
        except ValidationError:
            return CapabilityVerdict(
                False,
                f"DeepSearchQA judge returned an invalid structured rating: {raw.strip()[:200]}",
                {**metadata, "judgeStatus": "invalid"},
            )
        score = self._score(response.answer_correctness)
        rating = response.answer_correctness
        evidence: dict[str, Json] = {
            **metadata,
            "judgeStatus": "rated",
            "judgeExplanation": rating.explanation,
            "correctnessDetails": dict(rating.correctness_details),
            "excessiveAnswers": list(rating.excessive_answers),
            "correctCount": score.correct,
            "expectedCount": score.expected,
            "excessiveCount": score.excessive,
            "precision": score.precision,
            "recall": score.recall,
            "f1": score.f1,
            "outcome": score.outcome,
        }
        return CapabilityVerdict(
            score.outcome == "fully_correct",
            (
                f"{score.outcome}: {score.correct}/{score.expected} expected answers, "
                f"{score.excessive} excessive"
            ),
            evidence,
        )

    def _judge_prompt(self, response: str) -> str:
        if len(self.case.problem) > MAX_PROBLEM_CHARS:
            raise ValueError(
                f"DSQA problem {self.case.example_id} exceeds {MAX_PROBLEM_CHARS} characters"
            )
        if len(self.case.answer) > MAX_GOLD_ANSWER_CHARS:
            raise ValueError(
                f"DSQA answer {self.case.example_id} exceeds {MAX_GOLD_ANSWER_CHARS} characters"
            )
        return OFFICIAL_JUDGE_PROMPT.format(
            prompt=self.case.problem.strip(),
            prompt_type=self.case.answer_type.strip(),
            answer=self.case.answer.strip(),
            response=response.strip(),
        )

    def _parse_judge_response(self, raw: str) -> JudgeResponse:
        body = raw.strip()
        marker = "```json"
        start = body.find(marker)
        if start >= 0:
            body = body[start + len(marker) :]
            end = body.rfind("```")
            if end >= 0:
                body = body[:end]
        return JudgeResponse.model_validate_json(body.strip())

    def _score(self, rating: AnswerCorrectness) -> DSQAScore:
        expected = len(rating.correctness_details)
        correct = sum(rating.correctness_details.values())
        excessive = len(rating.excessive_answers)
        false_negative = expected - correct
        precision = correct / (correct + excessive) if correct + excessive else 0.0
        recall = correct / (correct + false_negative) if correct + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if correct == 0:
            outcome: Outcome = "fully_incorrect"
        elif correct == expected and excessive:
            outcome = "correct_with_extraneous"
        elif correct == expected:
            outcome = "fully_correct"
        else:
            outcome = "partially_correct"
        return DSQAScore(correct, expected, excessive, precision, recall, f1, outcome)


@dataclass(frozen=True)
class DSQA100Leaf:
    name: str
    workflow: Workflow
    capability: Capability
    pack: str


DSQA_100_LEAVES = (
    DSQA100Leaf("dsqa_100.general.core", "general", "core", "dsqa_core"),
    DSQA100Leaf("dsqa_100.research.core", "research", "core", "dsqa_core"),
    DSQA100Leaf("dsqa_100.general.search", "general", "search", "dsqa_search"),
    DSQA100Leaf("dsqa_100.research.search", "research", "search", "dsqa_search"),
    DSQA100Leaf("dsqa_100.general.browser", "general", "browser", "dsqa_browser"),
    DSQA100Leaf("dsqa_100.research.browser", "research", "browser", "dsqa_browser"),
)


@dataclass(frozen=True)
class DSQA100Run:
    tasks: tuple[EvalTask, ...]

    def tasks_for_pack(self, pack: str | None) -> tuple[EvalTask, ...]:
        names = {leaf.name for leaf in DSQA_100_LEAVES if leaf.pack == pack}
        if not names:
            expected = ", ".join(sorted({leaf.pack for leaf in DSQA_100_LEAVES}))
            raise ValueError(f"dsqa_100 requires one of these packs: {expected}")
        return tuple(task for task in self.tasks if task.name in names)

    def validate_pack(self, tasks: tuple[EvalTask, ...], pack: str | None) -> None:
        names = {task.name for task in tasks}
        expected = {leaf.pack for leaf in DSQA_100_LEAVES if leaf.name in names}
        if expected != {pack}:
            selected = ", ".join(task.name for task in tasks)
            raise ValueError(
                f"dsqa_100 leaves {selected} require one pack, found expected {sorted(expected)} "
                f"and configured {pack!r}"
            )


def load_dsqa_100(snapshot_root: Path) -> DSQA100Run:
    snapshot = load_snapshot(snapshot_root)
    tasks = tuple(_task(snapshot.cases, snapshot.manifest.digest, leaf) for leaf in DSQA_100_LEAVES)
    case_sets = {task.cases for task in tasks}
    if len(case_sets) != 1 or len(next(iter(case_sets))) != SUBSET_SIZE:
        raise ValueError(f"every dsqa_100 leaf must contain the same {SUBSET_SIZE} cases")
    return DSQA100Run(tasks)


def _task(cases: tuple[SnapshotCase, ...], snapshot_digest: str, leaf: DSQA100Leaf) -> EvalTask:
    messages = tuple(_message(case.problem, leaf.workflow) for case in cases)
    payload: dict[str, Json] = {
        "runner": "dsqa_100",
        "snapshot": snapshot_digest,
        "leaf": leaf.name,
        "workflow": leaf.workflow,
        "capability": leaf.capability,
        "pack": leaf.pack,
        "judgeModel": DSQA_JUDGE_MODEL,
        "judgeRevision": DSQA_JUDGE_REVISION,
        "judgePromptDigest": DSQA_JUDGE_PROMPT_DIGEST,
        "judgeMaxTokens": DSQA_JUDGE_MAX_TOKENS,
        "judgeReasoning": DSQA_JUDGE_REASONING,
        "cases": [
            {
                "name": f"dsqa/{case.example_id}",
                "problemDigest": case.problem_sha256,
                "message": message,
            }
            for case, message in zip(cases, messages, strict=True)
        ],
    }
    digest = digest_payload(payload)

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        if target.judge is None:
            raise RuntimeError("dsqa_100 requires the official model judge")
        capability_cases = tuple(
            CapabilityCase(
                name=f"dsqa/{case.example_id}",
                message=message,
                grader=DSQAGrader(case, target.judge),
                digest_tag=(
                    f"{snapshot_digest}:{leaf.name}:{DSQA_JUDGE_REVISION}:"
                    f"{DSQA_JUDGE_PROMPT_DIGEST}:{case.example_id}:{case.problem_sha256}"
                ),
            )
            for case, message in zip(cases, messages, strict=True)
        )
        results = await gather_cases(
            slots, tuple(partial(run_capability_case, case, target) for case in capability_cases)
        )
        return _with_metrics(
            EvalReport(name=leaf.name, suite="dsqa_100", digest=digest, cases=results)
        )

    return EvalTask(
        name=leaf.name,
        suite="dsqa_100",
        digest=digest,
        cases=tuple(f"dsqa/{case.example_id}" for case in cases),
        run=run,
        judge_model=DSQA_JUDGE_MODEL,
        judge_revision=DSQA_JUDGE_REVISION,
        judge_max_tokens=DSQA_JUDGE_MAX_TOKENS,
        judge_reasoning=DSQA_JUDGE_REASONING,
        pin_runtime=True,
    )


def _message(problem: str, workflow: Workflow) -> str:
    if workflow == "general":
        return problem
    return f"{RESEARCH_WORKFLOW}\n\n{problem}"


def _with_metrics(report: EvalReport) -> EvalReport:
    scores: list[dict[str, Json]] = []
    for case in report.cases:
        attempts = case.evidence["attempts"]
        selected = case.evidence["selectedAttempt"]
        if (
            not isinstance(attempts, list)
            or isinstance(selected, bool)
            or not isinstance(selected, int)
        ):
            raise TypeError("dsqa_100 attempt evidence is invalid")
        attempt = attempts[selected]
        if not isinstance(attempt, dict):
            raise TypeError("dsqa_100 selected attempt is invalid")
        grader = attempt.get("grader")
        if isinstance(grader, dict) and grader.get("judgeStatus") == "rated":
            scores.append(grader)
    if not scores:
        return report.model_copy(update={"metrics": (EvalMetric(name="rated", value=0.0),)})
    count = len(scores)
    outcomes = Counter(str(score["outcome"]) for score in scores)
    metrics = (
        EvalMetric(name="f1", value=_mean(scores, "f1")),
        EvalMetric(name="precision", value=_mean(scores, "precision")),
        EvalMetric(name="recall", value=_mean(scores, "recall")),
        EvalMetric(name="fully_correct", value=outcomes["fully_correct"] / count),
        EvalMetric(name="fully_incorrect", value=outcomes["fully_incorrect"] / count),
        EvalMetric(name="partially_correct", value=outcomes["partially_correct"] / count),
        EvalMetric(
            name="correct_with_extraneous",
            value=outcomes["correct_with_extraneous"] / count,
        ),
        EvalMetric(name="rated", value=count / len(report.cases)),
    )
    return report.model_copy(update={"metrics": metrics})


def _mean(scores: list[dict[str, Json]], field: str) -> float:
    values: list[float] = []
    for score in scores:
        value = score[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"dsqa_100 {field} evidence is invalid")
        values.append(float(value))
    return sum(values) / len(values)
