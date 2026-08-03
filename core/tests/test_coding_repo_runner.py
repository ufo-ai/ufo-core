"""The pinned coding suite's gates. The load-bearing proof is that every case's own merged diff —
the answer this repository shipped — passes the gate at that case's base commit: a gate that rejects
the real answer would score every candidate against nothing."""

import json
import shutil
from pathlib import Path

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
    PARENT_FORBIDDEN_TOOLS,
    REPO_ROOT,
    REPO_SLUG,
    REPO_URL,
    AllOf,
    DocumentGate,
    LaneAndRoute,
    PatchGate,
    _capability_case,
    _materialize_tree,
    _touched_paths,
    load_coding_repo,
)
from evals.harness.capability import CapabilityOutput, SharedArtifact, ToolInvocation
from evals.harness.scorers import delegation_only_scorer
from ufo.config import BlobConfig, Config, DatabaseConfig, PackConfig

SURVEY_CASE = next(case for case in CASES if case.deliverable == "document")
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


def output(
    *,
    lane: str = CODING_LANE,
    delegated: bool = True,
    commands: tuple[str, ...] = (),
    artifacts: tuple[SharedArtifact, ...] = (),
    shared_names: tuple[str, ...] = (),
    own_tools: tuple[str, ...] = (),
) -> CapabilityOutput:
    calls: list[ToolInvocation] = []
    if delegated:
        calls.append(
            ToolInvocation(
                name="spawn_subagent",
                input={"profile": lane, "payload": {"objective": "work"}},
                result='{"result": "done"}',
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
            input={"path": f"/workspace/{name}"},
            result=json.dumps({"name": name}),
            has_result=True,
        )
        for name in shared_names
    )
    return CapabilityOutput(
        response="done", calls=tuple(calls), artifacts=artifacts, own_tools=own_tools
    )


@pytest.mark.parametrize("case", PATCH_CASES, ids=lambda case: case.name)
async def test_the_shipped_diff_passes_the_gate_at_its_base_commit(
    case: CodingCase, tmp_path: Path
) -> None:
    _materialize_tree(case.base_sha, tmp_path / "trees")
    patch = reference_diff(case.reference_sha)
    name = f"{case.name}.patch"
    grader = PatchGate(case, tmp_path / "trees")
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


async def test_an_off_target_patch_is_refused_though_it_applies(tmp_path: Path) -> None:
    """A patch for an unrelated case touches no file the reference change touched, so it clears the
    apply check on disjoint files and the paths gate is what refuses it. Both gates carry weight."""
    case, other = PATCH_CASES[0], PATCH_CASES[2]
    _materialize_tree(case.base_sha, tmp_path / "trees")
    name = f"{case.name}.patch"
    grader = PatchGate(case, tmp_path / "trees")
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
    grader = PatchGate(case, tmp_path / "trees")
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
    grader = PatchGate(case, tmp_path / "trees")
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
    grader = PatchGate(case, tmp_path / "trees")
    verdict = await grader(output(artifacts=(SharedArtifact(name=name, content=b"diff"),)))
    assert not verdict.passed
    assert "did not share" in verdict.reason


async def test_the_lane_gate_requires_a_coding_child_at_the_pin() -> None:
    case = PATCH_CASES[0]
    grader = LaneAndRoute(case.base_sha)
    passed = await grader(output(commands=(f"git fetch --depth 1 origin {case.base_sha}",)))
    assert passed.passed, passed.reason
    assert (await grader(output(delegated=False))).reason == "did not delegate"
    wrong_lane = await grader(output(lane="research", commands=(case.base_sha,)))
    assert not wrong_lane.passed
    assert CODING_LANE in wrong_lane.reason
    unpinned = await grader(output(commands=("ls /workspace",)))
    assert not unpinned.passed
    assert "no call fetches" in unpinned.reason


async def test_the_lane_gate_refuses_a_historyless_route() -> None:
    """One case per argument a call reaches a target through, and per route that hands back a tree
    with no history — a shell command, a REPL, a URL fetch, and a connector action."""
    case = PATCH_CASES[0]
    grader = LaneAndRoute(case.base_sha)
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
    grader = DocumentGate(SURVEY_CASE)
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


def test_the_envelope_pins_the_commit_and_names_the_deliverable(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    capability = _capability_case(case, tmp_path / "trees")
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
        envelope = _capability_case(case, tmp_path / "trees").message
        envelope = envelope[envelope.index("Evaluation setup") :]
        assert "to pass on to whoever does the work" in envelope
        for command in ("git init", "git remote add", "git fetch", "git checkout", "cd repo"):
            assert command not in envelope, f"{case.name} envelope spells out {command!r}"
    reply = _capability_case(RESEARCH_CASES[0], tmp_path / "trees")
    assert reply.rubric == RESEARCH_CASES[0].criteria
    assert "Change no files." in reply.message


def test_a_case_states_what_its_kind_requires() -> None:
    for extra in ({"reference_sha": "1" * 40}, {"expected_paths": ("core/src/ufo/db.py",)}):
        with pytest.raises(ValueError, match="a reference commit or expected paths"):
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


def test_load_splits_the_suite_by_how_a_case_is_measured(tmp_path: Path) -> None:
    tasks = load_coding_repo(
        case_names=(RESEARCH_CASES[0].name, PATCH_CASES[0].name), trees_root=tmp_path / "trees"
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


async def test_a_materialized_tree_is_its_own_work_tree(tmp_path: Path) -> None:
    """A trees root inside a repository lets `git apply` discover the enclosing work tree and skip
    the patch with exit 0, which reads as applies-clean. Each tree owns its git dir so it cannot."""
    case = PATCH_CASES[0]
    inside = REPO_ROOT / ".local" / "trees-under-repo"
    try:
        tree = _materialize_tree(case.base_sha, inside)
        assert (tree / ".git").is_dir()
        grader = PatchGate(case, inside)
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
    grader = LaneAndRoute(case.base_sha)
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
    grader = AllOf((LaneAndRoute(case.base_sha), delegation_only_scorer(PARENT_FORBIDDEN_TOOLS)))
    verdict = await grader(output(commands=(f"git fetch --depth 1 origin {case.base_sha}",)))
    assert verdict.passed, verdict.reason
    assert "delegated to 'coding'" in verdict.reason
    assert "delegated without working" in verdict.reason
    assert verdict.evidence["codingSpawns"] == 1
    assert verdict.evidence["ownTools"] == []
    assert "delegates to the 'coding' subagent" in grader.grading
    assert "calls none of" in grader.grading


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
    envelope = _capability_case(case, tmp_path / "t").message
    relayed = CapabilityOutput(
        response="done",
        calls=(
            ToolInvocation(
                name="spawn_subagent",
                input={"profile": CODING_LANE, "payload": {"objective": envelope}},
                result='{"result": "done"}',
                has_result=True,
            ),
        ),
    )
    grader = LaneAndRoute(case.base_sha)
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
    not a run that delegates to a profile no manifest registers. One reply case selects the run, so
    the guard is reached without materializing a pinned tree."""
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
                "--out",
                str(tmp_path),
            ]
        )
    assert "coding_repo requires [pack] name in" in capsys.readouterr().err


async def test_the_route_gate_reads_only_the_arguments_a_call_reaches_a_target_through() -> None:
    """A note, a search for the phrase, and a path that merely spells `archive/` are prose or
    working files, not routes. Reading them refuses runs that took no route at all."""
    case = PATCH_CASES[0]
    grader = LaneAndRoute(case.base_sha)
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
    grader = LaneAndRoute(case.base_sha)
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
    grader = LaneAndRoute(case.base_sha)
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
    grader = LaneAndRoute(case.base_sha)
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
    verdict = await LaneAndRoute(case.base_sha)(
        CapabilityOutput(response="done", calls=(*output().calls, errored))
    )
    assert not verdict.passed
    assert "no call fetches" in verdict.reason


async def test_the_delegation_gate_refuses_a_turn_that_worked_the_repository_itself() -> None:
    grader = delegation_only_scorer(PARENT_FORBIDDEN_TOOLS)
    verdict = await grader(output(own_tools=("read", "bash", "edit")))
    assert not verdict.passed
    assert "did the work itself with bash, edit" in verdict.reason
    assert verdict.evidence["ownTools"] == ["read", "bash", "edit"]
    assert (await grader(output(own_tools=("read", "share_file")))).passed
