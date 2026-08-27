from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from ufo_ext_sites.application_builder import APPLICATION_INDEX, APPLICATION_PREVIEW_SCAFFOLD
from ufo_ext_sites.source import PROJECT_CONFIG_BYTES

from evals.driver import WorkspaceDriver
from evals.harness.capability import CapabilityOutput
from evals.suites.ufo_app_bench import AppBenchWorkspaceProbe
from evals.suites.ufo_app_qa_replay import (
    CASES,
    FIXTURES,
    PROBE_ERROR_DETAIL_CHARS,
    AppQaReplayProbe,
    ReplayEvidence,
    _prepare_kit,
    _repair_followup,
)

pytestmark = pytest.mark.docker


async def test_real_followup_probe_captures_one_initial_evidence_artifact(
    tmp_path: Path, sandbox_image: str
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace_root.chmod(0o777)
    conversation_id = uuid4()
    workspace = workspace_root / str(conversation_id)
    app = workspace / "ufo-app"
    app.mkdir(parents=True)
    workspace.chmod(0o777)
    app.chmod(0o777)
    fixture = FIXTURES[1]
    (app / "index.html").write_bytes(APPLICATION_INDEX)
    (app / "preview.html").write_bytes(APPLICATION_PREVIEW_SCAFFOLD)
    (app / "app.tsx").write_bytes(fixture.source)
    (app / "preview.svg").write_bytes(fixture.preview)
    (app / "vite.config.ts").write_bytes(PROJECT_CONFIG_BYTES)
    await _prepare_kit(UUID(int=0), workspace)

    class ProbeDriver:
        def workspace_path(self, identifier: UUID, rel: str) -> Path:
            return workspace_root / str(identifier) / rel

    driver = cast(
        WorkspaceDriver,
        ProbeDriver(),
    )
    output = CapabilityOutput(response="READY", calls=(), workspace_dir=workspace)

    captured = await AppQaReplayProbe(fixture, "initial")(
        output,
        AppBenchWorkspaceProbe(conversation_id, driver, sandbox_image),
    )

    assert captured.error == ""
    evidence = tuple(
        artifact
        for artifact in captured.artifacts
        if artifact.name == "issue-owner-initial-evidence.json"
    )
    assert len(evidence) == 1
    replay = ReplayEvidence.model_validate_json(evidence[0].content)
    assert replay.phase == "initial"
    assert replay.feedback_sha256 == fixture.expected.sha256

    failed = await AppQaReplayProbe(fixture, "initial")(
        output,
        AppBenchWorkspaceProbe(uuid4()),
    )
    failed_output = replace(output, artifact_error=failed.error)
    followup = await _repair_followup(fixture)(failed_output)
    verdict = await CASES[1].grader(failed_output)

    assert "No such container" in failed.error
    assert len(failed.error) <= len("app QA replay probe failed: ") + PROBE_ERROR_DETAIL_CHARS
    assert followup is None
    assert not verdict.passed
    assert failed.error in verdict.reason
