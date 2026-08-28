import json
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from ufo_ext_eval_env.manifest import (
    APP_QA_EDIT_CALL_LIMIT,
    APP_QA_EDIT_NEW_BYTES_LIMIT,
    APP_QA_EDIT_OLD_BYTES_LIMIT,
)
from ufo_ext_sites.application_audit import ApplicationAuditInteraction, ApplicationAuditReport
from ufo_ext_sites.application_builder import APPLICATION_BUILD_TIMEOUT_SECONDS
from ufo_ext_sites.tools import APPLICATION_AUDIT_TIMEOUT_SECONDS

import evals.suites.ufo_app_qa_replay as replay
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    ProbeCommandResult,
    SharedArtifact,
    ToolInvocation,
    linked_artifacts,
    run_capability_case,
)
from evals.harness.harness import EvalMetric, EvalReport
from evals.harness.target import TargetResult
from evals.registry import TASKS
from evals.suites.app_audit_probe import app_audit_command
from evals.suites.ufo_app_qa_replay import (
    CASES,
    FIXTURE_ROOT,
    FIXTURES,
    SOURCE_PATH,
    AppQaReplayProbe,
    _case,
    _repair_followup,
    issue_fingerprint,
)

EXPECTED = {
    "pre-meeting-briefs": {
        "source": "5e9a1d31c455dd02cb386a42f4cdc7f30954eab1f46c6d66a32efcf4d6041d90",
        "preview": "1c188cfdb40102ef0bbc46d2067117679743a438aef8504ec1a74905ff52e9b6",
        "contract": "815aad6b3053593c800cf0f9a207f65be1a741bbde87d0a02cf3ffd166f4c0eb",
        "repair": (1, 2_854, 3_287),
        "codes": ("contrast", "clipping", "above_fold"),
    },
    "issue-owner": {
        "source": "81416464d8211dd0e360d7f3efb84653ff6d45c507d657cc13a7fc89430050a4",
        "preview": "0bab22bbfb70654f4d3f0c14fbec7698ee876dfc3fb774b9554ec7edf895defc",
        "contract": "fe725407d047d1d755ccbaa858018c60e55b6138579445ac823917b1ae99d595",
        "repair": (1, 2_357, 2_008),
        "codes": ("contrast",),
    },
}


def _passing_output(
    fixture: replay.ReplayFixture,
    calls: tuple[ToolInvocation, ...],
) -> CapabilityOutput:
    initial = {
        "phase": "initial",
        "sourceSha256": fixture.provenance.source_sha256,
        "feedbackSha256": fixture.expected.sha256,
        "issues": [issue.model_dump(mode="json") for issue in fixture.expected.issues],
        "compileMs": 2,
        "auditMs": 3,
        "agentMs": 1,
        "agentResponse": "READY",
        "agentCalls": 0,
    }
    final = {
        "phase": "final",
        "sourceSha256": "1" * 64,
        "feedbackSha256": issue_fingerprint(()),
        "issues": [],
        "compileMs": 2,
        "auditMs": 3,
        "agentMs": 4,
        "agentResponse": "Fixed.",
        "agentCalls": len(calls),
    }
    return CapabilityOutput(
        response="Fixed.",
        calls=calls,
        artifacts=(
            SharedArtifact(f"{fixture.name}-initial-evidence.json", json.dumps(initial).encode()),
            SharedArtifact(f"{fixture.name}-final-evidence.json", json.dumps(final).encode()),
        ),
    )


def test_replay_fixtures_keep_exact_source_preview_contract_and_feedback() -> None:
    for fixture in FIXTURES:
        expected = EXPECTED[fixture.name]
        assert sha256(fixture.source).hexdigest() == expected["source"]
        assert sha256(fixture.preview).hexdigest() == expected["preview"]
        assert len(fixture.source) > APP_QA_EDIT_OLD_BYTES_LIMIT
        assert len(fixture.source) > APP_QA_EDIT_NEW_BYTES_LIMIT
        repair_calls, repair_old_bytes, repair_new_bytes = expected["repair"]
        assert repair_calls <= APP_QA_EDIT_CALL_LIMIT
        assert repair_old_bytes <= APP_QA_EDIT_OLD_BYTES_LIMIT
        assert repair_new_bytes <= APP_QA_EDIT_NEW_BYTES_LIMIT
        assert fixture.provenance.contract_sha256 == expected["contract"]
        assert (
            sha256((FIXTURE_ROOT / fixture.name / "contract.json").read_bytes()).hexdigest()
            == expected["contract"]
        )
        assert tuple(issue.code for issue in fixture.expected.issues) == expected["codes"]
        assert issue_fingerprint(fixture.expected.issues) == fixture.expected.sha256
        assert fixture.contract.facts


def test_replay_task_runs_the_direct_repair_agent_without_a_judge() -> None:
    task = next(task for task in TASKS if task.name == "ufo-app-qa-replay")

    assert task.agent == "app-qa-repair"
    assert task.judge_model is None
    assert task.exclusive is True
    assert task.cases == ("pre-meeting-briefs", "issue-owner")


def test_replay_probe_compiles_and_audits_without_agent_build_or_deploy_tools() -> None:
    command = app_audit_command(
        name="case-initial",
        output_dir="/workspace/.eval-output/case/initial",
        project="/workspace/ufo-app",
        design_path="/workspace/ufo-app/preview.svg",
        compile_source=True,
    )

    assert "vite build" in command
    assert "audit_application" not in command
    assert "ufo-app-bench-audit.cjs" in command
    assert "/workspace/ufo-app/preview.svg" in command
    assert "--design" in command
    assert "design_sha256" in command
    assert "case-initial-design-evidence.json" in command
    assert "deploy" not in command
    assert "node /tmp/ufo-app-bench-audit.cjs /workspace/ufo-app" in command
    assert "ufo-app-bench-server" not in command
    assert replay.PROBE_TIMEOUT_SECONDS == (
        APPLICATION_BUILD_TIMEOUT_SECONDS + APPLICATION_AUDIT_TIMEOUT_SECONDS
    )


def test_contract_bytes_are_validated_and_move_case_identity(tmp_path, monkeypatch) -> None:
    fixture = FIXTURES[0]
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURE_ROOT, copied)
    contract_path = copied / fixture.name / "contract.json"
    contract_path.write_bytes(contract_path.read_bytes() + b"\n")
    monkeypatch.setattr(replay, "FIXTURE_ROOT", copied)

    with pytest.raises(ValueError, match="contract SHA-256"):
        replay.ReplayFixture.load(fixture.name)

    changed_contract = fixture.contract.model_copy(
        update={
            "facts": (
                fixture.contract.facts[0].model_copy(update={"label": "changed contract fact"}),
                *fixture.contract.facts[1:],
            )
        }
    )
    changed = replace(
        fixture,
        contract=changed_contract,
        provenance=fixture.provenance.model_copy(
            update={
                "contract_sha256": sha256(
                    changed_contract.model_dump_json(by_alias=True).encode()
                ).hexdigest()
            }
        ),
    )
    assert fixture.provenance.contract_sha256 in CASES[0].payload()["grader"]
    assert _case(changed).payload()["grader"] != CASES[0].payload()["grader"]


async def test_followup_refuses_a_moved_initial_feedback_fingerprint() -> None:
    fixture = FIXTURES[0]
    evidence = {
        "phase": "initial",
        "sourceSha256": fixture.provenance.source_sha256,
        "feedbackSha256": "0" * 64,
        "issues": [issue.model_dump(mode="json") for issue in fixture.expected.issues],
        "compileMs": 1,
        "auditMs": 1,
        "agentMs": 1,
        "agentResponse": "READY",
        "agentCalls": 0,
    }
    output = CapabilityOutput(
        response="READY",
        calls=(),
        artifacts=(
            SharedArtifact(f"{fixture.name}-initial-evidence.json", json.dumps(evidence).encode()),
        ),
    )

    with pytest.raises(RuntimeError, match="initial feedback moved"):
        await _repair_followup(fixture)(output)


async def test_final_probe_is_a_self_contained_artifact_set(tmp_path: Path) -> None:
    fixture = FIXTURES[0]
    app = tmp_path / "ufo-app"
    app.mkdir()
    (app / "app.tsx").write_bytes(fixture.source)
    report = ApplicationAuditReport(
        views=(),
        interaction=ApplicationAuditInteraction(controls=(), successes=(), states=(), console=()),
    ).model_dump_json(by_alias=True)

    class Probe:
        async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
            phase = "final" if "/final" in command else "initial"
            name = f"{fixture.name}-{phase}"
            directory = tmp_path / ".eval-output" / "ufo-app-qa-replay" / fixture.name / phase
            directory.mkdir(parents=True)
            for suffix in ("design.html", "interactive.html", "static.html"):
                (directory / f"{name}-{suffix}").write_text(suffix)
            (directory / f"{name}-audit.json").write_text(report)
            for scheme in replay.SCHEMES:
                (directory / f"{name}-{scheme}.png").write_bytes(b"png")
            (directory / f"{name}-design.svg").write_text("<svg/>")
            (directory / f"{name}-design-evidence.json").write_text("{}")
            (directory / "timing.json").write_text('{"compileMs":1,"auditMs":2}')
            return ProbeCommandResult(0, "", "")

    output = CapabilityOutput("READY", (), workspace_dir=tmp_path)
    initial = await AppQaReplayProbe(fixture, "initial")(output, Probe())
    final = await AppQaReplayProbe(fixture, "final")(output, Probe())

    assert not initial.error
    assert not final.error
    names = {artifact.name for artifact in final.artifacts}
    assert sum(len(artifact.content) for artifact in final.artifacts) <= final.max_payload_bytes
    assert {item["name"] for item in linked_artifacts(final.artifacts, ())} == names
    assert f"{fixture.name}-initial-evidence.json" in names
    assert f"{fixture.name}-final-evidence.json" in names
    assert f"{fixture.name}-final-audit.json" in names
    assert f"{fixture.name}-final-interactive.html" in names
    assert f"{fixture.name}-final-light.png" in names
    assert not any(
        name.startswith(f"{fixture.name}-initial-")
        for name in names
        if name != f"{fixture.name}-initial-evidence.json"
    )


async def test_failed_initial_probe_persists_its_bounded_root_error() -> None:
    fixture = FIXTURES[0]
    root_error = "No such container: ufo-sbx-case"
    output = CapabilityOutput(
        response="READY",
        calls=(),
        artifact_error=f"app QA replay probe failed: {root_error}" + "x" * 1_000,
    )

    followup = await _repair_followup(fixture)(output)
    verdict = await CASES[0].grader(output)

    assert followup is None
    assert not verdict.passed
    assert root_error in verdict.reason
    assert verdict.reason == (
        "replay captured 0 initial evidence artifacts: " + output.artifact_error[:500]
    )

    class FailedProbeTarget:
        judge = None

        async def run(self, _case: CapabilityCase) -> TargetResult:
            return TargetResult(output=output, clean=True)

    result = await run_capability_case(CASES[0], FailedProbeTarget())  # type: ignore[arg-type]
    attempts = result.evidence["attempts"]
    assert isinstance(attempts, list)
    attempt = attempts[0]
    assert isinstance(attempt, dict)
    assert attempt["artifactError"] == output.artifact_error
    assert attempt["reason"] == verdict.reason


async def test_followup_probe_requires_a_followup() -> None:
    async def grade(_output: CapabilityOutput) -> CapabilityVerdict:
        return CapabilityVerdict(True, "passed")

    with pytest.raises(ValueError, match="requires a followup"):
        CapabilityCase(
            name="bad",
            message="bad",
            grader=grade,
            followup_artifact_probe=AppQaReplayProbe(FIXTURES[0], "final"),
        )


async def test_replay_grader_accepts_only_a_changed_source_with_a_clean_final_audit() -> None:
    fixture = FIXTURES[0]
    initial = {
        "phase": "initial",
        "sourceSha256": fixture.provenance.source_sha256,
        "feedbackSha256": fixture.expected.sha256,
        "issues": [issue.model_dump(mode="json") for issue in fixture.expected.issues],
        "compileMs": 2,
        "auditMs": 3,
        "agentMs": 1,
        "agentResponse": "READY",
        "agentCalls": 0,
    }
    final = {
        "phase": "final",
        "sourceSha256": "1" * 64,
        "feedbackSha256": issue_fingerprint(()),
        "issues": [],
        "compileMs": 2,
        "auditMs": 3,
        "agentMs": 4,
        "agentResponse": "Fixed.",
        "agentCalls": 2,
    }
    output = CapabilityOutput(
        response="Fixed.",
        calls=(
            ToolInvocation("read", {"file_path": SOURCE_PATH}, has_result=True),
            ToolInvocation(
                "edit",
                {
                    "file_path": SOURCE_PATH,
                    "edits": [{"old_string": "#aaa", "new_string": "#111"}],
                },
                has_result=True,
            ),
        ),
        artifacts=(
            SharedArtifact(f"{fixture.name}-initial-evidence.json", json.dumps(initial).encode()),
            SharedArtifact(f"{fixture.name}-final-evidence.json", json.dumps(final).encode()),
        ),
    )

    verdict = await CASES[0].grader(output)

    assert verdict.passed
    assert verdict.evidence["changedPaths"] == [SOURCE_PATH]
    assert verdict.evidence["contrastPassed"] is True


async def test_complete_replay_record_serializes_with_only_normalized_metrics() -> None:
    fixture = FIXTURES[0]
    calls = (
        ToolInvocation("read", {"file_path": SOURCE_PATH}, has_result=True),
        ToolInvocation(
            "edit",
            {
                "file_path": SOURCE_PATH,
                "edits": [{"old_string": "#aaa", "new_string": "#111"}],
            },
            has_result=True,
        ),
    )
    output = replace(_passing_output(fixture, calls), tokens=17, cost_micro_usd=23)

    class CompleteReplayTarget:
        judge = None

        async def run(self, _case: CapabilityCase) -> TargetResult:
            return TargetResult(output=output, clean=True)

    case = replace(CASES[0], followup=None, followup_artifact_probe=None)
    record = await run_capability_case(case, CompleteReplayTarget())  # type: ignore[arg-type]

    class ProbeErrorTarget:
        judge = None

        async def run(self, _case: CapabilityCase) -> TargetResult:
            return TargetResult(
                output=CapabilityOutput(
                    response="",
                    calls=(),
                    artifact_error="app QA replay probe failed: retained probe error",
                ),
                clean=True,
            )

    error_case = replace(CASES[1], followup=None, followup_artifact_probe=None)
    error_record = await run_capability_case(error_case, ProbeErrorTarget())  # type: ignore[arg-type]
    report = replay._metrics(
        EvalReport(
            name="complete-replay",
            suite="ufo-app-qa-replay",
            digest="sha256:test",
            cases=(record, error_record),
        )
    )
    payload = json.loads(report.model_dump_json())

    assert record.passed
    assert {metric.name for metric in report.metrics} == {
        "hard_pass_rate",
        "compile_pass_rate",
        "interaction_pass_rate",
        "facts_pass_rate",
        "above_fold_pass_rate",
        "design_pass_rate",
        "contrast_pass_rate",
    }
    assert all(metric.value == 0.5 for metric in report.metrics)
    assert all(EvalMetric.model_validate(metric.model_dump()) for metric in report.metrics)
    attempt = payload["cases"][0]["evidence"]["attempts"][0]
    error_attempt = payload["cases"][1]["evidence"]["attempts"][0]
    assert attempt["tokens"] == 17
    assert attempt["costMicroUsd"] == 23
    assert attempt["grader"]["initialAuditMs"] == 3
    assert attempt["grader"]["repairMs"] == 4
    assert attempt["grader"]["oldStringBytes"] == 4
    assert attempt["grader"]["forbiddenCalls"] == []
    assert len(attempt["artifacts"]) == 2
    assert error_record.excluded is False
    assert error_record.passed is False
    assert error_attempt["grader"] is None
    assert "retained probe error" in error_attempt["artifactError"]


async def test_replay_grader_enforces_exact_edit_budget_boundaries() -> None:
    fixture = FIXTURES[0]
    old_chunk = APP_QA_EDIT_OLD_BYTES_LIMIT // APP_QA_EDIT_CALL_LIMIT
    new_chunk = APP_QA_EDIT_NEW_BYTES_LIMIT // APP_QA_EDIT_CALL_LIMIT
    calls = [ToolInvocation("read", {"file_path": SOURCE_PATH}, has_result=True)]
    for index in range(APP_QA_EDIT_CALL_LIMIT):
        old_size = old_chunk + (index < APP_QA_EDIT_OLD_BYTES_LIMIT % APP_QA_EDIT_CALL_LIMIT)
        new_size = new_chunk + (index < APP_QA_EDIT_NEW_BYTES_LIMIT % APP_QA_EDIT_CALL_LIMIT)
        calls.append(
            ToolInvocation(
                "edit",
                {
                    "file_path": SOURCE_PATH,
                    "edits": [
                        {
                            "old_string": chr(65 + index) * old_size,
                            "new_string": chr(97 + index) * new_size,
                        }
                    ],
                },
                has_result=True,
            )
        )
    output = _passing_output(fixture, tuple(calls))

    boundary = await CASES[0].grader(output)
    over_calls = await CASES[0].grader(
        replace(
            output,
            calls=(
                *output.calls,
                ToolInvocation(
                    "edit",
                    {
                        "file_path": SOURCE_PATH,
                        "edits": [{"old_string": "extra", "new_string": "extra"}],
                    },
                    has_result=True,
                ),
            ),
        )
    )
    over_old = await CASES[0].grader(
        _passing_output(
            fixture,
            (
                output.calls[0],
                ToolInvocation(
                    "edit",
                    {
                        "file_path": SOURCE_PATH,
                        "edits": [
                            {
                                "old_string": "x" * (APP_QA_EDIT_OLD_BYTES_LIMIT + 1),
                                "new_string": "new",
                            }
                        ],
                    },
                    has_result=True,
                ),
            ),
        )
    )
    over_new = await CASES[0].grader(
        _passing_output(
            fixture,
            (
                output.calls[0],
                ToolInvocation(
                    "edit",
                    {
                        "file_path": SOURCE_PATH,
                        "edits": [
                            {
                                "old_string": "old",
                                "new_string": "x" * (APP_QA_EDIT_NEW_BYTES_LIMIT + 1),
                            }
                        ],
                    },
                    has_result=True,
                ),
            ),
        )
    )

    assert boundary.passed
    assert not over_calls.passed
    assert "edit call limit" in over_calls.reason
    assert not over_old.passed
    assert "old_string byte limit" in over_old.reason
    assert not over_new.passed
    assert "new_string byte limit" in over_new.reason


async def test_replay_grader_rejects_the_retained_47140_byte_full_source_edit() -> None:
    fixture = FIXTURES[0]
    assert len(fixture.source) == 47_140
    verdict = await CASES[0].grader(
        _passing_output(
            fixture,
            (
                ToolInvocation("read", {"file_path": SOURCE_PATH}, has_result=True),
                ToolInvocation(
                    "edit",
                    {
                        "file_path": SOURCE_PATH,
                        "edits": [
                            {
                                "old_string": fixture.source.decode(),
                                "new_string": "replacement",
                            }
                        ],
                    },
                    has_result=True,
                ),
            ),
        )
    )

    assert not verdict.passed
    assert "old_string byte limit" in verdict.reason
