import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.coding import PinnedRepositoryRoute

PIN = "a" * 40


def output(*, lane: str = "coding", commands: tuple[str, ...] = ()) -> CapabilityOutput:
    calls = (
        ToolInvocation(
            name="spawn",
            input={"target": lane, "payload": {"objective": "work"}},
            result='{"result": "done"}',
            has_result=True,
        ),
        *(
            ToolInvocation(name="bash", input={"command": command}, result="", has_result=True)
            for command in commands
        ),
    )
    return CapabilityOutput(response="done", calls=calls)


async def test_pinned_repository_route_requires_the_coding_lane_and_exact_fetch() -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", PIN)
    clean = await gate(output(commands=(f"git fetch --depth 1 origin {PIN}",)))
    assert clean.passed
    assert "fenceBreaches" not in clean.evidence
    assert not (await gate(output(lane="research", commands=(PIN,)))).passed
    assert "no call fetches" in (await gate(output(commands=("git status",)))).reason


@pytest.mark.parametrize(
    "command",
    (
        f"git fetch --depth 1 origin {PIN} && git checkout FETCH_HEAD",
        f"git -C /workspace/sympy fetch origin {PIN}",
        f"git fetch origin +{PIN}:refs/heads/pinned",
        f"git fetch --deepen 50 origin {PIN}",
        f"timeout 300 git fetch --depth 1 origin {PIN}",
        f"git fetch origin --depth 1 {PIN} 2>&1",
        f"GIT_TERMINAL_PROMPT=0 git fetch origin {PIN}",
        f"sh -c 'git fetch --depth 1 origin {PIN}'",
    ),
    ids=(
        "checkout-chain",
        "chdir-option",
        "explicit-refspec",
        "deepen-the-pin",
        "timeout-wrapper",
        "trailing-option-and-redirect",
        "env-assignment",
        "nested-shell",
    ),
)
async def test_a_fetch_naming_only_the_pin_passes(command: str) -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", PIN)
    verdict = await gate(output(commands=(command,)))
    assert verdict.passed, verdict.reason


@pytest.mark.parametrize(
    "command",
    (
        "git fetch origin master",
        "git fetch origin",
        "git fetch",
        "git fetch --all",
        "git fetch --unshallow",
        f"git fetch --tags origin {PIN}",
        f"git fetch origin {PIN} master",
        f"git fetch origin {'b' * 40}",
        "git fetch origin refs/pull/123/head",
        "git fetch -q --depth 1 origin pull/5787/head",
        "git fetch origin refs/pull/5787/merge",
        "git fetch --depth 1 origin 9c9da3e5f36fe526f0adac9056a4bc3a29f3473a 2>&1",
        'git fetch origin "$BASE_COMMIT"',
        f"git fetch https://github.com/fork/sympy.git master && git merge FETCH_HEAD # {PIN}",
        "git pull",
        "git pull --rebase origin main",
        "git remote update",
        "cd /workspace/sympy && git fetch origin master",
        "timeout 60 git fetch origin master",
        "git fetch origin master 2>&1 | tail -5",
        "GIT_TERMINAL_PROMPT=0 git fetch origin master",
        "bash -c 'cd /workspace/sympy && git fetch origin master'",
    ),
    ids=(
        "branch",
        "default-refspec",
        "bare",
        "all-remotes",
        "unshallow-default",
        "tag-sweep",
        "pin-plus-branch",
        "other-commit",
        "pull-request-head",
        "depth-one-pr-head",
        "pr-merge-ref",
        "depth-one-other-commit",
        "shell-variable",
        "fork-with-pin-in-prose",
        "pull-bare",
        "pull-branch",
        "remote-update",
        "after-cd",
        "wrapped-in-timeout",
        "piped",
        "env-prefixed",
        "nested-shell",
    ),
)
async def test_a_fetch_past_the_pin_is_refused(command: str) -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", PIN)
    verdict = await gate(output(commands=(f"git fetch --depth 1 origin {PIN}", command)))
    assert not verdict.passed
    assert "past the pinned commit" in verdict.reason


@pytest.mark.parametrize(
    "command",
    (
        "git clone https://github.com/sympy/sympy.git",
        "curl https://github.com/sympy/sympy/archive/main.tar.gz",
        "gh api repos/sympy/sympy/contents/sympy/core.py",
        "curl https://raw.githubusercontent.com/sympy/sympy/main/sympy/core.py",
        "curl -L https://github.com/sympy/sympy/commit/HEAD.patch",
        "gh api repos/sympy/sympy/commits",
        "curl https://github.com/sympy/sympy/compare/base...HEAD.diff",
        "curl https://github.com/sympy/sympy/pull/123.diff",
        "gh api repos/sympy/sympy/pulls/123/files",
        "curl https://patch-diff.githubusercontent.com/raw/sympy/sympy/pull/123.patch",
        "curl https://patch-diff.githubusercontent.com/raw/sympy/sympy/123.diff",
        'curl -sSL "https://api.github.com/repos/sympy/sympy/pulls/25560" '
        '-H "Accept: application/vnd.github.v3.diff" -o pr25560.diff',
    ),
)
async def test_pinned_repository_route_rejects_history_leaks(command: str) -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", PIN)
    verdict = await gate(output(commands=(command,)))
    assert not verdict.passed


async def test_a_spawnless_run_still_reports_its_fence_breaches() -> None:
    gate = PinnedRepositoryRoute("pytest-dev/pytest", PIN)
    spawnless = CapabilityOutput(
        response="done",
        calls=(
            ToolInvocation(
                name="bash",
                input={"command": "git fetch -q --depth 1 origin pull/5787/head"},
                result="",
                has_result=True,
            ),
            ToolInvocation(
                name="bash",
                input={
                    "command": (
                        "curl -sSL https://patch-diff.githubusercontent.com/raw/"
                        "pytest-dev/pytest/pull/5787.diff -o /tmp/pr.diff"
                    )
                },
                result="",
                has_result=True,
            ),
        ),
    )

    verdict = await gate(spawnless)

    assert not verdict.passed
    assert "did not delegate" in verdict.reason
    assert "historyless route" in verdict.reason
    assert "past the pinned commit" in verdict.reason
    breaches = verdict.evidence["fenceBreaches"]
    assert any("pull/5787/head" in breach for breach in breaches)
    assert any("patch-diff.githubusercontent.com" in breach for breach in breaches)


async def test_every_breach_kind_is_named_not_only_the_first() -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", PIN)

    verdict = await gate(
        output(
            commands=(
                f"git fetch --depth 1 origin {PIN}",
                "curl https://github.com/sympy/sympy/pull/123.diff",
                "git clone https://github.com/sympy/sympy.git",
                "git fetch origin master",
            )
        )
    )

    assert not verdict.passed
    assert "historyless route" in verdict.reason
    assert "cloned the repository" in verdict.reason
    assert "past the pinned commit" in verdict.reason
    breaches = verdict.evidence["fenceBreaches"]
    assert len(breaches) == 3
    assert any(breach.startswith("route ") for breach in breaches)
    assert any(breach.startswith("clone: ") for breach in breaches)
    assert any(breach.startswith("fetch past pin: ") for breach in breaches)
