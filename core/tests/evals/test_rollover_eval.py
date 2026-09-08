import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from ufo_ext_context_rollover.rollover import (
    RECOVERY_RESERVE_TOKENS,
    ROLLOVER_BUFFER_TOKENS,
    ContextRollover,
)
from ufo_testsupport.models import serving_model

from evals.harness.capability import CapabilityOutput, EvalTrajectory, ToolInvocation
from evals.harness.target import TargetResult
from evals.rollover.build import REAL_SKELETONS, SKELETON_SUFFIX, SnapshotBuild
from evals.rollover.models import PlantedFact, RolloverCase, estimate_tokens, message_text
from evals.rollover.runner import (
    SEED_MESSAGE,
    GenerationGrade,
    RolloverSuite,
    _grade_probe,
    load_rollover,
)
from evals.rollover.snapshot import load_snapshot, write_snapshot
from evals.rollover.target import FileJournal, RolloverTarget
from ufo.blob import FilesystemBlobStore
from ufo.harness.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.runtime.turns.transcript import Conversation, decode, encode, transcript_key

TEST_TARGET_TOKENS = 9_000
TEST_TRIGGER_TOKENS = int(TEST_TARGET_TOKENS * 0.9)
TEST_CONTEXT_WINDOW = TEST_TRIGGER_TOKENS + RECOVERY_RESERVE_TOKENS + ROLLOVER_BUFFER_TOKENS


@dataclass(frozen=True)
class RefusingModel:
    """A rollover spends no model call, so every leaf grades a boundary no model authored."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        raise AssertionError("the rollover suite must never call a model")
        yield  # pragma: no cover - keeps the signature an async iterator


@dataclass(frozen=True)
class ArtifactOnlyTarget:
    rollover: RolloverTarget


@dataclass
class ScriptedStepTarget:
    rollover: RolloverTarget
    conversation_id: UUID
    answers: dict[str, TargetResult]
    steps: list[str] = field(default_factory=list)

    @property
    def conversations(self) -> "ScriptedStepTarget":
        return self

    async def open(
        self, case_name: str, member_key: str | None = None, *_: object, **__: object
    ) -> UUID:
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


def _lab(blob_root: Path, context_window: int = TEST_CONTEXT_WINDOW) -> RolloverTarget:
    """The default window puts the live rollover line exactly at `TEST_TRIGGER_TOKENS`, so a
    probe-model window built at `TEST_TARGET_TOKENS` clears it just as a full-scale one clears
    production's."""
    serving = serving_model(cast(ModelClient, RefusingModel()))
    serving.spec = replace(serving.spec, context_window=context_window)
    return RolloverTarget(
        serving=serving,
        blob=FilesystemBlobStore(root=blob_root),
        workspace_root=blob_root / "workspaces",
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


async def test_the_estimator_mirror_matches_the_live_rollover(tmp_path: Path) -> None:
    """`estimate_tokens`/`message_text` claim to mirror `ContextRollover.window.tokens`/`_text`
    exactly, and a built window only provably crosses the line while that holds. Asserted over one
    window carrying each block kind, so a term or arm that changes on one side alone fails here."""
    window = (
        Message(role="user", content="plan the migration"),
        Message(
            role="assistant",
            content=(
                RedactedThinkingBlock(data="e" * 40),
                ThinkingBlock(thinking="weigh the options", signature="s" * 40),
                ReasoningItemBlock(
                    id="rs_1", encrypted_content="g" * 40, summary=("weigh the item",)
                ),
                TextBlock(text="reading the tree"),
                ImageBlock(source=ImageSource(media_type="image/png", data="AAAA")),
                ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
            ),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(tool_use_id="t1", content="README.md"),
                ToolResultBlock(
                    tool_use_id="t2",
                    content=(
                        TextBlock(text="chart.png"),
                        ImageBlock(source=ImageSource(media_type="image/png", data="BBBB")),
                    ),
                ),
            ),
        ),
    )
    live = ContextRollover(
        serving=serving_model(cast(ModelClient, RefusingModel())),
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=uuid4(),
        journal=FileJournal(tmp_path / "history.jsonl"),
    )
    assert [message_text(message) for message in window] == [
        live._text(message) for message in window
    ]
    assert estimate_tokens(window) == live.window.tokens(window)


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


async def test_buried_facts_are_recovered_from_the_journal_the_record_addresses(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """A fact buried mid-result is what a summary boundary lost first. The rollover keeps every one
    of them: the record itself states almost none, and the journal range it addresses holds them
    all, so the leaf is green at the 100% bar while the record-carry split stays visible."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "buried")
    buried = [fact for fact in cases[0].facts if fact.kind == "buried"]
    suite = RolloverSuite(
        leaf="buried", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    case = report.cases[0]
    assert case.evidence["buriedRecall"] == pytest.approx(1.0)
    assert case.evidence["weightedRecall"] == pytest.approx(1.0)
    assert case.passed
    facts = case.evidence["facts"]
    assert isinstance(facts, list)
    graded = {
        entry["id"]: entry
        for entry in facts
        if isinstance(entry, dict) and entry["kind"] == "buried"
    }
    assert len(graded) == len(buried)
    assert all(entry["journaled"] for entry in graded.values())
    assert not any(entry["carried"] for entry in graded.values())
    names = {metric.name for metric in report.metrics}
    assert "buried_recall" in names


async def test_reference_leaf_addresses_every_path_through_the_history(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """Under a summary boundary the early heavy references were permanent loss. The history lines
    the record names carry every offload notice, so coverage is total."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "reference")
    references = [fact for fact in cases[0].facts if fact.kind == "reference"]
    weights = [fact.weight for fact in references]
    assert weights == sorted(weights, reverse=True)

    suite = RolloverSuite(
        leaf="reference", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    case = report.cases[0]
    assert case.evidence["addressedCount"] == len(references)
    assert case.evidence["weightedReferenceCoverage"] == pytest.approx(1.0)
    assert case.evidence["referenceCoverage"] == pytest.approx(1.0)
    assert case.passed
    names = {metric.name for metric in report.metrics}
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


async def test_load_rollover_builds_one_pinned_task_per_leaf(snapshot_dir: Path) -> None:
    run = load_rollover(snapshot_dir)
    names = [task.name for task in run.tasks]
    assert names == [
        "rollover.overload",
        "rollover.buried",
        "rollover.supersession",
        "rollover.chain",
        "rollover.reference",
        "rollover.image",
        "rollover.behavior",
    ]
    assert len({task.digest for task in run.tasks}) == len(run.tasks)


async def test_overload_grades_recovery_fidelity_not_a_kept_subset(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """The overload leaf used to measure which facts a summary chose to keep. The rollover chooses
    nothing: every decision fact stays recoverable, no distractor is stated as current, and the
    record-carry rate reports how much of that recovery the record states without a history read."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "overload")
    suite = RolloverSuite(
        leaf="overload", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    first = report.cases[0]
    grading = first.evidence["grading"]
    assert isinstance(grading, str)
    assert "weighted decision-fact recovery fidelity" in grading and "100%" in grading
    assert first.evidence["weightedRecall"] == pytest.approx(1.0)
    assert first.evidence["distractorRate"] == pytest.approx(0.0)
    carry = first.evidence["recordCarryRate"]
    assert isinstance(carry, float) and carry < 1.0
    assert first.passed
    assert "the history lines it names hold the rest" in first.reason
    metric = next(metric for metric in report.metrics if metric.name == "weighted_recall")
    assert metric.value == pytest.approx(1.0)
    assert {"record_carry_rate", "distractor_rate"} <= {metric.name for metric in report.metrics}


async def test_supersession_recovers_corrections_and_states_no_stale_value(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """A stale value stays readable in the journal — the history is append-only, and a correction is
    a later entry rather than a rewrite. What the leaf forbids is the recovery record asserting one
    as current, which nothing at a rollover boundary is able to author."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "supersession")
    suite = RolloverSuite(
        leaf="supersession", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    case = report.cases[0]
    assert case.evidence["correctionRecall"] == pytest.approx(1.0)
    assert case.evidence["staleRate"] == pytest.approx(0.0)
    assert case.passed
    assert report.passed


async def test_a_stale_literal_inside_the_history_path_is_not_the_record_asserting_it(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """The record names the history file by a path that carries the conversation id, and four
    digits of a superseded value can sit inside those hex characters. A path is not an
    assertion, so a journal rooted at a directory spelled with a stale literal grades exactly
    like any other."""
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "supersession")
    stale = next(fact.stale_literal for fact in case.facts if fact.kind == "superseded")
    suite = RolloverSuite(
        leaf="supersession", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(
        ArtifactOnlyTarget(_lab(tmp_path / f"run-{stale}")), asyncio.Semaphore(1)
    )  # type: ignore[arg-type]
    graded = report.cases[0]
    assert graded.evidence["staleRate"] == pytest.approx(0.0)
    assert graded.passed


async def test_supersession_fails_when_the_record_states_a_stale_value(
    snapshot_dir: Path,
) -> None:
    """The stale cap is the leaf's live half: a boundary whose record repeats a superseded value
    fails even at full correction recall."""
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "supersession")
    superseded = [fact for fact in case.facts if fact.kind == "superseded"]
    stale_stated = {fact.id for fact in superseded[:6]}
    suite = RolloverSuite(
        leaf="supersession", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    grade = GenerationGrade(
        generation=1,
        before_tokens=TEST_TARGET_TOKENS,
        after_tokens=100,
        carried=frozenset(),
        journaled=frozenset(fact.id for fact in case.facts),
        searchable=frozenset(fact.id for fact in case.facts),
        stale_carried=frozenset(stale_stated),
        addressed=frozenset(),
    )
    passed, reason = suite._verdict(case, grade)
    assert not passed
    assert "correction recall 100%" in reason
    assert f"stale rate {len(stale_stated) / len(superseded):.0%}" in reason


async def test_chain_keeps_every_generation_searchable(snapshot_dir: Path, tmp_path: Path) -> None:
    """Three boundaries over one conversation. A summary of a summary decayed with each pass; the
    history file is append-only, so a fact planted before the first boundary is still reachable with
    grep after the third."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "chain")
    suite = RolloverSuite(
        leaf="chain", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    case = report.cases[0]
    generations = case.evidence["generations"]
    assert isinstance(generations, list) and len(generations) == 3
    assert all(
        isinstance(entry, dict) and entry["criticalSurvival"] == pytest.approx(1.0)
        for entry in generations
    )
    assert case.passed
    assert "reachable with grep" in case.reason
    by_metric = {metric.name: metric.value for metric in report.metrics}
    assert {"survival_gen1", "survival_gen2", "survival_gen3"} <= set(by_metric)
    assert by_metric["survival_gen3"] == pytest.approx(1.0)


async def test_image_leaf_is_red_while_images_die_at_the_boundary(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """The journal keeps rendered text, so a fact carried only as pixels is the one thing a
    rollover still loses. The leaf stays red on purpose and names why."""
    snapshot = load_snapshot(snapshot_dir)
    cases = tuple(case for case in snapshot.cases if case.leaf == "image")
    suite = RolloverSuite(
        leaf="image", cases=cases, digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(ArtifactOnlyTarget(_lab(tmp_path)), asyncio.Semaphore(1))  # type: ignore[arg-type]
    case = report.cases[0]
    assert not case.passed
    assert "image-borne fact died" in case.reason
    assert "an image becomes a marker" in case.reason
    metric = next(metric for metric in report.metrics if metric.name == "image_survival")
    assert metric.value == 0.0


async def test_behavior_probes_grade_answers_and_reread_trajectories(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    probes = {probe.id: probe for probe in case.probes}
    lab = _lab(tmp_path)
    conversation_id = uuid4()
    await lab.rollover(conversation_id, TEST_TRIGGER_TOKENS).maybe_cross(case.messages)
    reference = probes["reference"]
    answers = {
        SEED_MESSAGE: _clean("ready"),
        probes["recall"].question: _clean(f"We set it to {probes['recall'].expect_literals[0]}."),
        probes["supersession"].question: _clean(
            f"It is {probes['supersession'].expect_literals[0]}, previously "
            f"{probes['supersession'].forbid_literals[0]}."
        ),
        probes["supersession-last"].question: _clean(
            f"It is {probes['supersession-last'].expect_literals[0]}."
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
    suite = RolloverSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    by_name = {result.name: result for result in report.cases}
    assert by_name[f"{case.id}.recall"].passed
    assert not by_name[f"{case.id}.supersession"].passed
    assert "superseded" in by_name[f"{case.id}.supersession"].reason
    assert by_name[f"{case.id}.supersession-last"].passed
    assert by_name[f"{case.id}.reference"].passed
    assert not by_name[f"{case.id}.control"].passed
    assert "rollover" in by_name[f"{case.id}.{case.probes[0].id}"].evidence
    recall_grading = by_name[f"{case.id}.recall"].evidence["grading"]
    assert isinstance(recall_grading, str)
    assert probes["recall"].expect_literals[0] in recall_grading
    reference_grading = by_name[f"{case.id}.reference"].evidence["grading"]
    assert isinstance(reference_grading, str)
    assert reference.expect_read_path in reference_grading
    metric = next(metric for metric in report.metrics if metric.name == "probe_pass_rate")
    assert metric.value == pytest.approx(0.6)
    stored = decode(await lab.blob.get(transcript_key(conversation_id)))
    assert stored.seq == 1
    assert stored.messages == case.messages
    reference_fact = next(fact for fact in case.facts if fact.kind == "reference")
    materialized = (
        lab.workspace_root / str(conversation_id) / reference_fact.path.removeprefix("/workspace/")
    )
    assert materialized.read_text() == reference_fact.body


async def test_behavior_probes_fail_when_rollover_never_fired(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    lab = _lab(tmp_path)
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
    suite = RolloverSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    assert not report.passed
    assert all("the rollover never fired" in result.reason for result in report.cases)


async def test_behavior_refuses_a_window_the_probe_model_never_rolls_over(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """`claude-opus-5` declares a 1M-token window, so the engine puts the rollover line at 950k and
    a snapshot sized for the 200k default can never reach it. Refuse before spending a turn, naming
    both numbers, instead of running every probe and blaming the model for a rollover that was
    unreachable by construction."""
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    lab = _lab(tmp_path, context_window=1_000_000)
    target = ScriptedStepTarget(lab, uuid4(), {SEED_MESSAGE: _clean("ready")})
    suite = RolloverSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    assert not report.passed
    assert len(report.cases) == len(case.probes)
    for result in report.cases:
        assert "1,000,000-token context window" in result.reason
        assert "the rollover line sits at 950,000" in result.reason
    assert target.steps == []


async def test_grade_probe_reports_unclean_turns(snapshot_dir: Path) -> None:
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    probe = case.probes[0]
    conversation_id = uuid4()
    outcome = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason="turn failed",
        trajectory=EvalTrajectory(
            conversation_id=conversation_id,
            turn_id=None,
            status=None,
            messages=(Message(role="user", content=probe.question),),
        ),
    )
    result = _grade_probe(case, probe, outcome, wall_ms=1200)
    assert not result.passed
    assert result.reason == "turn failed"
    attempt = result.evidence["attempts"][0]
    assert attempt["passed"] is False
    assert attempt["reason"] == "turn failed"
    assert attempt["wallMs"] == 1200
    assert attempt["trajectory"]["conversation_id"] == str(conversation_id)


async def test_grade_probe_carries_the_turn_trajectory(snapshot_dir: Path) -> None:
    """The probe turn's stored transcript rides in the viewer's attempt shape, so a behavior case
    surfaces a `View trajectory` link the same way a capability case does."""
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    probe = next(probe for probe in case.probes if probe.expect_read_path)
    conversation_id, turn_id = uuid4(), uuid4()
    call = ToolInvocation(
        name="bash",
        input={"command": f"cat {probe.expect_read_path}"},
        result="report",
        has_result=True,
    )
    messages = (
        Message(role="user", content=probe.question),
        Message(role="assistant", content=f"The peak was {probe.expect_literals[0]}."),
    )
    outcome = TargetResult(
        CapabilityOutput(f"The peak was {probe.expect_literals[0]}.", (call,)),
        clean=True,
        trajectory=EvalTrajectory(
            conversation_id=conversation_id, turn_id=turn_id, status=None, messages=messages
        ),
    )
    result = _grade_probe(case, probe, outcome, wall_ms=1200)
    assert result.passed
    attempts = result.evidence["attempts"]
    assert isinstance(attempts, list) and len(attempts) == 1
    attempt = attempts[0]
    assert attempt["trajectory"]["conversation_id"] == str(conversation_id)
    assert attempt["trajectory"]["turn_id"] == str(turn_id)
    assert attempt["wallMs"] == 1200
    assert len(attempt["trajectory"]["messages"]) == len(messages)
    assert attempt["calls"][0] == {
        "name": "bash",
        "input": {"command": f"cat {probe.expect_read_path}"},
        "result": "report",
        "hasResult": True,
        "isError": False,
    }
    assert attempt["grader"]["readPathTouched"] is True
    assert result.evidence["question"] == probe.question
    assert isinstance(result.evidence["grading"], str)


async def test_seed_failure_carries_the_failed_seed_trajectory(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    """A seed turn that never terminates cleanly still records the failed turn's trajectory on every
    probe result, so the viewer links straight to the transcript that explains the failure."""
    snapshot = load_snapshot(snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "behavior")
    conversation_id = uuid4()
    seed = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason="boom",
        trajectory=EvalTrajectory(
            conversation_id=conversation_id,
            turn_id=None,
            status=None,
            messages=(Message(role="user", content=SEED_MESSAGE),),
        ),
    )
    target = ScriptedStepTarget(_lab(tmp_path), conversation_id, {SEED_MESSAGE: seed})
    suite = RolloverSuite(
        leaf="behavior", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    assert not report.passed
    assert len(report.cases) == len(case.probes)
    for result in report.cases:
        assert result.reason == "seed turn failed: boom"
        attempt = result.evidence["attempts"][0]
        assert attempt["passed"] is False
        assert attempt["reason"] == "seed turn failed: boom"
        assert attempt["trajectory"]["conversation_id"] == str(conversation_id)


async def test_materialize_rejects_paths_outside_the_workspace(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    with pytest.raises(ValueError, match="outside the workspace"):
        await lab.materialize(
            uuid4(),
            (Message(role="user", content="hello"),),
            {"/etc/passwd": "nope"},
        )


async def test_snapshot_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    fact = PlantedFact(id="f", kind="decision", literal="4242")
    case = RolloverCase(
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


SCRUB_BAIT = "escalate to ops.oncall@metalcraft.ai holding token ghp_abcdefghijklmnop123"
IMAGE_PAYLOAD = "QkFTRTY0U0VDUkVU" * 40


def _skeleton(root: Path, name: str, rounds: int) -> None:
    messages: list[Message] = [
        Message(role="user", content=f"Start the export task. {SCRUB_BAIT}"),
        Message(
            role="assistant",
            content=(
                TextBlock(text="Grabbing the dashboard screenshot."),
                ToolUseBlock(
                    id=f"{name}-shot",
                    name="browser_task",
                    input={"note": f"send to {SCRUB_BAIT}"},
                ),
            ),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(
                    tool_use_id=f"{name}-shot",
                    content=(
                        TextBlock(text=f"Screenshot captured. {SCRUB_BAIT}"),
                        ImageBlock(source=ImageSource(media_type="image/png", data=IMAGE_PAYLOAD)),
                    ),
                ),
            ),
        ),
    ]
    for index in range(rounds):
        messages.append(
            Message(
                role="assistant", content=f"Working step {index} of the export task " + "x" * 120
            )
        )
        messages.append(Message(role="user", content=f"Result {index} acknowledged " + "y" * 120))
    (root / f"{name}{SKELETON_SUFFIX}").write_bytes(
        encode(Conversation(seq=1, messages=tuple(messages)))
    )


def _real_build(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("real-corpus")
    _corpus(repo)
    transcripts = tmp_path_factory.mktemp("skeletons")
    for name in sorted({name for pair in REAL_SKELETONS.values() for name in pair}):
        _skeleton(transcripts, name, rounds=72)
    out = tmp_path_factory.mktemp("real-snapshot")
    SnapshotBuild(
        repo=repo, out=out, target_tokens=TEST_TARGET_TOKENS, transcripts=transcripts
    ).build()
    return out


@pytest.fixture(scope="module")
def real_snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _real_build(tmp_path_factory)


async def test_real_cases_build_only_with_transcripts(
    snapshot_dir: Path, real_snapshot_dir: Path
) -> None:
    without = load_snapshot(snapshot_dir)
    assert not [case for case in without.cases if case.leaf == "real"]
    assert without.manifest.skeletons == ()
    snapshot = load_snapshot(real_snapshot_dir)
    real = [case for case in snapshot.cases if case.leaf == "real"]
    assert sorted(case.id for case in real) == sorted(REAL_SKELETONS)
    assert len(snapshot.manifest.skeletons) == len(
        {name for pair in REAL_SKELETONS.values() for name in pair}
    )
    for case in real:
        assert estimate_tokens(case.messages) >= TEST_TARGET_TOKENS
        assert case.probes
    tasks = load_rollover(real_snapshot_dir).tasks
    assert "rollover.real" in {task.name for task in tasks}


async def test_real_skeletons_are_scrubbed(real_snapshot_dir: Path) -> None:
    snapshot = load_snapshot(real_snapshot_dir)
    case = next(case for case in snapshot.cases if case.leaf == "real")
    text = "\n".join(message_text(message) for message in case.messages)
    assert "ops.oncall@metalcraft.ai" not in text
    assert "ghp_abcdefghijklmnop123" not in text
    assert "redacted@example.com" in text
    assert "[redacted]" in text


async def test_real_skeleton_blocks_are_scrubbed_and_images_become_markers(
    real_snapshot_dir: Path,
) -> None:
    """The fixture skeletons open with block-shaped content — a tool_use whose input carries a
    secret, a tool_result carrying text plus an inline screenshot — so the block match arms are
    what this proves: payloads reduce to markers, secrets scrub inside every block shape."""
    snapshot = load_snapshot(real_snapshot_dir)
    for case in snapshot.cases:
        if case.leaf != "real":
            continue
        blocks = [
            block
            for message in case.messages
            if not isinstance(message.content, str)
            for block in message.content
        ]
        assert not any(isinstance(block, ImageBlock) for block in blocks)
        text = "\n".join(message_text(message) for message in case.messages)
        assert IMAGE_PAYLOAD not in text
        tool_inputs = "\n".join(
            str(block.input) for block in blocks if isinstance(block, ToolUseBlock)
        )
        parts_text = "\n".join(
            part.text
            for block in blocks
            if isinstance(block, ToolResultBlock) and isinstance(block.content, tuple)
            for part in block.content
            if isinstance(part, TextBlock)
        )
        assert "ops.oncall@metalcraft.ai" not in tool_inputs
        assert "redacted@example.com" in tool_inputs
        assert "ops.oncall@metalcraft.ai" not in parts_text
        assert "[image]" in text


async def test_real_literals_are_disjoint_across_cases(real_snapshot_dir: Path) -> None:
    snapshot = load_snapshot(real_snapshot_dir)
    seen: dict[str, str] = {}
    for case in snapshot.cases:
        if case.leaf != "real":
            continue
        for fact in case.facts:
            for literal in (fact.literal, fact.stale_literal):
                if not literal:
                    continue
                assert literal not in seen, (
                    f"literal {literal} appears in both {seen[literal]} and {case.id}"
                )
                seen[literal] = case.id


async def test_real_probes_are_report_only(real_snapshot_dir: Path, tmp_path: Path) -> None:
    snapshot = load_snapshot(real_snapshot_dir)
    case = next(case for case in snapshot.cases if case.id == "real-plan-reversal")
    probes = {probe.id: probe for probe in case.probes}
    lab = _lab(tmp_path)
    conversation_id = uuid4()
    await lab.rollover(conversation_id, TEST_TRIGGER_TOKENS).maybe_cross(case.messages)
    answers = {
        SEED_MESSAGE: _clean("ready"),
        probes["recall"].question: _clean(f"It is {probes['recall'].expect_literals[0]}."),
        probes["reversal"].question: _clean(f"It is {probes['reversal'].forbid_literals[0]}."),
        probes["revision"].question: _clean(f"It is {probes['revision'].expect_literals[0]}."),
        probes["deleted"].question: _clean("No — that step was dropped from the plan."),
        probes["control"].question: _clean("I do not have that value."),
    }
    target = ScriptedStepTarget(lab, conversation_id, answers)
    suite = RolloverSuite(
        leaf="real", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    assert report.passed
    probes_results = [result for result in report.cases if not result.name.endswith(".rolled")]
    sanity = next(result for result in report.cases if result.name.endswith(".rolled"))
    assert sanity.passed and not sanity.excluded
    assert all(result.excluded for result in probes_results)
    assert all("excluded from the pass bar" in result.reason for result in probes_results)
    first = report.cases[0]
    assert "rollover" in first.evidence
    assert "artifactGrading" in first.evidence
    assert first.evidence["weightedRecall"] == pytest.approx(1.0)
    assert first.evidence["staleRate"] == pytest.approx(0.0)
    assert first.evidence["correctionRecall"] == pytest.approx(1.0)
    by_metric = {metric.name: metric.value for metric in report.metrics}
    assert by_metric["probe_pass_rate"] == pytest.approx(0.6)
    assert by_metric["stale_rate"] == pytest.approx(0.0)


async def test_real_sanity_failure_is_not_report_only(
    real_snapshot_dir: Path, tmp_path: Path
) -> None:
    snapshot = load_snapshot(real_snapshot_dir)
    case = next(case for case in snapshot.cases if case.id == "real-plan-reversal")
    lab = _lab(tmp_path)
    answers = {SEED_MESSAGE: _clean("ready")}
    for probe in case.probes:
        answers[probe.question] = _clean(" ".join(probe.expect_literals) or "dropped")
    target = ScriptedStepTarget(lab, uuid4(), answers)
    suite = RolloverSuite(
        leaf="real", cases=(case,), digest="test", trigger_tokens=TEST_TRIGGER_TOKENS
    )
    report = await suite.run(target, asyncio.Semaphore(1))  # type: ignore[arg-type]
    assert not report.passed
    assert all(not result.excluded for result in report.cases)
    assert all("the rollover never fired" in result.reason for result in report.cases)


async def test_real_builds_are_deterministic(tmp_path_factory: pytest.TempPathFactory) -> None:
    first = load_snapshot(_real_build(tmp_path_factory)).manifest.digest
    second = load_snapshot(_real_build(tmp_path_factory)).manifest.digest
    assert first == second
