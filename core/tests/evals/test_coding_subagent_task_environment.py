import asyncio
from uuid import uuid4

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    prepare_task_repository,
    task_environment_scorer,
    url_repository_scorer,
    workspace_repository_scorer,
)


def load() -> ToolInvocation:
    return ToolInvocation("load_skill", {"name": "coding"}, "workflow", True)


async def test_task_environment_scorer_accepts_path_lookup_then_delegation() -> None:
    loaded = load()
    located = ToolInvocation(
        "bash",
        {"command": "find /workspace -name .git -type d"},
        "/workspace/dclm/.git",
        True,
    )
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {
                "objective": (
                    "Repository setup: use the existing checkout at /workspace/dclm. Do not "
                    "clone or fetch; if it is missing, report it."
                )
            },
        },
        "missing model key",
        False,
    )

    verdict = await task_environment_scorer()(
        CapabilityOutput("failed", (loaded, located, spawned), own_calls=(loaded, located, spawned))
    )

    assert verdict.passed


async def test_task_environment_scorer_rejects_clone_route() -> None:
    loaded = load()
    located = ToolInvocation(
        "bash",
        {"command": "find /workspace -name .git -type d"},
        "/workspace/dclm/.git",
        True,
    )
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {"objective": "Repository setup: clone dclm into /workspace/dclm with git."},
        },
        "done",
        True,
    )

    verdict = await task_environment_scorer()(
        CapabilityOutput("done", (loaded, located, spawned), own_calls=(loaded, located, spawned))
    )

    assert not verdict.passed


async def test_task_environment_scorer_rejects_a_remote_search_before_the_checkout() -> None:
    loaded = load()
    located = ToolInvocation(
        "bash",
        {"command": "find /workspace -name .git -type d"},
        "/workspace/dclm/.git",
        True,
    )
    searched = ToolInvocation(
        "search_web",
        {"queries": ["Which repository URL backs the dclm checkout?"]},
        "https://github.com/mlfoundations/dclm",
        True,
    )
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {
                "objective": (
                    "Repository setup: use the existing checkout at /workspace/dclm. Do not "
                    "clone or fetch; if it is missing, report it."
                )
            },
        },
        "missing model key",
        False,
    )
    calls = (loaded, located, searched, spawned)

    verdict = await task_environment_scorer()(CapabilityOutput("failed", calls, own_calls=calls))

    assert not verdict.passed
    assert verdict.reason == "the parent searched remotely before using the checkout"


async def test_task_environment_scorer_rejects_parent_repository_work() -> None:
    loaded = load()
    inspected = ToolInvocation(
        "bash",
        {"command": "git -C /workspace/dclm ls-files"},
        "README.md",
        True,
    )
    spawned = ToolInvocation(
        "spawn",
        {
            "target": "coding",
            "payload": {
                "objective": (
                    "Repository setup: use the existing checkout at /workspace/dclm. Do not "
                    "clone or fetch; if it is missing, report it."
                )
            },
        },
        "done",
        True,
    )

    verdict = await task_environment_scorer()(
        CapabilityOutput(
            "done", (loaded, inspected, spawned), own_calls=(loaded, inspected, spawned)
        )
    )

    assert not verdict.passed


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
