"""The pinned coding suite's gates. The load-bearing proof is that every case's own merged diff —
the answer this repository shipped — passes the gate at that case's base commit: a gate that rejects
the real answer would score every candidate against nothing."""

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import pytest
from coding_repo_history import pinned_history, reference_diff  # noqa: F401

from evals.__main__ import main as evals_main
from evals.coding_repo.cases import (
    CASES,
    DOCUMENT_SUFFIX,
    MAX_CRITERIA_PER_CASE,
    PATCH_CASES,
    RESEARCH_CASES,
    CodingCase,
)
from evals.coding_repo.runner import (
    CODING_LANE,
    DELIVERABLES_TASK,
    PARENT_FORBIDDEN_TOOLS,
    REFUSED_DIR,
    REPO_ROOT,
    REPO_SLUG,
    REPO_URL,
    DocumentCapture,
    PatchCapture,
    PatchRuns,
    _capability_case,
    _materialize_tree,
    _test_paths,
    _touched_paths,
    load_coding_repo,
)
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    SharedArtifact,
    ToolInvocation,
)
from evals.harness.coding import AllOf, PinnedRepositoryRoute
from evals.harness.registry import EvalTask
from evals.harness.scorers import delegation_only_scorer
from evals.harness.target import TargetResult
from ufo.config import BlobConfig, Config, DatabaseConfig, PackConfig
from ufo.host.tools.builtins import _spawn_handles

SURVEY_CASE = next(case for case in CASES if case.deliverable == "document")
CAPTURED_DOCUMENT = b"- ufo/<deploy>/api-keys\n"
OTHER_SLUG = "astral-sh/ruff"
CLONE_SEPARATORS = ("&&", "||", ";", "\n")
SCOPED_ROUTES = (
    "gh api repos/{slug}/zipball/HEAD",
    "gh api repos/{slug}/tarball/HEAD",
    "curl -L https://github.com/{slug}/archive/HEAD.tar.gz",
    "gh api repos/{slug}/contents/README.md",
    "curl https://codeload.github.com/{slug}/tar.gz/HEAD",
    "curl https://raw.githubusercontent.com/{slug}/HEAD/README.md",
)


class RunStarted(Exception):
    pass


@dataclass
class SurvivingTurn:
    """A target whose turn shares the document `SURVEY_CASE` asked for, so the run leaves a real
    capture on disk. Any other case's turn survives sharing nothing."""

    judge: None = None
    simulator: None = None

    async def run(self, case: CapabilityCase) -> TargetResult:
        if case.name != SURVEY_CASE.name:
            return TargetResult(output(), clean=True)
        name = Path(SURVEY_CASE.document_path).name
        return TargetResult(
            output(
                artifacts=(SharedArtifact(name=name, content=CAPTURED_DOCUMENT),),
                shared_names=(name,),
                commands=(f"git fetch --depth 1 origin {SURVEY_CASE.base_sha}",),
            ),
            clean=True,
        )


def seeded(submissions: Path, case_dir: str, name: str) -> Path:
    target = submissions / case_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("a previous run's bytes")
    return target


def output(
    *,
    lane: str = CODING_LANE,
    delegated: bool = True,
    spawn_result: str = '{"result": "done"}',
    commands: tuple[str, ...] = (),
    artifacts: tuple[SharedArtifact, ...] = (),
    shared_names: tuple[str, ...] = (),
    own_tools: tuple[str, ...] = (),
) -> CapabilityOutput:
    calls: list[ToolInvocation] = []
    if delegated:
        calls.append(
            ToolInvocation(
                name="spawn",
                input={"target": lane, "payload": {"objective": "work"}},
                result=spawn_result,
                has_result=True,
            )
        )
    calls.extend(
        ToolInvocation(name="bash", input={"command": command}, result="", has_result=True)
        for command in commands
    )
    calls.extend(
        ToolInvocation(
            name="share_file",
            input={"files": [{"file_path": f"/workspace/{name}"}]},
            result=json.dumps([{"name": name}]),
            has_result=True,
        )
        for name in shared_names
    )
    return CapabilityOutput(
        response="done", calls=tuple(calls), artifacts=artifacts, own_tools=own_tools
    )


def stubbed_test_run(
    monkeypatch: pytest.MonkeyPatch, outcome: bool | None, report: str = "stubbed"
) -> None:
    async def ran(_self: PatchRuns, _tree: Path) -> tuple[bool | None, str]:
        return outcome, report

    monkeypatch.setattr(PatchRuns, "_run_tests", ran)


@pytest.mark.parametrize("case", PATCH_CASES, ids=lambda case: case.name)
async def test_the_shipped_diff_passes_the_gate_at_its_base_commit(
    case: CodingCase, tmp_path: Path
) -> None:
    _materialize_tree(case.base_sha, tmp_path / "trees")
    patch = reference_diff(case.reference_sha)
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(
            artifacts=(SharedArtifact(name=name, content=patch),),
            shared_names=(name,),
        )
    )
    assert verdict.passed, verdict.reason
    assert verdict.evidence["appliesClean"] is True
    assert verdict.evidence["expectedPathsTouched"]
    assert verdict.evidence["sizeBytes"] == len(patch)
    assert (tmp_path / "submissions" / case.name / name).read_bytes() == patch


async def test_an_off_target_patch_is_refused_though_it_applies(tmp_path: Path) -> None:
    """A patch for an unrelated case touches no file the reference change touched, so it clears the
    apply check on disjoint files and the paths gate is what refuses it. Both gates carry weight."""
    case, other = PATCH_CASES[0], PATCH_CASES[2]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(
            artifacts=(SharedArtifact(name=name, content=reference_diff(other.reference_sha)),),
            shared_names=(name,),
        )
    )
    assert not verdict.passed
    assert verdict.evidence["appliesClean"] is True
    assert "none of the paths" in verdict.reason


async def test_a_patch_whose_context_is_not_in_the_pinned_tree_is_refused(tmp_path: Path) -> None:
    """The apply check reads the pinned bytes: a diff naming a file the reference change touched,
    against context that file does not contain, is refused on the context and not on the path."""
    case = PATCH_CASES[1]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    target = case.expected_paths[0]
    patch = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1,2 +1,3 @@\n"
        " a line this file has never contained\n"
        " nor this one\n"
        "+an added line\n"
    ).encode()
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(artifacts=(SharedArtifact(name=name, content=patch),), shared_names=(name,))
    )
    assert not verdict.passed
    assert verdict.evidence["touchedPaths"] == [target]
    assert "does not apply" in verdict.reason


async def test_a_patch_touching_no_expected_path_fails(tmp_path: Path) -> None:
    """A new-file diff applies to any tree, so nothing but the paths gate stands between an
    irrelevant change and a pass."""
    case = PATCH_CASES[0]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    patch = (
        b"diff --git a/NOTES.md b/NOTES.md\n"
        b"new file mode 100644\n"
        b"--- /dev/null\n"
        b"+++ b/NOTES.md\n"
        b"@@ -0,0 +1 @@\n"
        b"+a line\n"
    )
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(artifacts=(SharedArtifact(name=name, content=patch),), shared_names=(name,))
    )
    assert not verdict.passed
    assert verdict.evidence["appliesClean"] is True
    assert "none of the paths" in verdict.reason
    assert verdict.evidence["touchedPaths"] == ["NOTES.md"]


async def test_an_unshared_patch_is_refused(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(output(artifacts=(SharedArtifact(name=name, content=b"diff"),)))
    assert not verdict.passed
    assert "did not share" in verdict.reason


async def test_the_lane_gate_requires_a_coding_child_at_the_pin() -> None:
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    passed = await grader(output(commands=(f"git fetch --depth 1 origin {case.base_sha}",)))
    assert passed.passed, passed.reason
    assert (await grader(output(delegated=False))).reason == "did not delegate"
    wrong_lane = await grader(output(lane="research", commands=(case.base_sha,)))
    assert not wrong_lane.passed
    assert CODING_LANE in wrong_lane.reason
    unpinned = await grader(output(commands=("ls /workspace",)))
    assert not unpinned.passed
    assert "no call fetches" in unpinned.reason


@pytest.mark.parametrize("moved", [False, True], ids=["detached", "moved"])
async def test_the_lane_gate_reads_a_background_spawn_as_no_result(moved: bool) -> None:
    """The acknowledgement the spawn tool itself writes, for a child asked into the background and
    for one an arriving message moved there: both name a running child and carry no output, so
    neither counts as a delegation that returned a result."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    verdict = await grader(
        output(
            spawn_result=_spawn_handles(CODING_LANE, uuid4(), moved),
            commands=(f"git fetch --depth 1 origin {case.base_sha}",),
        )
    )
    assert not verdict.passed
    assert "no coding delegation returned a result" in verdict.reason


async def test_the_lane_gate_refuses_a_historyless_route() -> None:
    """One case per argument a call reaches a target through, and per route that hands back a tree
    with no history — a shell command, a REPL, a URL fetch, and a connector action."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    routes = (
        ("bash", "command", f"gh api repos/{REPO_SLUG}/zipball/{case.base_sha}"),
        ("bash", "command", f"gh api repos/{REPO_SLUG}/contents/core/src/ufo/db.py"),
        ("bash", "command", f"curl https://codeload.github.com/{REPO_SLUG}/tar.gz/{case.base_sha}"),
        (
            "fetch_url",
            "url",
            f"https://raw.githubusercontent.com/{REPO_SLUG}/{case.base_sha}/README.md",
        ),
        ("js_repl", "code", f"await fetch('https://github.com/{REPO_SLUG}/tarball/HEAD')"),
        ("call_external_tool", "tool_name", "GITHUB_DOWNLOAD_A_REPOSITORY_ARCHIVE_ZIP"),
        ("call_external_tool", "tool_name", "GITHUB_GET_RAW_REPOSITORY_CONTENT"),
    )
    for tool, argument, value in routes:
        route = ToolInvocation(name=tool, input={argument: value}, result="", has_result=True)
        verdict = await grader(CapabilityOutput(response="done", calls=(*output().calls, route)))
        assert not verdict.passed, value
        assert "historyless route" in verdict.reason, value


async def test_the_document_gate_wants_the_file_the_brief_named(tmp_path: Path) -> None:
    grader = DocumentCapture(SURVEY_CASE, tmp_path / "submissions")
    name = Path(SURVEY_CASE.document_path).name
    verdict = await grader(
        output(
            artifacts=(SharedArtifact(name=name, content=b"- a finding\n"),),
            shared_names=(name,),
        )
    )
    assert verdict.passed, verdict.reason
    assert verdict.evidence["words"] == 3
    empty = await grader(
        output(artifacts=(SharedArtifact(name=name, content=b"   "),), shared_names=(name,))
    )
    assert not empty.passed
    assert "is empty" in empty.reason
    misnamed = await grader(
        output(
            artifacts=(SharedArtifact(name="notes.md", content=b"body"),),
            shared_names=("notes.md",),
        )
    )
    assert not misnamed.passed
    assert name in misnamed.reason


def test_touched_paths_reads_every_diff_header() -> None:
    patch = (
        "diff --git a/core/src/ufo/db.py b/core/src/ufo/db.py\n"
        "@@ -1 +1 @@\n"
        "diff --git a/core/tests/test_db.py b/core/tests/test_db.py\n"
    )
    assert _touched_paths(patch) == ("core/src/ufo/db.py", "core/tests/test_db.py")
    assert _touched_paths("no diff here") == ()


def test_held_out_targets_name_each_test_file_once() -> None:
    case = next(case for case in PATCH_CASES if len(case.held_out_tests) > 1)
    assert _test_paths(case.held_out_tests) == tuple(
        dict.fromkeys(target.partition("::")[0] for target in case.held_out_tests)
    )


def test_the_envelope_pins_the_commit_and_names_the_deliverable(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    capability = _capability_case(case, tmp_path / "submissions", tmp_path / "trees")
    assert capability.message.startswith(case.brief)
    assert case.base_sha in capability.message
    assert f"{case.name}.patch" in capability.message
    assert "not a clone of the default branch" in capability.message
    assert capability.rubric == ()


def test_the_envelope_states_the_setup_without_commands_to_run(tmp_path: Path) -> None:
    """The setup is addressed to whoever does the work, never spelled as commands. A first live case
    had the evaluated turn run the envelope's `git init` itself before delegating — into its own
    workspace, which no child can see."""
    for case in (PATCH_CASES[0], RESEARCH_CASES[0]):
        envelope = _capability_case(case, tmp_path / "submissions", tmp_path / "trees").message
        envelope = envelope[envelope.index("Evaluation setup") :]
        assert "to pass on to whoever does the work" in envelope
        for command in ("git init", "git remote add", "git fetch", "git checkout", "cd repo"):
            assert command not in envelope, f"{case.name} envelope spells out {command!r}"
    reply = _capability_case(RESEARCH_CASES[0], tmp_path / "submissions", tmp_path / "trees")
    assert reply.rubric == RESEARCH_CASES[0].criteria
    assert "Change no files." in reply.message


def test_a_case_states_what_its_kind_requires() -> None:
    for extra in (
        {"reference_sha": "1" * 40},
        {"expected_paths": ("core/src/ufo/db.py",)},
        {"held_out_tests": ("core/tests/test_db.py::test_db",)},
    ):
        with pytest.raises(ValueError, match="a reference commit, paths, or held-out tests"):
            CodingCase(
                name="bad",
                kind="research",
                base_sha="0" * 40,
                brief="b",
                criteria=("c",),
                **extra,
            )
    with pytest.raises(ValueError, match="full 40-character commit sha"):
        CodingCase(name="bad", kind="research", base_sha="abc", brief="b", criteria=("c",))
    with pytest.raises(ValueError, match="states its criteria"):
        CodingCase(name="bad", kind="research", base_sha="0" * 40, brief="b", criteria=())
    with pytest.raises(ValueError, match="delivers a"):
        CodingCase(
            name="bad",
            kind="fix",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
        )
    with pytest.raises(ValueError, match="names the merged commit that answered it"):
        CodingCase(
            name="bad",
            kind="fix",
            deliverable="patch",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            expected_paths=("core/src/ufo/db.py",),
        )
    with pytest.raises(ValueError, match="names the paths its answer changes"):
        CodingCase(
            name="bad",
            kind="fix",
            deliverable="patch",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            reference_sha="1" * 40,
        )
    with pytest.raises(ValueError, match="names its held-out tests"):
        CodingCase(
            name="bad",
            kind="fix",
            deliverable="patch",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            reference_sha="1" * 40,
            expected_paths=("core/src/ufo/db.py",),
        )
    with pytest.raises(ValueError, match="invalid held-out test target"):
        CodingCase(
            name="bad",
            kind="fix",
            deliverable="patch",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            reference_sha="1" * 40,
            expected_paths=("core/src/ufo/db.py",),
            held_out_tests=("../test_db.py::test_db",),
        )
    with pytest.raises(ValueError, match=f"exceeds the judge's {MAX_CRITERIA_PER_CASE}"):
        CodingCase(
            name="bad",
            kind="research",
            base_sha="0" * 40,
            brief="b",
            criteria=tuple(str(n) for n in range(MAX_CRITERIA_PER_CASE + 1)),
        )
    with pytest.raises(ValueError, match="names the path it writes"):
        CodingCase(
            name="bad",
            kind="research",
            deliverable="document",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
        )
    with pytest.raises(ValueError, match="names the path it writes"):
        CodingCase(
            name="bad",
            kind="research",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            document_path=f"/workspace/notes{DOCUMENT_SUFFIX}",
        )
    with pytest.raises(ValueError, match=f"a document deliverable is {DOCUMENT_SUFFIX}"):
        CodingCase(
            name="bad",
            kind="research",
            deliverable="document",
            base_sha="0" * 40,
            brief="b",
            criteria=("c",),
            document_path="/workspace/notes.txt",
        )


def test_load_splits_the_suite_by_where_a_case_can_be_judged(tmp_path: Path) -> None:
    tasks = load_coding_repo(
        case_names=(RESEARCH_CASES[0].name, PATCH_CASES[0].name),
        submissions_root=tmp_path / "submissions",
        trees_root=tmp_path / "trees",
    )
    assert [task.name for task in tasks] == ["coding_repo_answers", "coding_repo_deliverables"]
    answers, deliverables = tasks
    assert answers.cases == (RESEARCH_CASES[0].name,)
    assert answers.judge_model is not None
    assert deliverables.cases == (PATCH_CASES[0].name,)
    assert deliverables.judge_model is None
    assert all(task.pin_runtime for task in tasks)
    with pytest.raises(ValueError, match="unknown coding_repo case"):
        load_coding_repo(case_names=("nope",))


def test_a_case_essential_must_index_its_own_criteria() -> None:
    with pytest.raises(ValueError, match="essential must index its own criteria"):
        CodingCase(
            name="bad", kind="research", base_sha="0" * 40, brief="b", criteria=("c",), essential=3
        )


def test_a_patch_case_asks_for_the_note_beside_the_diff(tmp_path: Path) -> None:
    """A diff cannot state what was verified or what remains uncertain, so a patch case hands over
    both and the judge reads them together."""
    case = PATCH_CASES[0]
    envelope = _capability_case(case, tmp_path / "submissions", tmp_path / "trees").message
    assert f"{case.name}.patch" in envelope
    assert case.notes_name in envelope
    assert "what remains uncertain" in envelope


async def test_the_note_is_captured_beside_the_patch(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    patch_name, notes_name = f"{case.name}.patch", case.notes_name
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(
            artifacts=(
                SharedArtifact(name=patch_name, content=reference_diff(case.reference_sha)),
                SharedArtifact(name=notes_name, content=b"Verified by rerunning the slack tests."),
            ),
            shared_names=(patch_name, notes_name),
        )
    )
    assert verdict.passed, verdict.reason
    assert verdict.evidence["notes"] == 38
    assert (tmp_path / "submissions" / case.name / notes_name).is_file()


async def test_a_patch_without_its_note_still_passes_the_gate(tmp_path: Path) -> None:
    """The note is scored, not gated: a missing note costs the criteria that needed it rather than
    suppressing judgment of the diff that did arrive."""
    case = PATCH_CASES[0]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    name = f"{case.name}.patch"
    grader = PatchCapture(case, tmp_path / "submissions", tmp_path / "trees")
    verdict = await grader(
        output(
            artifacts=(SharedArtifact(name=name, content=reference_diff(case.reference_sha)),),
            shared_names=(name,),
        )
    )
    assert verdict.passed, verdict.reason
    assert verdict.evidence["notes"] is None


async def test_a_materialized_tree_is_its_own_work_tree(tmp_path: Path) -> None:
    """A trees root inside a repository lets `git apply` discover the enclosing work tree and skip
    the patch with exit 0, which reads as applies-clean. Each tree owns its git dir so it cannot."""
    case = PATCH_CASES[0]
    inside = REPO_ROOT / ".local" / "trees-under-repo"
    try:
        tree = _materialize_tree(case.base_sha, inside)
        assert (tree / ".git").is_dir()
        grader = PatchCapture(case, tmp_path / "submissions", inside)
        target = case.expected_paths[0]
        patch = (
            f"diff --git a/{target} b/{target}\n"
            f"--- a/{target}\n+++ b/{target}\n@@ -1,2 +1,3 @@\n"
            " a line this file has never contained\n nor this one\n+added\n"
        ).encode()
        name = f"{case.name}.patch"
        verdict = await grader(
            output(artifacts=(SharedArtifact(name=name, content=patch),), shared_names=(name,))
        )
        assert not verdict.passed
        assert verdict.evidence["appliesClean"] is False
    finally:
        shutil.rmtree(inside, ignore_errors=True)


async def test_the_route_gate_refuses_an_archive_url_and_a_clone() -> None:
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    archive = await grader(
        output(
            commands=(
                f"curl -L https://github.com/metalcraftai/ufo/archive/{case.base_sha}.tar.gz",
            )
        )
    )
    assert not archive.passed
    assert "historyless route" in archive.reason
    cloned = await grader(
        output(commands=(f"git clone {REPO_URL} && git checkout {case.base_sha}",))
    )
    assert not cloned.passed
    assert "cloned the repository" in cloned.reason
    mentioned = await grader(output(commands=(f"echo {case.base_sha}",)))
    assert not mentioned.passed
    assert "no call fetches" in mentioned.reason


async def test_all_of_merges_evidence_and_joins_its_reasons() -> None:
    case = RESEARCH_CASES[0]
    grader = AllOf(
        (
            PinnedRepositoryRoute(REPO_SLUG, case.base_sha),
            delegation_only_scorer(PARENT_FORBIDDEN_TOOLS),
        )
    )
    verdict = await grader(output(commands=(f"git fetch --depth 1 origin {case.base_sha}",)))
    assert verdict.passed, verdict.reason
    assert "delegated to 'coding'" in verdict.reason
    assert "delegated without working" in verdict.reason
    assert verdict.evidence["codingSpawns"] == 1
    assert verdict.evidence["ownTools"] == []
    assert "delegates to the 'coding' subagent" in grader.grading
    assert "reaches no checkout with" in grader.grading


def test_the_cli_refuses_a_coding_repo_case_without_the_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        evals_main(["--coding-repo-case", RESEARCH_CASES[0].name])
    assert "--coding-repo-case requires --coding-repo" in capsys.readouterr().err


async def test_the_route_gate_reads_only_calls_that_act(tmp_path: Path) -> None:
    """A spawn payload relays the envelope, whose own words name the pinned fetch. Reading it would
    pass a run that fetched nothing."""
    case = PATCH_CASES[0]
    envelope = _capability_case(case, tmp_path / "s", tmp_path / "t").message
    relayed = CapabilityOutput(
        response="done",
        calls=(
            ToolInvocation(
                name="spawn",
                input={"target": CODING_LANE, "payload": {"objective": envelope}},
                result='{"result": "done"}',
                has_result=True,
            ),
        ),
    )
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    relayed_only = await grader(relayed)
    assert not relayed_only.passed
    assert "no call fetches" in relayed_only.reason, "the envelope must not satisfy the fetch"

    with_fetch = CapabilityOutput(
        response="done",
        calls=(
            *relayed.calls,
            ToolInvocation(
                name="bash",
                input={"command": f"git fetch --depth 1 origin {case.base_sha}"},
                result="",
                has_result=True,
            ),
        ),
    )
    assert (await grader(with_fetch)).passed, "a relayed envelope beside a real fetch must pass"


def test_the_cli_refuses_coding_repo_under_the_wrong_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The suite needs the pack that carries the coding subagent; another pack is a startup error,
    not a run that delegates to a profile no manifest registers. Neither selected case delivers a
    patch, so the guard is reached without materializing a pinned tree — and the refusal leaves an
    earlier run's capture alone, because no turn of this run will replace it."""
    submissions = tmp_path / "submissions"
    stale = submissions / SURVEY_CASE.name / Path(SURVEY_CASE.document_path).name
    stale.parent.mkdir(parents=True)
    stale.write_text("a previous run's bytes")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        pack=PackConfig(name="gdpval_full"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    with pytest.raises(SystemExit):
        evals_main(
            [
                "--coding-repo",
                "--coding-repo-case",
                RESEARCH_CASES[0].name,
                "--coding-repo-case",
                SURVEY_CASE.name,
                "--coding-repo-submissions",
                str(submissions),
                "--out",
                str(tmp_path),
            ]
        )
    assert "coding_repo requires [pack] name in" in capsys.readouterr().err
    assert stale.is_file()


async def test_the_route_gate_reads_only_the_arguments_a_call_reaches_a_target_through() -> None:
    """A note, a search for the phrase, and a path that merely spells `archive/` are prose or
    working files, not routes. Reading them refuses runs that took no route at all."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    fetch = ToolInvocation(
        name="bash",
        input={"command": f"git fetch --depth 1 origin {case.base_sha}"},
        result="",
        has_result=True,
    )
    note = ToolInvocation(
        name="write",
        input={
            "file_path": "/workspace/notes.md",
            "content": f"Reproduced after git clone {REPO_URL}; see docs/rfcs/archive/0055.md.",
        },
        result="written",
        has_result=True,
    )
    search = ToolInvocation(
        name="bash",
        input={"command": "rg -n 'git clone' docs/rfcs/archive/"},
        result="",
        has_result=True,
    )
    verdict = await grader(
        CapabilityOutput(
            response="done",
            calls=(*output().calls, fetch, note, search),
        )
    )
    assert verdict.passed, verdict.reason


async def test_a_local_clone_passes_and_a_remote_clone_of_this_repository_does_not() -> None:
    """The coding skill gives every overlapping child its own checkout by copying the pinned tree
    with git. That copy carries no commit the pin does not; a clone of the remote carries all of
    them."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    local = await grader(
        output(
            commands=(
                f"git fetch --depth 1 origin {case.base_sha}",
                f"git clone /workspace/org-repo /workspace/org-repo-fix && "
                f"git -C /workspace/org-repo-fix remote set-url origin {REPO_URL}",
            )
        )
    )
    assert local.passed, local.reason
    for command in (
        f"git clone {REPO_URL}",
        "git clone git@github.com:metalcraftai/ufo.git",
        f"gh repo clone {REPO_SLUG}",
    ):
        remote = await grader(
            output(commands=(f"git fetch --depth 1 origin {case.base_sha}", command))
        )
        assert not remote.passed, command
        assert "cloned the repository" in remote.reason
    unrelated = await grader(
        output(
            commands=(
                f"git fetch --depth 1 origin {case.base_sha}",
                "git clone https://github.com/astral-sh/ruff /workspace/ruff",
            )
        )
    )
    assert unrelated.passed, unrelated.reason


async def test_each_url_route_is_refused_only_for_this_repository() -> None:
    """Every URL route the gate refuses is scoped to this repository's slug. Somebody else's archive
    or raw read is ordinary work — a dependency's source, a snippet from a public repository — so an
    unscoped substring would refuse a run that never reached the pinned commit by that route."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    fetch = f"git fetch --depth 1 origin {case.base_sha}"
    for route in SCOPED_ROUTES:
        ours = await grader(output(commands=(fetch, route.format(slug=REPO_SLUG))))
        assert not ours.passed, route
        assert "historyless route" in ours.reason, route
        theirs = await grader(output(commands=(fetch, route.format(slug=OTHER_SLUG))))
        assert theirs.passed, theirs.reason


async def test_a_clone_is_read_in_whichever_shell_segment_names_it() -> None:
    """A clone carries every commit after the pin whatever ran beside it, so each separator the gate
    splits on has to split. The same split is what lets a local clone pass while its next segment
    names the remote, so dropping a separator reds that half."""
    case = PATCH_CASES[0]
    grader = PinnedRepositoryRoute(REPO_SLUG, case.base_sha)
    fetch = f"git fetch --depth 1 origin {case.base_sha}"
    for separator in CLONE_SEPARATORS:
        remote = await grader(
            output(commands=(fetch, f"cd /workspace {separator} git clone {REPO_URL}"))
        )
        assert not remote.passed, separator
        assert "cloned the repository" in remote.reason, separator
        local = await grader(
            output(
                commands=(
                    fetch,
                    f"git clone /workspace/org-repo /workspace/copy {separator} "
                    f"git -C /workspace/copy remote set-url origin {REPO_URL}",
                )
            )
        )
        assert local.passed, f"{separator}: {local.reason}"


async def test_a_fetch_whose_call_errored_does_not_name_the_pin() -> None:
    case = PATCH_CASES[0]
    errored = ToolInvocation(
        name="bash",
        input={"command": f"git fetch --depth 1 origin {case.base_sha}"},
        result="fatal: could not read Username",
        has_result=True,
        is_error=True,
    )
    verdict = await PinnedRepositoryRoute(REPO_SLUG, case.base_sha)(
        CapabilityOutput(response="done", calls=(*output().calls, errored))
    )
    assert not verdict.passed
    assert "no call fetches" in verdict.reason


async def test_git_head_proof_recovers_an_errored_fetch() -> None:
    case = PATCH_CASES[0]
    errored = ToolInvocation(
        name="bash",
        input={"command": f"git fetch --depth 1 origin {case.base_sha}"},
        result="connection reset",
        has_result=True,
        is_error=True,
    )
    inspected = ToolInvocation(
        name="bash",
        input={"command": "cd /workspace/ufo && git log --oneline -1 2>&1 | head"},
        result=f"{case.base_sha[:7]} pinned commit\n",
        has_result=True,
    )
    verdict = await PinnedRepositoryRoute(REPO_SLUG, case.base_sha)(
        CapabilityOutput(response="done", calls=(*output().calls, errored, inspected))
    )
    assert verdict.passed, verdict.reason


async def test_prose_before_a_git_command_does_not_prove_head() -> None:
    case = PATCH_CASES[0]
    claimed = ToolInvocation(
        name="bash",
        input={"command": f"echo {case.base_sha}; git status"},
        result=f"{case.base_sha}\nOn branch main\n",
        has_result=True,
    )
    verdict = await PinnedRepositoryRoute(REPO_SLUG, case.base_sha)(
        CapabilityOutput(response="done", calls=(*output().calls, claimed))
    )
    assert not verdict.passed
    assert "no call fetches" in verdict.reason


async def test_an_explicit_non_head_revision_does_not_prove_head() -> None:
    case = PATCH_CASES[0]
    inspected = ToolInvocation(
        name="bash",
        input={"command": "git log --oneline -1 other-branch"},
        result=f"{case.base_sha[:7]} pinned commit\n",
        has_result=True,
    )
    verdict = await PinnedRepositoryRoute(REPO_SLUG, case.base_sha)(
        CapabilityOutput(response="done", calls=(*output().calls, inspected))
    )
    assert not verdict.passed
    assert "no call fetches" in verdict.reason


async def test_a_refused_patch_is_set_aside_out_of_the_offline_judges_reach(tmp_path: Path) -> None:
    """A patch that fails the gate stays on disk to read, and not where grading looks: a deliverable
    earns a judge only once the run proves it real, so the note beside it is set aside too rather
    than becoming the whole submission."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    patch_name, notes_name = f"{case.name}.patch", case.notes_name
    grader = PatchCapture(case, submissions, tmp_path / "trees")
    verdict = await grader(
        output(
            artifacts=(
                SharedArtifact(name=patch_name, content=b"prose, not a diff\n"),
                SharedArtifact(name=notes_name, content=b"What was verified.\n"),
            ),
            shared_names=(patch_name, notes_name),
        )
    )
    assert not verdict.passed
    assert "carries no unified diff" in verdict.reason
    refused = submissions / case.name / REFUSED_DIR
    assert verdict.evidence["refused"] == str(refused)
    assert (refused / patch_name).read_bytes() == b"prose, not a diff\n"
    assert (refused / notes_name).is_file()
    assert not (submissions / case.name / patch_name).exists()
    assert not (submissions / case.name / notes_name).exists()


async def test_the_submissions_root_reaches_both_capture_graders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root the suite is loaded with is the root the graders write under — for a patch with its
    note, and for a document. The reference diff's own tests are the run gate's subject and have
    their own tests, so the nested run is stood in for here."""
    case = PATCH_CASES[0]
    submissions, trees = tmp_path / "submissions", tmp_path / "trees"
    _materialize_tree(case.base_sha, trees)
    _materialize_tree(case.reference_sha, trees)
    stubbed_test_run(monkeypatch, True, "1 passed")
    patch_name, notes_name = f"{case.name}.patch", case.notes_name
    patch = reference_diff(case.reference_sha)
    verdict = await _capability_case(case, submissions, trees).grader(
        output(
            artifacts=(
                SharedArtifact(name=patch_name, content=patch),
                SharedArtifact(name=notes_name, content=b"Verified by rerunning the tests."),
            ),
            shared_names=(patch_name, notes_name),
            commands=(f"git fetch --depth 1 origin {case.base_sha}",),
        )
    )
    assert verdict.passed, verdict.reason
    assert (submissions / case.name / patch_name).read_bytes() == patch
    assert (submissions / case.name / notes_name).is_file()
    assert verdict.evidence["submission"] == str(submissions / case.name / patch_name)

    survey_name = Path(SURVEY_CASE.document_path).name
    body = b"- ufo/<deploy>/api-keys\n"
    survey = await _capability_case(SURVEY_CASE, submissions, trees).grader(
        output(
            artifacts=(SharedArtifact(name=survey_name, content=body),),
            shared_names=(survey_name,),
            commands=(f"git fetch --depth 1 origin {SURVEY_CASE.base_sha}",),
        )
    )
    assert survey.passed, survey.reason
    saved = submissions / SURVEY_CASE.name / survey_name
    assert saved.read_bytes() == body
    assert survey.evidence["submission"] == str(saved)


async def test_a_first_run_captures_and_keeps_what_its_turn_shared(
    tmp_path: Path,
) -> None:
    """The drop belongs before the turns and only where a capture exists."""
    submissions = tmp_path / "submissions"
    (task,) = load_coding_repo(
        case_names=(SURVEY_CASE.name,),
        submissions_root=submissions,
        trees_root=tmp_path / "trees",
    )
    report = await task.run(SurvivingTurn(), asyncio.Semaphore(1))
    assert report.cases[0].passed, report.cases[0].reason
    saved = submissions / SURVEY_CASE.name / Path(SURVEY_CASE.document_path).name
    assert saved.read_bytes() == CAPTURED_DOCUMENT


async def test_the_run_drops_every_selected_case_whole_and_no_other(tmp_path: Path) -> None:
    """Offline grading reads whatever sits under the submissions root, so the drop reaches each
    selected case's `refused/` bytes as well as its deliverables, and a case this run did not select
    keeps its patch, its notes and its refused bytes."""
    submissions = tmp_path / "submissions"
    selected_patch, unselected = PATCH_CASES[0], PATCH_CASES[1]
    survey_leftover = seeded(submissions, SURVEY_CASE.name, "an-earlier-run.md")
    patch_leftover = seeded(submissions, selected_patch.name, f"{selected_patch.name}.patch")
    patch_refused = seeded(submissions, f"{selected_patch.name}/{REFUSED_DIR}", "old.patch")
    kept = (
        seeded(submissions, unselected.name, f"{unselected.name}.patch"),
        seeded(submissions, unselected.name, unselected.notes_name),
        seeded(submissions, f"{unselected.name}/{REFUSED_DIR}", "old.patch"),
    )
    (task,) = load_coding_repo(
        case_names=(SURVEY_CASE.name, selected_patch.name),
        submissions_root=submissions,
        trees_root=tmp_path / "trees",
    )
    await task.run(SurvivingTurn(), asyncio.Semaphore(1))
    assert not survey_leftover.exists()
    assert not patch_leftover.exists()
    assert not patch_refused.exists()
    saved = submissions / SURVEY_CASE.name / Path(SURVEY_CASE.document_path).name
    assert saved.read_bytes() == CAPTURED_DOCUMENT
    for path in kept:
        assert path.is_file(), path


async def test_a_run_that_names_no_case_still_drops_every_case_it_holds(tmp_path: Path) -> None:
    """`--coding-repo` alone names no case, so the drop follows the cases the task holds. A
    directory under the root that is no case of this suite is left alone."""
    submissions = tmp_path / "submissions"
    stale = seeded(submissions, SURVEY_CASE.name, "an-earlier-run.md")
    refused = seeded(submissions, f"{PATCH_CASES[0].name}/{REFUSED_DIR}", "old.patch")
    kept = seeded(submissions, "not-a-case-of-this-suite", "old.patch")
    tasks = load_coding_repo(submissions_root=submissions, trees_root=tmp_path / "trees")
    deliverables = next(task for task in tasks if task.name == DELIVERABLES_TASK)
    assert len(deliverables.cases) > 1, "the whole suite delivers more than one file"
    await deliverables.run(SurvivingTurn(), asyncio.Semaphore(1))
    assert not stale.exists()
    assert not refused.exists()
    saved = submissions / SURVEY_CASE.name / Path(SURVEY_CASE.document_path).name
    assert saved.read_bytes() == CAPTURED_DOCUMENT
    assert kept.is_file()


async def test_a_capture_that_cannot_be_dropped_stops_the_run(tmp_path: Path) -> None:
    """A removal that failed silently would leave the last run's bytes to be scored as this run's,
    so the run stops instead. Something squatting the case's own path is how that happens."""
    submissions = tmp_path / "submissions"
    submissions.mkdir()
    (submissions / SURVEY_CASE.name).write_text("a file where the case directory belongs")
    (task,) = load_coding_repo(
        case_names=(SURVEY_CASE.name,),
        submissions_root=submissions,
        trees_root=tmp_path / "trees",
    )
    with pytest.raises(NotADirectoryError):
        await task.run(SurvivingTurn(), asyncio.Semaphore(1))


def test_the_cli_drops_captures_under_the_root_it_is_given_once_the_run_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--coding-repo-submissions` names the root the suite drops and captures under, and nothing
    before the run touches it — every read-only mode and every startup guard runs first."""
    submissions = tmp_path / "submissions"
    leftover = submissions / SURVEY_CASE.name / "an-earlier-run.md"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("a previous run's bytes")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        pack=PackConfig(name="assistant"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    survived: list[bool] = []

    async def reached(
        _config: object, tasks: tuple[EvalTask, ...], *_rest: object, **_kwargs: object
    ) -> NoReturn:
        survived.append(leftover.is_file())
        for task in tasks:
            await task.run(SurvivingTurn(), asyncio.Semaphore(1))
        raise RunStarted

    monkeypatch.setattr("evals.__main__._run", reached)
    with pytest.raises(RunStarted):
        evals_main(
            [
                "--coding-repo",
                "--coding-repo-case",
                SURVEY_CASE.name,
                "--coding-repo-submissions",
                str(submissions),
                "--out",
                str(tmp_path / "out"),
            ]
        )
    assert survived == [True], "no startup step may destroy a capture"
    assert not leftover.exists()
    saved = submissions / SURVEY_CASE.name / Path(SURVEY_CASE.document_path).name
    assert saved.read_bytes() == CAPTURED_DOCUMENT


async def test_the_delegation_gate_reads_the_target_not_the_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "org-repo" / ".git").mkdir(parents=True)
    (workspace / "notes-recon.md").write_text("findings")
    grader = delegation_only_scorer(PARENT_FORBIDDEN_TOOLS)

    def parent(*calls: ToolInvocation) -> CapabilityOutput:
        return replace(
            output(own_tools=tuple(call.name for call in calls)),
            own_calls=calls,
            workspace_dir=workspace,
        )

    handoff = await grader(
        parent(
            ToolInvocation(name="glob", input={"path": "/workspace", "pattern": "notes-recon.md"}),
            ToolInvocation(name="bash", input={"command": "cd /workspace && wc -l notes-recon.md"}),
        )
    )
    assert not handoff.passed
    assert handoff.evidence["checkoutCalls"] == ["glob"]

    scoped_handoff = await grader(
        parent(
            ToolInvocation(name="glob", input={"path": "/workspace/handoff", "pattern": "*.md"}),
        )
    )
    assert scoped_handoff.passed, scoped_handoff.reason
    assert scoped_handoff.evidence["checkouts"] == ["org-repo"]

    inside = await grader(
        parent(ToolInvocation(name="bash", input={"command": "cd /workspace/org-repo && git diff"}))
    )
    assert not inside.passed
    assert "worked the repository itself with bash" in inside.reason
    assert inside.evidence["checkoutCalls"] == ["bash"]
    relative = await grader(
        parent(ToolInvocation(name="bash", input={"command": "git -C org-repo diff"}))
    )
    assert not relative.passed
    grep = await grader(
        parent(
            ToolInvocation(name="grep", input={"pattern": "progress", "glob": "org-repo/**/*.py"})
        )
    )
    assert not grep.passed
    assert grep.evidence["checkoutCalls"] == ["grep"]


def sha1_of(seed: str) -> str:
    return sha256(seed.encode()).hexdigest()[:40]


def _tiny_tree(trees_root: Path, sha: str) -> Path:
    tree = trees_root / sha
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "calc.py").write_text("def triple(n):\n    return n + n\n")
    (tree / ".materialized").write_text(sha)
    return tree


def _new_file_patch(body: str, path: str) -> bytes:
    lines = body.splitlines()
    header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    hunk = f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    return (header + hunk).encode()


def _calc_patch(expression: str) -> bytes:
    return (
        "diff --git a/pkg/calc.py b/pkg/calc.py\n"
        "--- a/pkg/calc.py\n"
        "+++ b/pkg/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def triple(n):\n"
        "-    return n + n\n"
        f"+    return {expression}\n"
    ).encode()


def _tiny_case(trees: Path, name: str) -> CodingCase:
    base_sha = sha1_of(f"{name}-base")
    reference_sha = sha1_of(f"{name}-reference")
    _tiny_tree(trees, base_sha)
    reference = _tiny_tree(trees, reference_sha)
    (reference / "pkg" / "calc.py").write_text("def triple(n):\n    return n * 3\n")
    (reference / "tests").mkdir()
    (reference / "tests" / "test_calc.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg.calc import triple\n\n"
        "def test_triple():\n    assert triple(3) == 9\n"
    )
    return replace(
        PATCH_CASES[0],
        base_sha=base_sha,
        reference_sha=reference_sha,
        held_out_tests=("tests/test_calc.py::test_triple",),
    )


@pytest.mark.parametrize(
    ("name", "expression", "passed"), (("wrong", "n - n", False), ("reference", "n * 3", True))
)
async def test_the_patch_gate_uses_the_held_out_reference_test(
    tmp_path: Path, name: str, expression: str, passed: bool
) -> None:
    trees = tmp_path / "trees"
    case = _tiny_case(trees, name)
    submissions = tmp_path / "submissions" / case.name
    submissions.mkdir(parents=True)
    patch = _calc_patch(expression)
    if name == "wrong":
        patch += _new_file_patch(
            "import sys\nsys.path.insert(0, '.')\nfrom pkg.calc import triple\n\n"
            "def test_triple():\n    assert triple(3) == 0\n",
            "tests/test_calc.py",
        )
    (submissions / f"{case.name}.patch").write_bytes(patch)

    verdict = await PatchRuns(case, tmp_path / "submissions", trees, timeout_seconds=180)(
        output(own_tools=())
    )

    assert verdict.passed is passed, verdict.reason
    assert verdict.evidence["testTargets"] == ["tests/test_calc.py::test_triple"]


async def test_a_candidate_cannot_redirect_a_held_out_test_outside_its_tree(tmp_path: Path) -> None:
    trees = tmp_path / "trees"
    case = _tiny_case(trees, "symlink")
    submissions = tmp_path / "submissions" / case.name
    submissions.mkdir(parents=True)
    (submissions / f"{case.name}.patch").write_bytes(
        _calc_patch("n * 3") + b"diff --git a/tests b/tests\n"
        b"new file mode 120000\n"
        b"--- /dev/null\n"
        b"+++ b/tests\n"
        b"@@ -0,0 +1 @@\n"
        b"+../outside\n"
    )

    verdict = await PatchRuns(case, tmp_path / "submissions", trees)(output(own_tools=()))

    assert not verdict.passed
    assert "held-out test parent" in verdict.reason
    assert "symlink" in verdict.reason


async def test_a_candidate_that_breaks_test_collection_fails_the_gate(tmp_path: Path) -> None:
    trees = tmp_path / "trees"
    case = _tiny_case(trees, "collection")
    submissions = tmp_path / "submissions" / case.name
    submissions.mkdir(parents=True)
    (submissions / f"{case.name}.patch").write_bytes(
        b"diff --git a/pkg/calc.py b/pkg/calc.py\n"
        b"--- a/pkg/calc.py\n"
        b"+++ b/pkg/calc.py\n"
        b"@@ -1,2 +1,2 @@\n"
        b"-def triple(n):\n"
        b"+def other(n):\n"
        b"     return n + n\n"
    )

    verdict = await PatchRuns(case, tmp_path / "submissions", trees)(output(own_tools=()))

    assert not verdict.passed
    assert not verdict.excluded
    assert "held-out tests fail" in verdict.reason


async def test_the_patch_gate_reads_the_patch_the_capture_gate_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = tmp_path / "trees"
    case = _tiny_case(trees, "othername")
    submissions = tmp_path / "submissions" / case.name
    submissions.mkdir(parents=True)
    (submissions / "fix.patch").write_bytes(_calc_patch("n * 3"))
    stubbed_test_run(monkeypatch, True, "1 passed")

    verdict = await PatchRuns(case, tmp_path / "submissions", trees)(output(own_tools=()))

    assert verdict.passed, verdict.reason
    assert verdict.evidence["testTargets"] == ["tests/test_calc.py::test_triple"]
    assert verdict.evidence["testReport"] == "1 passed"


async def test_a_run_that_could_not_happen_leaves_the_case_unscored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = tmp_path / "trees"
    case = _tiny_case(trees, "unrun")
    submissions = tmp_path / "submissions" / case.name
    submissions.mkdir(parents=True)
    (submissions / f"{case.name}.patch").write_bytes(_calc_patch("n * 3"))
    stubbed_test_run(monkeypatch, None, "pytest exceeded 900s")

    verdict = await AllOf((PatchRuns(case, tmp_path / "submissions", trees),))(output(own_tools=()))

    assert not verdict.passed
    assert verdict.excluded
    assert "held-out tests could not run" in verdict.reason
