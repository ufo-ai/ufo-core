import asyncio
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
from ufo.sandbox.session import SANDBOX_GID, SANDBOX_UID

pytestmark = pytest.mark.docker


async def _docker(*argv: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "docker",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout.decode(), stderr.decode()


@pytest.mark.parametrize("fixture_index", range(len(FIXTURES)))
async def test_real_followup_probe_captures_initial_evidence_artifact(
    tmp_path: Path, sandbox_image: str, fixture_index: int
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    conversation_id = uuid4()
    workspace = workspace_root / str(conversation_id)
    app = workspace / "ufo-app"
    app.mkdir(parents=True)
    fixture = FIXTURES[fixture_index]
    (app / "index.html").write_bytes(APPLICATION_INDEX)
    (app / "preview.html").write_bytes(APPLICATION_PREVIEW_SCAFFOLD)
    (app / "app.tsx").write_bytes(fixture.source)
    (app / "preview.svg").write_bytes(fixture.preview)
    (app / "vite.config.ts").write_bytes(PROJECT_CONFIG_BYTES)
    await _prepare_kit(UUID(int=0), workspace)
    ownership = await _docker(
        "run",
        "--rm",
        "--entrypoint",
        "chown",
        "--user",
        "0:0",
        "-v",
        f"{workspace}:/workspace",
        sandbox_image,
        "-R",
        "1001:1001",
        "/workspace",
    )
    assert ownership[0] == 0, ownership[2]

    class ProbeDriver:
        def workspace_path(self, identifier: UUID, rel: str) -> Path:
            return workspace_root / str(identifier) / rel

    driver = cast(
        WorkspaceDriver,
        ProbeDriver(),
    )
    output = CapabilityOutput(response="READY", calls=(), workspace_dir=workspace)

    probe = AppBenchWorkspaceProbe(conversation_id, driver, sandbox_image)
    captured = await AppQaReplayProbe(fixture, "initial")(output, probe)

    assert captured.error == ""
    evidence = tuple(
        artifact
        for artifact in captured.artifacts
        if artifact.name == f"{fixture.name}-initial-evidence.json"
    )
    assert len(evidence) == 1
    replay = ReplayEvidence.model_validate_json(evidence[0].content)
    assert replay.phase == "initial"
    assert replay.feedback_sha256 == fixture.expected.sha256
    owner = await _docker(
        "run",
        "--rm",
        "--entrypoint",
        "stat",
        "-v",
        f"{workspace}:/workspace",
        sandbox_image,
        "-c",
        "%u:%g",
        "/workspace",
    )
    assert owner[0] == 0, owner[2]
    assert owner[1].strip() == f"{SANDBOX_UID}:{SANDBOX_GID}"
    editable = await _docker(
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        "-v",
        f"{workspace}:/workspace",
        sandbox_image,
        "-c",
        "printf '\\n' >> /workspace/ufo-app/app.tsx",
    )
    assert editable[0] == 0, editable[2]

    final = await AppQaReplayProbe(fixture, "final")(output, probe)
    assert final.error == ""
    assert {
        artifact.name for artifact in final.artifacts if artifact.name.endswith("-evidence.json")
    } >= {
        f"{fixture.name}-initial-evidence.json",
        f"{fixture.name}-final-evidence.json",
    }

    failed = await AppQaReplayProbe(fixture, "initial")(
        output,
        AppBenchWorkspaceProbe(uuid4()),
    )
    failed_output = replace(output, artifact_error=failed.error)
    followup = await _repair_followup(fixture)(failed_output)
    verdict = await CASES[fixture_index].grader(failed_output)

    assert "No such container" in failed.error
    assert len(failed.error) <= len("app QA replay probe failed: ") + PROBE_ERROR_DETAIL_CHARS
    assert followup is None
    assert not verdict.passed
    assert failed.error in verdict.reason
