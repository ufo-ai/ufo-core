import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import ufo_ext_coding.review_checkout as review_checkout
from ufo_ext_coding.review_checkout import (
    CODE_REVIEW_PROFILE,
    CODE_REVIEW_ROUND_LIMIT,
    CODE_REVIEW_TOOL,
    CheckoutCodeReviewInput,
    ExactComparison,
    ExactComparisonCheckout,
    ReviewGlobInput,
    ReviewGrepInput,
    ReviewReadInput,
    checkout_code_review,
    review_glob,
    review_grep,
    review_read,
)
from ufo_ext_coding.review_routing import ReviewTarget

from ufo.ext.manifest import SUBAGENT_ROUND_LIMIT
from ufo.sandbox.local import LocalCarrier
from ufo.sdk.context import ExtensionContext
from ufo.sdk.sandbox import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.sdk.tools import ToolContext

PULL_NUMBER = 17


async def _git(path: Path, *args: str, check: bool = True) -> tuple[str, str, int]:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    result = stdout.decode(), stderr.decode(), process.returncode or 0
    if check and result[2] != 0:
        raise RuntimeError(result[1])
    return result


async def _write(path: Path, value: str | bytes) -> None:
    if isinstance(value, str):
        await asyncio.to_thread(path.write_text, value)
        return
    await asyncio.to_thread(path.write_bytes, value)


async def _comparison(tmp_path: Path) -> tuple[Path, Path, str, str]:
    remote = tmp_path / "source.git"
    source = tmp_path / "source"
    remote.mkdir()
    source.mkdir()
    await _git(remote, "init", "--bare", ".")
    await _git(source, "init", "--initial-branch=main", ".")
    await _git(source, "config", "user.name", "Reviewer Test")
    await _git(source, "config", "user.email", "reviewer@example.com")
    await _write(source / "kept.txt", "base\n")
    await _write(source / "changed.txt", "before\n")
    await _write(source / "asset.bin", bytes(range(64)))
    await _git(source, "add", ".")
    await _git(source, "commit", "-m", "root")
    await _git(source, "branch", "feature")
    await _write(source / "kept.txt", "base branch\n")
    await _git(source, "add", ".")
    await _git(source, "commit", "-m", "base")
    base = (await _git(source, "rev-parse", "HEAD"))[0].strip()
    await _git(source, "remote", "add", "origin", str(remote))
    await _git(source, "push", "origin", "HEAD:refs/heads/main")
    await _git(source, "checkout", "feature")
    await _write(source / "changed.txt", "after\n")
    await _write(source / "added.txt", "new\n")
    await _write(source / "large.txt", f"TARGET {'x' * 10_000}\nTARGET second\n")
    await _write(source / "wide.txt", "WIDE match\n" * 20_000)
    await _write(source / "asset.bin", bytes(reversed(range(64))))
    await _git(source, "add", ".")
    await _git(source, "commit", "-m", "head")
    head = (await _git(source, "rev-parse", "HEAD"))[0].strip()
    await _git(source, "push", "origin", f"HEAD:refs/pull/{PULL_NUMBER}/head")
    return remote, source, base, head


async def _sandbox(tmp_path: Path) -> SandboxSession:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=9999, ca_cert="test-ca"),
            run_token="run-token",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


async def test_exact_comparison_checkout_uses_the_real_remote_and_leaves_no_push_target(
    tmp_path: Path,
) -> None:
    remote, source, base, head = await _comparison(tmp_path)
    workflow = ExactComparisonCheckout(
        await _sandbox(tmp_path), str(remote), "/workspace/.ufo-review/test"
    )
    checkout = await workflow.run(
        ExactComparison(
            repository="metalcraftai/ufo",
            pull_number=PULL_NUMBER,
            base_sha=base,
            head_sha=head,
        )
    )
    root = tmp_path / "workspace" / checkout.path.removeprefix("/workspace/")
    expected = (
        await _git(
            source,
            "diff",
            "--no-ext-diff",
            "--binary",
            "--full-index",
            "--find-renames",
            f"{base}...{head}",
        )
    )[0]

    await asyncio.to_thread(remote.rename, tmp_path / "offline.git")
    assert (await _git(root, "rev-parse", "HEAD"))[0].strip() == head
    assert (await _git(root, "rev-parse", "refs/ufo/base"))[0].strip() == base
    assert (await _git(root, "show", "HEAD:changed.txt"))[0] == "after\n"
    diff = tmp_path / "workspace" / checkout.diff_path.removeprefix("/workspace/")
    assert await asyncio.to_thread(diff.read_text) == expected
    assert "GIT binary patch" in expected
    assert (await _git(root, "remote"))[0] == ""
    config = await asyncio.to_thread((root / ".git" / "config").read_text)
    assert "extraheader" not in config.casefold()
    assert str(remote) not in config
    assert (await _git(root, "fsck", "--full"))[2] == 0
    assert (await _git(root, "push", check=False))[2] != 0
    await workflow.remove()
    assert not await asyncio.to_thread(root.exists)


async def test_checkout_tool_records_its_conversation_and_registers_turn_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[object, ReviewTarget, UUID]] = []

    async def record(ext: object, target: ReviewTarget, conversation_id: UUID) -> None:
        recorded.append((ext, target, conversation_id))

    monkeypatch.setattr(review_checkout, "record_review_conversation", record)
    remote, _, base, head = await _comparison(tmp_path)
    sandbox = await _sandbox(tmp_path)
    rewrite = await sandbox.carrier.exec(
        sandbox.handle,
        (
            "git",
            "config",
            "--global",
            f"url.{remote.as_uri()}.insteadOf",
            "https://github.com/metalcraftai/ufo.git",
        ),
        timeout_s=30,
    )
    assert rewrite.exit_code == 0
    comparison = ExactComparison(
        repository="metalcraftai/ufo",
        pull_number=PULL_NUMBER,
        base_sha=base,
        head_sha=head,
    )
    turn_id = uuid4()
    conversation_id = uuid4()
    ext = SimpleNamespace()
    context = ToolContext(
        sandbox=sandbox,
        blob=None,
        turn=SimpleNamespace(
            id=turn_id,
            conversation_id=conversation_id,
            subagent_profile="code_review",
            inbound=comparison.model_dump_json(),
        ),
        agent=SimpleNamespace(),
        spawn=None,
        speaker_member_id=None,
        audience=None,
        artifact_token_secret="",
        ext=cast(ExtensionContext, ext),
    )
    await checkout_code_review(
        context,
        CheckoutCodeReviewInput(
            **comparison.model_dump(), user_description="Preparing the review."
        ),
    )
    assert recorded == [
        (
            ext,
            ReviewTarget(
                repository="metalcraftai/ufo",
                pull_request_number=PULL_NUMBER,
                base_sha=base,
                head_sha=head,
            ),
            conversation_id,
        )
    ]
    checkout = tmp_path / "workspace" / ".ufo-review" / turn_id.hex
    assert await asyncio.to_thread(checkout.exists)
    await context.cleanup.drain()
    assert not await asyncio.to_thread(checkout.exists)


@pytest.mark.parametrize("wrong", ("repository", "pull_number", "base_sha", "head_sha"))
async def test_exact_comparison_checkout_refuses_every_wrong_identity(
    tmp_path: Path, wrong: str
) -> None:
    remote, _, base, head = await _comparison(tmp_path)
    other = tmp_path / "other.git"
    other.mkdir()
    await _git(other, "init", "--bare", ".")
    values: dict[str, object] = {
        "repository": "metalcraftai/ufo",
        "pull_number": PULL_NUMBER,
        "base_sha": base,
        "head_sha": head,
    }
    replacements: dict[str, object] = {
        "repository": "metalcraftai/another",
        "pull_number": PULL_NUMBER + 1,
        "base_sha": "0" * 40,
        "head_sha": base,
    }
    values[wrong] = replacements[wrong]
    if wrong == "repository":
        remote = other

    named = {
        "repository": "exited",
        "pull_number": "exited",
        "base_sha": f"fetch {'0' * 40} exited",
        "head_sha": f"checked out base {base} head {head}",
    }
    with pytest.raises(ValueError, match="comparison does not match") as refused:
        await ExactComparisonCheckout(
            await _sandbox(tmp_path), str(remote), "/workspace/.ufo-review/test"
        ).run(ExactComparison.model_validate(values))
    assert named[wrong] in str(refused.value)
    reviews = tmp_path / "workspace" / ".ufo-review"
    assert not list(reviews.iterdir())


def test_review_lifts_its_round_budget_above_the_default() -> None:
    assert CODE_REVIEW_PROFILE.max_rounds == CODE_REVIEW_ROUND_LIMIT == 200
    assert CODE_REVIEW_PROFILE.max_rounds > SUBAGENT_ROUND_LIMIT


def test_review_candidate_has_only_checkout_and_read_tools() -> None:
    assert CODE_REVIEW_PROFILE.name == "code_review"
    assert CODE_REVIEW_PROFILE.input_model is ExactComparison
    assert CODE_REVIEW_PROFILE.tool_names == (
        "checkout_code_review",
        "review_read",
        "review_glob",
        "review_grep",
    )
    assert CODE_REVIEW_PROFILE.untrusted_output is True
    assert CODE_REVIEW_PROFILE.isolated_tools is True
    assert {
        "bash",
        "write",
        "edit",
        "ask_user",
        "request_user_input",
        "connect_github",
        "call_external_tool",
        "describe_external_tools",
        "search_web",
        "fetch_url",
    }.isdisjoint(CODE_REVIEW_PROFILE.tool_names)
    assert CODE_REVIEW_TOOL.profile_only is True
    assert CODE_REVIEW_TOOL.untrusted is True
    assert CODE_REVIEW_TOOL.side_effecting is True
    assert CODE_REVIEW_PROFILE.output_model.model_validate(
        {
            "findings": [
                {
                    "path": "core/review.py",
                    "line": 17,
                    "title": "Publishes the wrong commit",
                    "trigger": "Publish a completed review after the pull request head changes.",
                    "failure": "The check is attached to a commit the reviewer did not inspect.",
                    "impact": "materially incorrect result or state for a supported workflow",
                }
            ]
        }
    )
    with pytest.raises(ValueError):
        CODE_REVIEW_PROFILE.output_model.model_validate(
            {
                "findings": [
                    {
                        "path": "file.py",
                        "line": 1,
                        "severity": "P1",
                        "title": "Add a docstring",
                        "body": "This public method has no docstring.",
                    }
                ],
            }
        )


def test_review_prompt_admits_only_severe_merge_blocking_defects() -> None:
    prompt = CODE_REVIEW_PROFILE.prompt

    assert "concrete, reachable defects introduced by the pull request" in prompt
    assert (
        "materially harms a supported workflow, result, state, security, or availability" in prompt
    )
    assert "`security or workspace-boundary breach`" in prompt
    assert "`data loss, corruption, or wrong-target mutation`" in prompt
    assert "`production outage, deadlock, or permanently unfinished work`" in prompt
    assert "`a supported operation fails or cannot complete for valid input`" in prompt
    assert "`materially incorrect result or state for a supported workflow`" in prompt
    assert "`substantial availability, reliability, or performance regression`" in prompt
    assert "`the feature cannot function in its supported production configuration`" in prompt
    assert "`the code fails to build or breaks required CI`" in prompt
    assert "Style, naming, readability, and documentation nits." in prompt
    assert "Missing tests when no actual defect is demonstrated." in prompt
    assert "Return an empty findings list when there is no severe defect." in prompt
    assert "critical defect" not in prompt
    assert "P0" not in prompt
    assert "P1" not in prompt
    assert "P2" not in prompt
    assert "P3" not in prompt


def test_review_prompt_requires_all_qualifying_findings() -> None:
    prompt = CODE_REVIEW_PROFILE.prompt

    assert "Make an internal coverage list of every changed file and hunk." in prompt
    assert "inspect the containing function and the supported workflow" in prompt
    assert "Do not inspect unrelated unchanged code until you assess every changed hunk." in prompt
    assert "Finding one severe defect is not a stopping condition." in prompt
    assert "review the remaining changed workflows as if you found none" in prompt
    assert "Do not return until you assess every entry in the coverage list." in prompt
    assert "Return every qualifying finding that you establish." in prompt
    assert "Do not lower the finding bar to increase the count." in prompt


def test_review_prompt_writes_findings_in_simplified_technical_english() -> None:
    prompt = CODE_REVIEW_PROFILE.prompt

    assert "ASD-STE100 Simplified Technical English" in prompt
    assert "Write `title`, `trigger`, and `failure`" in prompt
    assert "one statement per sentence, active voice, present tense" in prompt
    assert "Reproduce paths, identifiers, and quoted diff lines exactly" in prompt


async def test_review_grep_truncates_a_match_wider_than_the_pipe(tmp_path: Path) -> None:
    remote, _, base, head = await _comparison(tmp_path)
    turn_id = uuid4()
    await ExactComparisonCheckout(
        sandbox := await _sandbox(tmp_path),
        str(remote),
        f"/workspace/.ufo-review/{turn_id.hex}",
    ).run(
        ExactComparison(
            repository="metalcraftai/ufo",
            pull_number=PULL_NUMBER,
            base_sha=base,
            head_sha=head,
        )
    )
    context = cast(ToolContext, SimpleNamespace(sandbox=sandbox, turn=SimpleNamespace(id=turn_id)))
    grepped = await review_grep(
        context,
        ReviewGrepInput(pattern="WIDE", head_limit=2, user_description="Searching wide output."),
    )
    assert "[more matches omitted]" in grepped.content[0].text
    assert grepped.content[0].text.count("wide.txt") == 2


async def test_review_glob_refuses_when_the_checkout_is_absent(tmp_path: Path) -> None:
    context = cast(
        ToolContext,
        SimpleNamespace(sandbox=await _sandbox(tmp_path), turn=SimpleNamespace(id=uuid4())),
    )
    with pytest.raises(RuntimeError, match="git could not list review files") as failed:
        await review_glob(
            context,
            ReviewGlobInput(pattern="*.txt", user_description="Listing text files."),
        )
    assert "ls-files exited" in str(failed.value)


async def test_review_file_tools_are_text_only_and_bound_to_this_turn(
    tmp_path: Path,
) -> None:
    remote, _, base, head = await _comparison(tmp_path)
    sandbox = await _sandbox(tmp_path)
    turn_id = uuid4()
    directory = f"/workspace/.ufo-review/{turn_id.hex}"
    checkout = await ExactComparisonCheckout(sandbox, str(remote), directory).run(
        ExactComparison(
            repository="metalcraftai/ufo",
            pull_number=PULL_NUMBER,
            base_sha=base,
            head_sha=head,
        )
    )
    context = cast(
        ToolContext,
        SimpleNamespace(sandbox=sandbox, turn=SimpleNamespace(id=turn_id)),
    )
    read = await review_read(
        context,
        ReviewReadInput(file_path="changed.txt", user_description="Reading changed text."),
    )
    assert "after" in read.content[0].text
    with pytest.raises(ValueError, match="text files only"):
        await review_read(
            context,
            ReviewReadInput(file_path="asset.bin", user_description="Reading binary content."),
        )
    with pytest.raises(ValueError, match="outside the verified checkout"):
        await review_read(
            context,
            ReviewReadInput(
                file_path="/workspace/.ufo-review/another/checkout/changed.txt",
                user_description="Reading another checkout.",
            ),
        )
    globbed = await review_glob(
        context,
        ReviewGlobInput(pattern="*.txt", user_description="Listing text files."),
    )
    assert "added.txt" in globbed.content[0].text
    grepped = await review_grep(
        context,
        ReviewGrepInput(pattern="after", user_description="Searching changed text."),
    )
    assert "changed.txt:1:after" in grepped.content[0].text
    bounded = await review_grep(
        context,
        ReviewGrepInput(
            pattern="TARGET", head_limit=1, user_description="Searching bounded output."
        ),
    )
    assert len(bounded.content[0].text) < 2_100
    assert "[more matches omitted]" in bounded.content[0].text
    assert checkout.diff_path == f"{directory}/comparison.diff"


def test_review_checkout_has_no_api_material_route_or_question_path() -> None:
    source = Path(review_checkout.__file__).read_text() + CODE_REVIEW_PROFILE.prompt
    assert all(
        value not in source
        for value in (
            "api.github.com",
            "call_external_tool",
            "describe_external_tools",
            "ask_user",
            "request_user_input",
        )
    )


@pytest.mark.parametrize(
    "values",
    (
        {"repository": "../ufo", "pull_number": 1, "base_sha": "0" * 40, "head_sha": "1" * 40},
        {
            "repository": "metalcraftai/ufo",
            "pull_number": 0,
            "base_sha": "0" * 40,
            "head_sha": "1" * 40,
        },
        {
            "repository": "metalcraftai/ufo",
            "pull_number": 1,
            "base_sha": "short",
            "head_sha": "1" * 40,
        },
    ),
)
def test_exact_comparison_refuses_malformed_identity(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ExactComparison.model_validate(values)


async def test_checkout_tool_refuses_another_profile_and_substituted_identity() -> None:
    comparison = ExactComparison(
        repository="metalcraftai/ufo",
        pull_number=17,
        base_sha="0" * 40,
        head_sha="1" * 40,
    )
    args = CheckoutCodeReviewInput(
        **comparison.model_dump(), user_description="Preparing the review."
    )
    context = cast(
        ToolContext,
        SimpleNamespace(
            turn=SimpleNamespace(subagent_profile="coding", inbound=comparison.model_dump_json())
        ),
    )
    with pytest.raises(ValueError, match="only to the code review candidate"):
        await checkout_code_review(context, args)

    context.turn.subagent_profile = "code_review"
    context.turn.inbound = comparison.model_copy(update={"head_sha": "2" * 40}).model_dump_json()
    with pytest.raises(ValueError, match="does not match the admitted comparison"):
        await checkout_code_review(context, args)
