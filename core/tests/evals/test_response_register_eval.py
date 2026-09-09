from pathlib import Path

from evals.harness.capability import CapabilityOutput, SharedArtifact, ToolInvocation
from evals.suites.response_register import (
    CASES,
    addressed_reply_scorer,
    carried_report_scorer,
    conversational_scorer,
    shared_report_scorer,
)

OBSERVED_RESPONSE = (
    'GitHub access is still broken on my side: two connections completed as "marshall-ufo" but '
    "no tool in this session can see the account, and I have reported that fault to the team. So "
    "you can apply it now, I wrote the full change to "
    "[glm-provider-routing.md](/workspace/glm-provider-routing.md): the env var value for both "
    "glm-5.3 and glm-5.3-flash, the client-side parse that injects the `provider` object, and the "
    "two checks to run after. Ping me once the connection issue is fixed and I will open the PR "
    "myself."
)
PLAIN_RESPONSE = (
    "GitHub access is still broken on my side. Set the environment value for both models, parse "
    "the provider object on the client, and run the two checks before opening the PR."
)
CARRIED_REPORT = (
    b"# Provider routing\n\nEnvironment value and client parsing.\n\n## Tests\n\nRun both checks.\n"
)


def _carried_output(response: str) -> CapabilityOutput:
    return CapabilityOutput(
        response=response,
        calls=(),
        artifacts=(SharedArtifact("glm-provider-routing.md", CARRIED_REPORT, "details"),),
    )


async def test_carried_report_rejects_the_observed_workspace_link() -> None:
    grader = carried_report_scorer(1, 100, 5, 1, 2)

    verdict = await grader(_carried_output(OBSERVED_RESPONSE))

    assert not verdict.passed
    assert verdict.evidence["unreachableMarkdownTargets"] == ["/workspace/glm-provider-routing.md"]
    assert verdict.evidence["workspacePaths"] == ["/workspace/glm-provider-routing.md"]


async def test_carried_report_accepts_a_plain_summary_beside_its_artifact() -> None:
    grader = carried_report_scorer(1, 100, 5, 1, 2, expected_name="glm-provider-routing.md")

    verdict = await grader(_carried_output(PLAIN_RESPONSE))

    assert verdict.passed, verdict.reason
    assert verdict.evidence["carriedArtifacts"] == 1


async def test_carried_report_fails_a_reply_that_sent_a_file_or_carried_none() -> None:
    grader = carried_report_scorer(1, 100, 5, 1, 2)

    bare = await grader(CapabilityOutput(response=PLAIN_RESPONSE, calls=()))
    sent = await grader(
        CapabilityOutput(
            response=PLAIN_RESPONSE,
            calls=(),
            artifacts=(SharedArtifact("glm-provider-routing.md", CARRIED_REPORT, "file"),),
        )
    )

    assert not bare.passed and "carried 0 artifacts" in bare.reason
    assert not sent.passed and "shared 1 files" in sent.reason


async def test_chat_reply_accepts_an_https_markdown_link() -> None:
    grader = conversational_scorer(20, 2)

    verdict = await grader(
        CapabilityOutput("Read [pull request 144](https://github.com/example/repo/pull/144).", ())
    )

    assert verdict.passed, verdict.reason


async def test_chat_reply_rejects_relative_and_file_markdown_links() -> None:
    grader = conversational_scorer(20, 2)

    verdict = await grader(
        CapabilityOutput("Read [the plan](./plan.md) or [the copy](file:///workspace/plan.md).", ())
    )

    assert not verdict.passed
    assert verdict.evidence["unreachableMarkdownTargets"] == [
        "./plan.md",
        "file:///workspace/plan.md",
    ]


async def test_chat_reply_rejects_a_plain_workspace_path() -> None:
    grader = conversational_scorer(20, 2)

    verdict = await grader(CapabilityOutput("The report is at /workspace/plan.md.", ()))

    assert not verdict.passed
    assert verdict.evidence["workspacePaths"] == ["/workspace/plan.md"]


async def test_chat_reply_ignores_a_workspace_link_inside_code() -> None:
    grader = conversational_scorer(20, 4)

    verdict = await grader(
        CapabilityOutput("Example:\n```markdown\n[plan](/workspace/plan.md)\n```", ())
    )

    assert verdict.passed, verdict.reason


async def test_shared_report_rejects_a_workspace_link(tmp_path: Path) -> None:
    report = b"# Provider routing\n\nEnvironment value.\n\n## Tests\n\nRun both checks."
    (tmp_path / "glm-provider-routing.md").write_bytes(report)
    grader = shared_report_scorer(200, 5, 1, 2)
    output = CapabilityOutput(
        "Sent [glm-provider-routing.md](/workspace/glm-provider-routing.md).",
        (
            ToolInvocation(
                name="share_file",
                input={},
                result='[{"name":"glm-provider-routing.md"}]',
                has_result=True,
            ),
        ),
        artifacts=(SharedArtifact("glm-provider-routing.md", report),),
        workspace_dir=tmp_path,
    )

    verdict = await grader(output)

    assert not verdict.passed
    assert verdict.evidence["unreachableMarkdownTargets"] == ["/workspace/glm-provider-routing.md"]


async def test_shared_report_accepts_the_exact_shared_artifact_url(tmp_path: Path) -> None:
    report = b"# Provider routing\n\nEnvironment value.\n\n## Tests\n\nRun both checks."
    (tmp_path / "glm-provider-routing.md").write_bytes(report)
    artifact_url = "/artifacts/report/glm-provider-routing.md?signature=test"
    grader = shared_report_scorer(200, 5, 1, 2)
    output = CapabilityOutput(
        f"Sent [glm-provider-routing.md]({artifact_url}).",
        (
            ToolInvocation(
                name="share_file",
                input={},
                result=(f'[{{"name":"glm-provider-routing.md","url":"{artifact_url}"}}]'),
                has_result=True,
            ),
        ),
        artifacts=(SharedArtifact("glm-provider-routing.md", report),),
        workspace_dir=tmp_path,
    )

    verdict = await grader(output)

    assert verdict.passed, verdict.reason


async def test_addressed_reply_rejects_a_deferral_to_the_mentioned_people() -> None:
    grader = addressed_reply_scorer(8, 55, 4)

    verdict = await grader(CapabilityOutput("That one is for Dana and Priya.", ()))

    assert not verdict.passed
    assert "under the 8 floor" in verdict.reason


async def test_addressed_reply_accepts_an_answer_to_the_member() -> None:
    grader = addressed_reply_scorer(8, 55, 4)

    verdict = await grader(
        CapabilityOutput(
            "Yes. VACUUM FULL takes an ACCESS EXCLUSIVE lock on the table, so reads and writes "
            "both block until it finishes.",
            (),
        )
    )

    assert verdict.passed, verdict.reason


def test_mention_case_carries_mentions_in_the_thread_and_the_message() -> None:
    case = next(case for case in CASES if case.name == "fact-question-mentions-other-people")

    assert "@dana" in case.message and "@priya" in case.message
    assert any("@dana" in prior and "@priya" in prior for prior in case.prior_messages)


def test_workspace_link_regression_runs_each_attempt() -> None:
    names = {
        "workspace-report-followup-stays-undelivered",
        "report-workspace-link-opens-detail",
    }
    cases = [case for case in CASES if case.name in names]

    assert len(cases) == 2
    assert all(case.samples == 1 for case in cases)
    assert all(
        "glm-provider-routing.md" in (case.message + "".join(case.prior_messages)) for case in cases
    )
