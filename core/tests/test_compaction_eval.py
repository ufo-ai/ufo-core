from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evals.compaction.build import SnapshotBuild
from evals.compaction.models import CompactionCase, PlantedFact, estimate_tokens, message_text
from evals.compaction.runner import SEED_MESSAGE, CompactionSuite, _grade_probe, load_compaction
from evals.compaction.snapshot import load_snapshot, write_snapshot
from evals.compaction.target import CompactionTarget
from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.target import TargetResult
from ufo.blob import FilesystemBlobStore
from ufo.loop.compaction import MAX_REFERENCE_PATHS, TOOL_OUTPUT_PATH_RE, CompactionSummary
from ufo.models.interface import ImageBlock, Message, ModelEvent, ModelRequest, TextDelta
from ufo.schema.records import Usage
from ufo.transcript import decode, transcript_key

TEST_TARGET_TOKENS = 9_000
TEST_TRIGGER_TOKENS = int(TEST_TARGET_TOKENS * 0.9)


@dataclass(frozen=True)
class ScriptedSummaryModel:
    """Returns a fixed structured summary, so survival grading is asserted against a known keep."""

    summary: CompactionSummary

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=self.summary.model_dump_json())
        yield Usage(input_tokens=10, output_tokens=5)


@dataclass(frozen=True)
class ArtifactOnlyTarget:
    compaction: CompactionTarget


@dataclass
class ScriptedStepTarget:
    compaction: CompactionTarget
    conversation_id: UUID
    answers: dict[str, TargetResult]
    steps: list[str] = field(default_factory=list)

    @property
    def conversations(self) -> "ScriptedStepTarget":
        return self

    async def open(self, case_name: str, member_key: str | None = None) -> UUID:
        return self.conversation_id

    async def step(self, conversation_id: UUID, message: str, idempotency_key: str) -> TargetResult:
        self.steps.append(message)
        return self.answers[message]


def _corpus(root: Path) -> None:
    (root / "docs").mkdir()
    body = "\n".join(
        f"Filler paragraph {index} describing the platform review in plain words."
        for index in range(400)
    )
    (root / "spec.md").write_text(body, encoding="utf-8")
    (root / "docs" / "notes.md").write_text(body.replace("paragraph", "note"), encoding="utf-8")


def _summary(**overrides: object) -> CompactionSummary:
    base: dict[str, object] = {
        "intent": "quarterly configuration review",
        "current_work": "walking the corpus",
        "next_step": "continue the review",
    }
    base.update(overrides)
    return CompactionSummary.model_validate(base)


def _lab(blob_root: Path, summary: CompactionSummary) -> CompactionTarget:
    return CompactionTarget(
        client=ScriptedSummaryModel(summary),
        model="claude-opus-4-8",
        blob=FilesystemBlobStore(root=blob_root),
    )


def _clean(answer: str, calls: tuple[ToolInvocation, ...] = ()) -> TargetResult:
    return TargetResult(CapabilityOutput(answer, calls), clean=True)


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("corpus")
    _corpus(repo)
    out = tmp_path_factory.mktemp("snapshot")
    SnapshotBuild(repo=repo, out=out, target_tokens=TEST_TARGET_TOKENS).build()
    return out


async def test_build_is_deterministic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _corpus(repo)
    first = SnapshotBuild(repo=repo, out=tmp_path / "one", target_tokens=TEST_TARGET_TOKENS).build()
    second = SnapshotBuild(
        repo=repo, out=tmp_path / "two", target_tokens=TEST_TARGET_TOKENS
    ).build()
    assert first == second
    assert (tmp_path / "one" / "cases.jsonl.gz").read_bytes() == (
        tmp_path / "two" / "cases.jsonl.gz"
    ).read_bytes()


async def test_windows_cross_the_trigger_and_alternate_strictly(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    leaves = {case.leaf for case in snapshot.cases}
    assert leaves == {
        "overload",
        "buried",
        "supersession",
        "chain",
        "reference",
        "image",
        "behavior",
    }
    for case in snapshot.cases:
        assert estimate_tokens(case.messages) >= TEST_TARGET_TOKENS
        assert case.messages[0].role == "user"
        assert case.messages[-1].role == "assistant"
        roles = [message.role for message in case.messages]
        assert all(earlier != later for earlier, later in pairwise(roles))


async def test_planted_literals_appear_exactly_where_declared(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    for case in snapshot.cases:
        window_text = "\n".join(message_text(message) for message in case.messages)
        for fact in case.facts:
            match fact.kind:
                case "decision" | "buried" | "control" | "distractor" | "superseded":
                    assert window_text.count(fact.literal) == 1
                case "reference":
                    assert fact.literal not in window_text
                    assert fact.literal in fact.body
                    assert TOOL_OUTPUT_PATH_RE.search(window_text) is not None
                    assert fact.path in window_text
                case "image":
                    assert fact.literal not in window_text
            if fact.stale_literal:
                assert window_text.count(fact.stale_literal) == 1


async def test_fact_statements_share_no_single_surface_marker(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "overload")
    literals = {fact.literal for fact in case.facts if fact.kind == "decision"}
    statements = [
        message.content
        for message in case.messages
        if message.role == "user"
        and isinstance(message.content, str)
        and any(literal in message.content for literal in literals)
    ]
    assert len(statements) == len(literals)
    marked = sum(1 for statement in statements if statement.startswith("For the record:"))
    assert marked / len(statements) <= 0.15
    fact_free = [
        message.content
        for message in case.messages[1:]
        if message.role == "user"
        and isinstance(message.content, str)
        and not any(
            fact.literal in message.content
            or (fact.stale_literal and fact.stale_literal in message.content)
            for fact in case.facts
        )
    ]
    assert fact_free
    assert not any(char.isdigit() for chatter in fact_free for char in chatter)


async def test_buried_facts_live_inside_tool_results(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "buried")
    for fact in case.facts:
        if fact.kind != "buried":
            continue
        homes = [message for message in case.messages if fact.literal in message_text(message)]
        assert len(homes) == 1
        assert not isinstance(homes[0].content, str)
        home_text = message_text(homes[0])
        assert not home_text.strip().endswith(fact.literal)


async def test_buried_recall_grades_against_the_spoken_comparator(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "buried")
    buried = [fact for fact in cases[0].facts if fact.kind == "buried"]
    kept = sorted(buried, key=lambda fact: fact.weight, reverse=True)[: len(buried) // 4]
    summary = _summary(decisions=tuple(f"operative value {fact.literal}" for fact in kept))
    suite = CompactionSuite(
        leaf="buried", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path, summary)))  # type: ignore[arg-type]
    case = report.cases[0]
    expected = sum(fact.weight for fact in kept) / sum(fact.weight for fact in buried)
    assert case.evidence["buriedRecall"] == pytest.approx(expected)
    assert case.evidence["weightedRecall"] == pytest.approx(0.0)
    assert case.passed == (expected >= 0.20)
    names = {metric.name for metric in report.metrics}
    assert "buried_recall" in names


async def test_reference_leaf_splits_the_harvester_cap_from_model_carry(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "reference")
    references = [fact for fact in cases[0].facts if fact.kind == "reference"]
    assert len(references) > MAX_REFERENCE_PATHS
    weights = [fact.weight for fact in references]
    assert weights == sorted(weights, reverse=True)
    total = sum(weights)
    recent = references[-MAX_REFERENCE_PATHS:]
    heavy = references[:2]

    suite = CompactionSuite(
        leaf="reference", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    dropped = await suite.run(ArtifactOnlyTarget(_lab(tmp_path / "drop", _summary())))  # type: ignore[arg-type]
    floor = sum(fact.weight for fact in recent) / total
    case = dropped.cases[0]
    assert case.evidence["harvestedCount"] == MAX_REFERENCE_PATHS
    assert case.evidence["carriedCount"] == 0
    assert case.evidence["weightedReferenceCoverage"] == pytest.approx(floor)
    assert not case.passed

    carrying = _summary(
        files=tuple({"path": fact.path, "why": "heavy soak report"} for fact in heavy)
    )
    saved = await suite.run(ArtifactOnlyTarget(_lab(tmp_path / "carry", carrying)))  # type: ignore[arg-type]
    case = saved.cases[0]
    covered = sum(fact.weight for fact in (*recent, *heavy)) / total
    assert case.evidence["carriedCount"] == len(heavy)
    assert case.evidence["weightedReferenceCoverage"] == pytest.approx(covered)
    assert case.passed
    names = {metric.name for metric in saved.metrics}
    assert "weighted_reference_coverage" in names


async def test_supersession_states_the_stale_value_before_the_correction(
    snapshot_dir: Path,
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "supersession")
    texts = [message_text(message) for message in case.messages]
    for fact in case.facts:
        if fact.kind != "superseded":
            continue
        stale_at = next(index for index, text in enumerate(texts) if fact.stale_literal in text)
        corrected_at = next(index for index, text in enumerate(texts) if fact.literal in text)
        assert stale_at < corrected_at


async def test_image_case_carries_the_fact_only_as_pixels(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "image")
    blocks = [
        block
        for message in case.messages
        if not isinstance(message.content, str)
        for block in message.content
    ]
    assert any(isinstance(block, ImageBlock) for block in blocks)


async def test_load_compaction_builds_one_pinned_task_per_leaf(snapshot_dir: Path) -> None:
    run = load_compaction(snapshot_dir)
    names = [task.name for task in run.tasks]
    assert names == [
        "compaction.overload",
        "compaction.buried",
        "compaction.supersession",
        "compaction.chain",
        "compaction.reference",
        "compaction.image",
        "compaction.behavior",
    ]
    assert len({task.digest for task in run.tasks}) == len(run.tasks)


async def test_overload_weighted_recall_measures_the_kept_subset(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "overload")
    decisions = [fact for fact in cases[0].facts if fact.kind == "decision"]
    kept = sorted(decisions, key=lambda fact: fact.weight, reverse=True)[: len(decisions) // 2]
    distractor = next(fact for fact in cases[0].facts if fact.kind == "distractor")
    summary = _summary(
        decisions=(
            *(f"kept value {fact.literal}" for fact in kept),
            f"vendor figure {distractor.literal}",
        ),
    )
    suite = CompactionSuite(
        leaf="overload", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path, summary)))  # type: ignore[arg-type]
    expected = sum(fact.weight for fact in kept) / sum(fact.weight for fact in decisions)
    first = report.cases[0]
    assert first.evidence["weightedRecall"] == pytest.approx(expected)
    assert first.evidence["distractorRate"] == pytest.approx(1 / 25)
    assert first.passed == (expected >= 0.30)
    metric = next(metric for metric in report.metrics if metric.name == "weighted_recall")
    assert 0.0 < metric.value < 1.0


async def test_supersession_fails_when_stale_values_survive(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "supersession")
    superseded = [fact for fact in cases[0].facts if fact.kind == "superseded"]
    stale_kept = superseded[:6]
    summary = _summary(
        decisions=tuple(f"current value {fact.literal}" for fact in superseded)
        + tuple(f"earlier note {fact.stale_literal}" for fact in stale_kept),
    )
    suite = CompactionSuite(
        leaf="supersession", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path, summary)))  # type: ignore[arg-type]
    case = report.cases[0]
    assert case.evidence["correctionRecall"] == pytest.approx(1.0)
    assert case.evidence["staleRate"] == pytest.approx(len(stale_kept) / len(superseded))
    assert not case.passed
    assert not report.passed


async def test_chain_grades_every_generation(snapshot_dir: Path, tmp_path: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "chain")
    critical = [fact for fact in cases[0].facts if fact.kind == "decision" and fact.weight >= 4]
    summary = _summary(decisions=tuple(f"kept {fact.literal}" for fact in critical))
    suite = CompactionSuite(
        leaf="chain", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path, summary)))  # type: ignore[arg-type]
    case = report.cases[0]
    generations = case.evidence["generations"]
    assert isinstance(generations, list) and len(generations) == 3
    assert case.passed
    names = {metric.name for metric in report.metrics}
    assert {"survival_gen1", "survival_gen2", "survival_gen3"} <= names


async def test_image_leaf_is_red_while_images_die_at_the_boundary(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "image")
    suite = CompactionSuite(
        leaf="image", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path, _summary())))  # type: ignore[arg-type]
    case = report.cases[0]
    assert not case.passed
    assert "image-borne fact died" in case.reason
    metric = next(metric for metric in report.metrics if metric.name == "image_survival")
    assert metric.value == 0.0


async def test_behavior_probes_grade_answers_and_reread_trajectories(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    probes = {probe.id: probe for probe in case.probes}
    lab = _lab(tmp_path, _summary())
    conversation_id = uuid4()
    await lab.compactor(conversation_id, TEST_TRIGGER_TOKENS).maybe_compact(case.messages)
    reference = probes["reference"]
    answers = {
        SEED_MESSAGE: _clean("ready"),
        probes["recall"].question: _clean(f"We set it to {probes['recall'].expect_literals[0]}."),
        probes["supersession"].question: _clean(
            f"It is {probes['supersession'].expect_literals[0]}, previously "
            f"{probes['supersession'].forbid_literals[0]}."
        ),
        reference.question: _clean(
            f"The peak was {reference.expect_literals[0]}.",
            (
                ToolInvocation(
                    name="bash",
                    input={"command": f"cat {reference.expect_read_path}"},
                    result="report",
                    has_result=True,
                ),
            ),
        ),
        probes["control"].question: _clean("I could not find that value."),
    }
    target = ScriptedStepTarget(lab, conversation_id, answers)
    suite = CompactionSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target)  # type: ignore[arg-type]
    by_name = {result.name: result for result in report.cases}
    assert by_name[f"{case.id}.recall"].passed
    assert not by_name[f"{case.id}.supersession"].passed
    assert "superseded" in by_name[f"{case.id}.supersession"].reason
    assert by_name[f"{case.id}.reference"].passed
    assert not by_name[f"{case.id}.control"].passed
    assert "compaction" in by_name[f"{case.id}.recall"].evidence
    metric = next(metric for metric in report.metrics if metric.name == "probe_pass_rate")
    assert metric.value == pytest.approx(0.5)
    stored = decode(await lab.blob.get(transcript_key(conversation_id)))
    assert stored.seq == 1
    assert stored.messages == case.messages
    reference_fact = next(fact for fact in case.facts if fact.kind == "reference")
    key = (
        f"conversations/{conversation_id}/workspace/"
        f"{reference_fact.path.removeprefix('/workspace/')}"
    )
    assert (await lab.blob.get(key)).decode() == reference_fact.body


async def test_behavior_probes_fail_when_compaction_never_fired(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    lab = _lab(tmp_path, _summary())
    answers: dict[str, TargetResult] = {SEED_MESSAGE: _clean("ready")}
    for probe in case.probes:
        answers[probe.question] = _clean(
            " ".join((*probe.expect_literals, "and the file", probe.expect_read_path)),
            (
                ToolInvocation(
                    name="bash",
                    input={"command": f"cat {probe.expect_read_path}"},
                    result="x",
                    has_result=True,
                ),
            )
            if probe.expect_read_path
            else (),
        )
    target = ScriptedStepTarget(lab, uuid4(), answers)
    suite = CompactionSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target)  # type: ignore[arg-type]
    assert not report.passed
    assert all("compaction never fired" in result.reason for result in report.cases)


async def test_grade_probe_reports_unclean_turns(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    probe = case.probes[0]
    outcome = TargetResult(CapabilityOutput("", ()), clean=False, failure_reason="turn failed")
    result = _grade_probe(case, probe, outcome)
    assert not result.passed
    assert result.reason == "turn failed"


async def test_materialize_rejects_paths_outside_the_workspace(tmp_path: Path) -> None:
    lab = _lab(tmp_path, _summary())
    with pytest.raises(ValueError, match="outside the workspace"):
        await lab.materialize(
            uuid4(),
            (Message(role="user", content="hello"),),
            {"/etc/passwd": "nope"},
        )


async def test_snapshot_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    fact = PlantedFact(id="f", kind="decision", literal="4242")
    case = CompactionCase(
        id="dup",
        leaf="overload",
        messages=(Message(role="user", content="x"), Message(role="assistant", content="y")),
        facts=(fact,),
    )
    with pytest.raises(ValueError, match="duplicate case ids"):
        write_snapshot(
            tmp_path,
            builder_digest=f"sha256:{'0' * 64}",
            target_tokens=100,
            corpus=(),
            cases=(case, case),
        )
