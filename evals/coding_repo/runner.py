"""Each pinned coding case as one capability case: the brief reaches the agent as a member wrote it,
a fixed envelope states the commit to fetch and the deliverable to hand back, and the graders read
what the delegated child actually did.

The suite splits by where its cases can be judged. A `reply` case carries its criteria as the
harness's own semantic rubric and is judged inside the run. A `patch` or `document` case is gated
deterministically here — delegated to the coding lane, worked at the pinned commit, and for a patch,
applying to that commit's tree and touching the paths the real change touched — and its bytes are
captured under the submissions root for `evals.coding_repo.grading` to score offline. A patch then
runs against its held-out test tree: the reference test tree plus case-owned tests.

Base trees are materialized and every pin verified against the local clone at load time, before a
turn runs, so a missing commit is a startup error and never a grader that fails a case for the
harness's own reasons."""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PurePosixPath

from evals.coding_repo.cases import (
    CASES,
    DOCUMENT_SUFFIX,
    PATCH_SUFFIX,
    PYTEST_TARGET_SEPARATOR,
    CodingCase,
)
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
    SharedArtifact,
    shared_file_names,
)
from evals.harness.coding import AllOf, PinnedRepositoryRoute
from evals.harness.harness import EvalReport, JsonObject
from evals.harness.registry import EvalRunner, EvalTask, capability_task, rewrapped
from evals.harness.scorers import delegation_only_scorer
from evals.harness.target import CapabilityTarget

REPO_SLUG = "metalcraftai/ufo"
REPO_URL = f"https://github.com/{REPO_SLUG}.git"
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = Path(".local/coding_repo")
SUBMISSIONS_ROOT = LOCAL_ROOT / "submissions"
TREES_ROOT = LOCAL_ROOT / "trees"
HELD_OUT_ROOT = Path(__file__).with_name("held_out")
REFUSED_DIR = "refused"
CODING_REPO_PACKS = ("assistant", "assistant_hosted")
ANSWERS_TASK = "coding_repo_answers"
DELIVERABLES_TASK = "coding_repo_deliverables"
CODING_LANE = "coding"
WORKFLOW_WAIT_SECONDS = 7_200.0
TEST_TIMEOUT_SECONDS = 900.0
ENVELOPE_REVISION = "pinned-fetch-enforced-share-held-out-tests"
GIT_TIMEOUT_SECONDS = 300.0
DIFF_HEADER = "diff --git a/"
PARENT_FORBIDDEN_TOOLS = ("bash", "edit", "grep", "glob")
JUDGE_MODEL = "gpt-5.4-mini"


def load_coding_repo(
    case_names: tuple[str, ...] = (),
    submissions_root: Path = SUBMISSIONS_ROOT,
    trees_root: Path = TREES_ROOT,
) -> tuple[EvalTask, ...]:
    """The suite's tasks over the selected cases, with every pin verified and every base tree
    materialized first."""
    requested = frozenset(case_names)
    unknown = sorted(requested - {case.name for case in CASES})
    if unknown:
        raise ValueError(f"unknown coding_repo case: {', '.join(unknown)}")
    selected = tuple(case for case in CASES if not requested or case.name in requested)
    for case in selected:
        _require_commit(case.base_sha, case.name)
        if case.reference_sha:
            _require_commit(case.reference_sha, case.name)
        if case.deliverable == "patch":
            _materialize_tree(case.base_sha, trees_root)
            reference_tree = _materialize_tree(case.reference_sha, trees_root)
            missing_tests = tuple(
                path
                for path in _test_paths(case.held_out_tests)
                if not _held_out_source(case.name, reference_tree, path).is_file()
            )
            if missing_tests:
                raise ValueError(
                    f"coding_repo case {case.name!r} has missing held-out tests at "
                    f"{case.reference_sha}: {', '.join(missing_tests)}"
                )
            missing_targets: list[str] = []
            for path in _test_paths(case.held_out_tests):
                functions = {
                    node.name
                    for node in ast.parse(
                        _held_out_source(case.name, reference_tree, path).read_text()
                    ).body
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                missing_targets.extend(
                    target
                    for target in case.held_out_tests
                    if target.partition(PYTEST_TARGET_SEPARATOR)[0] == path
                    and target.partition(PYTEST_TARGET_SEPARATOR)[2] not in functions
                )
            if missing_targets:
                raise ValueError(
                    f"coding_repo case {case.name!r} has unknown held-out test targets at "
                    f"{case.reference_sha}: {', '.join(missing_targets)}"
                )
    answers = tuple(
        _capability_case(case, submissions_root, trees_root)
        for case in selected
        if not case.delivers_a_file
    )
    deliverables = tuple(
        _capability_case(case, submissions_root, trees_root)
        for case in selected
        if case.delivers_a_file
    )
    tasks: tuple[EvalTask, ...] = ()
    if answers:
        tasks += (
            rewrapped(
                capability_task(ANSWERS_TASK, answers, judge_model=JUDGE_MODEL),
                lambda task: replace(task, pin_runtime=True),
            ),
        )
    if deliverables:
        tasks += (
            rewrapped(
                capability_task(DELIVERABLES_TASK, deliverables),
                lambda task: replace(
                    task,
                    run=_dropping_captures(task, submissions_root),
                    pin_runtime=True,
                ),
            ),
        )
    return tasks


def _require_commit(sha: str, case_name: str) -> None:
    probe = subprocess.run(
        ("git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"),
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError(
            f"coding_repo case {case_name!r} pins commit {sha} which this clone does not have; "
            "fetch it before running the suite"
        )


def _materialize_tree(sha: str, trees_root: Path = TREES_ROOT) -> Path:
    """The pinned commit's tree on disk, extracted once, so a candidate patch is checked against
    the exact bytes the child worked from. Content-addressed by the commit, so it is built once and
    reused by every later run. The tree is its own work tree: without that, a trees root inside a
    repository lets `git apply` discover the enclosing one and skip the patch it was asked to
    check."""
    target = trees_root / sha
    if (target / ".materialized").exists():
        return target
    if target.exists():
        shutil.rmtree(target)
    staging = trees_root / f"{sha}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    archive = staging.with_suffix(".tar")
    with archive.open("wb") as handle:
        subprocess.run(
            ("git", "-C", str(REPO_ROOT), "archive", "--format=tar", sha),
            stdout=handle,
            check=True,
        )
    subprocess.run(("tar", "-x", "-f", str(archive), "-C", str(staging)), check=True)
    archive.unlink()
    subprocess.run(("git", "init", "--quiet", str(staging)), check=True)
    (staging / ".materialized").write_text(sha)
    staging.rename(target)
    return target


def _capability_case(case: CodingCase, submissions_root: Path, trees_root: Path) -> CapabilityCase:
    graders: list[Grader] = [
        PinnedRepositoryRoute(REPO_SLUG, case.base_sha),
        delegation_only_scorer(PARENT_FORBIDDEN_TOOLS),
    ]
    if case.deliverable == "patch":
        graders.append(PatchCapture(case, submissions_root, trees_root))
        graders.append(PatchRuns(case, submissions_root, trees_root))
    elif case.deliverable == "document":
        graders.append(DocumentCapture(case, submissions_root))
    return CapabilityCase(
        name=case.name,
        message=case.brief + _envelope(case),
        grader=AllOf(tuple(graders)),
        rubric=() if case.delivers_a_file else case.criteria,
        digest_tag=(
            f"{ENVELOPE_REVISION}:delegated:"
            f"{case.name}:{case.base_sha}:{case.reference_sha}:"
            f"{sha256(' '.join(case.criteria).encode()).hexdigest()[:16]}"
        ),
    )


def _envelope(case: CodingCase) -> str:
    deliverable = {
        "reply": "Deliverable: your findings in this conversation's reply. Change no files.",
        "patch": (
            "Two deliverables, both through share_file, because only shared files are scored:\n"
            f"1. /workspace/{case.name}.patch — the complete unified diff of the change relative "
            "to the fetched commit, whether or not the work was committed along the way.\n"
            f"2. /workspace/{case.notes_name} — a short note on the change: what it does and why, "
            "what was verified and how, what remains uncertain, and anything it reassigns or "
            "breaks. A diff cannot say those things, and they are read as part of the work."
        ),
        "document": (
            f"Deliverable: {case.document_path}, delivered with share_file. Only a shared file is "
            "scored."
        ),
    }[case.deliverable]
    return (
        "\n\n---\n"
        "Evaluation setup, to pass on to whoever does the work:\n"
        f"The repository is {REPO_URL} at commit {case.base_sha}. Exactly that one commit is to be "
        "fetched — shallow, by commit, not a clone of the default branch — and the work happens in "
        "that checkout. Commits after it are not part of this task, and the repository is not "
        "to be read through any other route.\n"
        f"{deliverable}"
    )


def _dropping_captures(task: EvalTask, submissions_root: Path) -> EvalRunner:
    """The task's run with each of its own cases' captured deliverables dropped as it starts, so a
    case that shares nothing this run scores as having shared nothing rather than on the last run's
    bytes. A failed removal raises: a capture that survives is read as this run's."""

    async def dropping(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        for name in task.cases:
            case_dir = submissions_root / name
            if case_dir.exists():
                await asyncio.to_thread(shutil.rmtree, case_dir)
        return await task.run(target, slots)

    return dropping


@dataclass(frozen=True)
class DocumentCapture:
    """The document the brief asked for, captured for offline grading: shared under the name the
    brief named, and not empty."""

    case: CodingCase
    submissions_root: Path = SUBMISSIONS_ROOT

    @property
    def grading(self) -> str:
        return f"shares a non-empty {PurePosixPath(self.case.document_path).name}"

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        wanted = PurePosixPath(self.case.document_path).name
        selected, failure = _delivered(output, self.case.deliverable_suffix, wanted)
        if selected is None:
            return CapabilityVerdict(False, failure)
        text = selected.content.decode("utf-8", errors="replace").strip()
        if not text:
            return CapabilityVerdict(False, f"{selected.name} is empty")
        saved = await _save(self.submissions_root / self.case.name, selected)
        return CapabilityVerdict(
            True,
            f"captured {selected.name} ({len(text)} chars)",
            {"submission": str(saved), "words": len(text.split())},
        )


@dataclass(frozen=True)
class PatchCapture:
    """The candidate patch, gated on being a real change to the pinned tree before its quality is
    anybody's question: it parses as a unified diff, it applies to the commit the child worked from,
    and it touches at least one file the reference change touched. The bytes are saved either way,
    so a patch that fails the gate is still there to read — under `REFUSED_DIR`, out of the offline
    judge's reach, because a deliverable earns a judge only once the run proves it real."""

    case: CodingCase
    submissions_root: Path = SUBMISSIONS_ROOT
    trees_root: Path = TREES_ROOT

    @property
    def grading(self) -> str:
        return (
            f"shares a {self.case.deliverable_suffix} applying to {self.case.base_sha[:12]} that "
            f"touches one of {', '.join(self.case.expected_paths)}"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        selected, failure = _delivered(output, self.case.deliverable_suffix)
        if selected is None:
            return CapabilityVerdict(False, failure)
        notes, _ = _delivered(output, DOCUMENT_SUFFIX, self.case.notes_name)
        shared = (selected,) if notes is None else (selected, notes)
        evidence: JsonObject = {
            "sizeBytes": len(selected.content),
            "notes": None if notes is None else len(notes.content),
        }
        text = selected.content.decode("utf-8", errors="replace")
        touched = _touched_paths(text)
        evidence["touchedPaths"] = list(touched)
        if not touched:
            return await self._refused(shared, f"{selected.name} carries no unified diff", evidence)
        applied, detail = await _applies(self.trees_root, self.case.base_sha, selected.content)
        evidence["appliesClean"] = applied
        if not applied:
            return await self._refused(
                shared,
                f"{selected.name} does not apply to {self.case.base_sha[:12]}: {detail}",
                evidence,
            )
        overlap = tuple(path for path in touched if path in self.case.expected_paths)
        evidence["expectedPathsTouched"] = list(overlap)
        if not overlap:
            return await self._refused(
                shared,
                f"{selected.name} touches {', '.join(touched)}, none of the paths the reference "
                f"change touched",
                evidence,
            )
        case_dir = self.submissions_root / self.case.name
        evidence["submission"] = str(await _save(case_dir, selected))
        if notes is not None:
            await _save(case_dir, notes)
        return CapabilityVerdict(
            True, f"captured {selected.name}: applies clean, touches {', '.join(overlap)}", evidence
        )

    async def _refused(
        self, shared: tuple[SharedArtifact, ...], reason: str, evidence: JsonObject
    ) -> CapabilityVerdict:
        refused = self.submissions_root / self.case.name / REFUSED_DIR
        for artifact in shared:
            await _save(refused, artifact)
        evidence["refused"] = str(refused)
        return CapabilityVerdict(False, reason, evidence)


def _delivered(
    output: CapabilityOutput, suffix: str, name: str = ""
) -> tuple[SharedArtifact | None, str]:
    """The artifact a successful `share_file` handed back, by suffix and optionally by exact name.
    A child's share records against the child's own turn, and the harness collects descendants'
    artifacts, so this reads the same list either way."""
    if output.artifact_error:
        return None, f"artifact collection failed: {output.artifact_error}"
    shared: set[str] = set()
    for call in output.calls:
        shared.update(shared_file_names(call))
    candidates = tuple(
        artifact
        for artifact in output.artifacts
        if artifact.name in shared and artifact.name.lower().endswith(suffix)
    )
    if name:
        exact = tuple(
            artifact for artifact in candidates if PurePosixPath(artifact.name).name == name
        )
        if not exact:
            return None, f"did not share {name}"
        return exact[-1], ""
    if not candidates:
        return None, f"did not share a {suffix} file"
    return candidates[-1], ""


def _test_paths(targets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(target.partition(PYTEST_TARGET_SEPARATOR)[0] for target in targets))


def _held_out_source(case_name: str, reference_tree: Path, path: str) -> Path:
    corpus_test = HELD_OUT_ROOT / case_name / path
    return corpus_test if corpus_test.is_file() else reference_tree / path


@dataclass(frozen=True)
class PatchRuns:
    case: CodingCase
    submissions_root: Path
    trees_root: Path
    timeout_seconds: float = TEST_TIMEOUT_SECONDS

    @property
    def grading(self) -> str:
        return "the patch passes the held-out tests against the pinned tree"

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        patch = await asyncio.to_thread(self._captured_patch)
        if patch is None:
            return CapabilityVerdict(False, "no patch to run tests from")
        content = await asyncio.to_thread(patch.read_bytes)
        evidence: JsonObject = {"testTargets": list(self.case.held_out_tests)}
        tree, detail = await self._patched_tree(content)
        if tree is None:
            return CapabilityVerdict(False, f"could not patch the tree to run tests: {detail}")
        passed, report = await self._run_tests(tree)
        evidence["testReport"] = report
        if passed is None:
            return CapabilityVerdict(
                False, f"held-out tests could not run: {report}", evidence, excluded=True
            )
        if not passed:
            return CapabilityVerdict(False, f"the held-out tests fail: {report}", evidence)
        return CapabilityVerdict(True, "the held-out tests pass", evidence)

    def _captured_patch(self) -> Path | None:
        case_dir = self.submissions_root / self.case.name
        if not case_dir.is_dir():
            return None
        captured = sorted(
            (
                path
                for path in case_dir.iterdir()
                if path.is_file() and path.suffix.lower() == PATCH_SUFFIX
            ),
            key=lambda path: path.stat().st_mtime,
        )
        return captured[-1] if captured else None

    async def _patched_tree(self, patch: bytes) -> tuple[Path | None, str]:
        source = self.trees_root / self.case.base_sha
        reference = self.trees_root / self.case.reference_sha
        target = self.trees_root / f"{self.case.base_sha}-run"
        await asyncio.to_thread(shutil.rmtree, target, True)
        await asyncio.to_thread(shutil.copytree, source, target, symlinks=True)
        process = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            "-p1",
            "-",
            cwd=target,
            env={**os.environ, "GIT_CEILING_DIRECTORIES": str(self.trees_root)},
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
        )
        _, stderr = await process.communicate(patch)
        if process.returncode:
            return None, stderr.decode(errors="replace").strip()[:240]
        detail = await asyncio.to_thread(
            self._overlay_held_out_tests, target, reference, _test_paths(self.case.held_out_tests)
        )
        return (None, detail) if detail else (target, "")

    def _overlay_held_out_tests(
        self, target: Path, reference: Path, test_paths: tuple[str, ...]
    ) -> str:
        for path in test_paths:
            destination = target / path
            relative_parent = destination.parent.relative_to(target)
            parent = target
            for part in relative_parent.parts:
                parent /= part
                if parent.is_symlink():
                    return f"candidate made held-out test parent {parent} a symlink"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                destination.unlink()
            source = _held_out_source(self.case.name, reference, path)
            shutil.copy2(source, destination, follow_symlinks=False)
        return ""

    async def _run_tests(self, tree: Path) -> tuple[bool | None, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "--frozen",
                "pytest",
                *self.case.held_out_tests,
                "-q",
                "-p",
                "no:cacheprovider",
                cwd=tree,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as error:
            return None, str(error)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None, f"pytest exceeded {self.timeout_seconds:.0f}s"
        tail = stdout.decode(errors="replace").strip().splitlines()
        report = " / ".join(tail[-3:])[:400]
        return process.returncode == 0, report


def _touched_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith(DIFF_HEADER):
            continue
        remainder = line[len(DIFF_HEADER) :]
        marker = remainder.find(" b/")
        path = remainder[:marker] if marker > 0 else remainder
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


async def _applies(trees_root: Path, base_sha: str, patch: bytes) -> tuple[bool, str]:
    tree = trees_root / base_sha
    process = await asyncio.create_subprocess_exec(
        "git",
        "apply",
        "--check",
        "-p1",
        "-",
        cwd=tree,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(patch), timeout=GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return False, "git apply --check timed out"
    if process.returncode == 0:
        return True, ""
    return False, stderr.decode(errors="replace").strip()[:240]


async def _save(directory: Path, artifact: SharedArtifact) -> Path:
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    target = directory / PurePosixPath(artifact.name).name
    await asyncio.to_thread(target.write_bytes, artifact.content)
    return target
