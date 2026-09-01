import asyncio
from uuid import uuid4

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    EXISTING_CHECKOUT_URL_FALLBACK_OBJECTIVE,
    PROFILE_CASES,
    existing_checkout_execution_scorer,
    prepare_fallback_repository,
    prepare_task_repository,
    profile_proxy_message,
    url_repository_scorer,
    workspace_repository_scorer,
)


def load() -> ToolInvocation:
    return ToolInvocation("load_skill", {"name": "coding"}, "workflow", True)


def test_the_url_fallback_profile_case_uses_the_coding_proxy() -> None:
    cases = {case.name: case for case in PROFILE_CASES}
    case = cases["coding-profile-existing-checkout-url-fallback"]

    assert case.message == profile_proxy_message(EXISTING_CHECKOUT_URL_FALLBACK_OBJECTIVE)
    assert case.grader.grading.startswith("one exact profile:coding spawn satisfies:")


async def test_prepare_task_repository_creates_three_tracked_files(tmp_path) -> None:
    await prepare_task_repository(uuid4(), tmp_path)
    process = await asyncio.create_subprocess_exec(
        "git",
        "ls-files",
        cwd=tmp_path / "dclm",
        stdout=asyncio.subprocess.PIPE,
    )
    output, _error = await process.communicate()

    assert process.returncode == 0
    assert len(output.splitlines()) == 3


async def test_prepare_fallback_repository_creates_source_only(tmp_path) -> None:
    await prepare_fallback_repository(uuid4(), tmp_path)

    assert (tmp_path / "source" / ".git").is_dir()
    assert not (tmp_path / "dclm").exists()


async def test_existing_checkout_execution_scorer_accepts_url_fallback() -> None:
    cloned = ToolInvocation(
        "bash",
        {
            "command": (
                "git clone file:///workspace/source /workspace/dclm && "
                "git -C /workspace/dclm ls-files | wc -l"
            )
        },
        "3",
        True,
    )

    verdict = await existing_checkout_execution_scorer()(
        CapabilityOutput("ANSWER: 3", (cloned,), own_calls=(cloned,))
    )

    assert verdict.passed


async def test_existing_checkout_execution_scorer_accepts_separate_probe_and_clone() -> None:
    probed = ToolInvocation(
        "bash",
        {"command": "ls -d /workspace/dclm 2>/dev/null || echo ABSENT"},
        "ABSENT",
        True,
    )
    cloned = ToolInvocation(
        "bash",
        {"command": "cd /workspace && git clone file:///workspace/source /workspace/dclm"},
        "",
        True,
    )
    inspected = ToolInvocation(
        "bash",
        {"command": "cd /workspace/dclm && git ls-files | wc -l"},
        "3",
        True,
    )

    verdict = await existing_checkout_execution_scorer()(
        CapabilityOutput(
            "ANSWER: 3",
            (probed, cloned, inspected),
            own_calls=(),
        )
    )

    assert verdict.passed


async def test_existing_checkout_execution_scorer_rejects_a_missing_clone() -> None:
    inspected = ToolInvocation(
        "bash",
        {"command": "ls -d /workspace/dclm && git -C /workspace/dclm ls-files | wc -l"},
        "/workspace/dclm\n3",
        True,
    )

    verdict = await existing_checkout_execution_scorer()(
        CapabilityOutput("ANSWER: 3", (inspected,), own_calls=())
    )

    assert not verdict.passed


async def test_workspace_repository_scorer_accepts_existing_checkout() -> None:
    loaded = load()
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {
                "objective": (
                    "Repository setup: use the existing checkout at /workspace/dclm. Do not "
                    "clone. Report the tracked-file count."
                )
            },
        },
        "missing model key",
        False,
    )

    verdict = await workspace_repository_scorer()(
        CapabilityOutput("failed", (loaded, spawned), own_calls=(loaded, spawned))
    )

    assert verdict.passed


async def test_workspace_repository_scorer_rejects_parent_work() -> None:
    loaded = load()
    inspected = ToolInvocation("bash", {"command": "git -C /workspace/dclm ls-files"}, "3", True)

    verdict = await workspace_repository_scorer()(
        CapabilityOutput("3", (loaded, inspected), own_calls=(loaded, inspected))
    )

    assert not verdict.passed


async def test_url_repository_scorer_accepts_delegation_even_when_child_fails() -> None:
    loaded = load()
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {
                "objective": (
                    "Repository setup: clone https://github.com/octocat/Hello-World into "
                    "/workspace/octocat-Hello-World with git, then work inside it."
                )
            },
        },
        "missing model key",
        False,
    )

    verdict = await url_repository_scorer()(
        CapabilityOutput("failed", (loaded, spawned), own_calls=(loaded, spawned))
    )

    assert verdict.passed


async def test_url_repository_scorer_rejects_parent_inspection() -> None:
    loaded = load()
    inspected = ToolInvocation(
        "bash",
        {"command": "git clone https://github.com/octocat/Hello-World"},
        "",
        True,
    )

    verdict = await url_repository_scorer()(
        CapabilityOutput("1", (loaded, inspected), own_calls=(loaded, inspected))
    )

    assert not verdict.passed
