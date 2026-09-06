import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from ufo_ext_sites.application_audit import validate_application_source
from ufo_ext_sites.application_homepage import APPLICATION_TEMPLATE_DIR
from ufo_ext_sites.source import PROJECT_CONFIG_BYTES, PROJECT_PREVIEW_BYTES

from evals.driver import WorkspaceDriver
from evals.harness.capability import CapabilityOutput
from evals.suites.ufo_app_bench import AppBenchWorkspaceProbe
from evals.suites.ufo_app_qa_replay import (
    CASES,
    FIXTURES,
    PROBE_ERROR_DETAIL_CHARS,
    AppQaReplayProbe,
    RepairTurns,
    ReplayEvidence,
    _prepare_kit,
    _repair_followup,
)
from ufo.harness.sandbox.session import SANDBOX_GID, SANDBOX_UID

APPLICATION_INDEX = (APPLICATION_TEMPLATE_DIR / "index.html").read_bytes()

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
    (app / "preview.html").write_bytes(PROJECT_PREVIEW_BYTES)
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

    turns = RepairTurns()
    probe = AppBenchWorkspaceProbe(conversation_id, driver, sandbox_image)
    captured = await AppQaReplayProbe(fixture, "initial", turns)(output, probe)

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

    turns.admit()
    final = await AppQaReplayProbe(fixture, "final", turns)(output, probe)
    assert final.error == ""
    assert {
        artifact.name for artifact in final.artifacts if artifact.name.endswith("-evidence.json")
    } >= {
        f"{fixture.name}-initial-evidence.json",
        f"{fixture.name}-final-evidence.json",
    }

    failed = await AppQaReplayProbe(fixture, "initial", turns)(
        output,
        AppBenchWorkspaceProbe(uuid4()),
    )
    failed_output = replace(output, artifact_error=failed.error)
    followup = await _repair_followup(fixture, RepairTurns())(failed_output)
    verdict = await CASES[fixture_index].grader(failed_output)

    assert "No such container" in failed.error
    assert len(failed.error) <= len("app QA replay probe failed: ") + PROBE_ERROR_DETAIL_CHARS
    assert followup is None
    assert not verdict.passed
    assert failed.error in verdict.reason


def _shipped_pages() -> tuple[tuple[str, dict[str, bytes]], ...]:
    """Every app page the deploy ships, with whatever is committed beside it. Read at collection
    so the test body touches no path of its own."""
    root = Path(__file__).parents[3]
    pages = []
    for source in sorted(root.glob("extensions/app_*/ufo_ext_app_*/skills/*/app.tsx")):
        beside = {
            item.name: item.read_bytes()
            for item in sorted(source.parent.iterdir())
            if item.is_file() and item.name not in {"index.html", "SKILL.md"}
        }
        pages.append((source.parent.name, beside))
    return tuple(pages)


SHIPPED_PAGES = _shipped_pages()


@pytest.mark.parametrize("name,beside", SHIPPED_PAGES, ids=[name for name, _ in SHIPPED_PAGES])
async def test_every_shipped_app_page_builds_the_way_its_deploy_builds_it(
    tmp_path: Path, sandbox_image: str, name: str, beside: dict[str, bytes]
) -> None:
    """The gate that hosts a member's page hosts these, and only a real build can say they still
    pass it. The source half had a test and the build half had none, so a page that no longer
    compiled against the deploy's own kit would have reached a member as a refusal.

    These carry no design, so the browser audit does not measure them: it drives a page in a
    preview that answers every read with an empty workspace, and a page whose content is the
    workspace's rows draws nothing there. What holds is the kit rules and the build."""

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    conversation_id = uuid4()
    workspace = workspace_root / str(conversation_id)
    app = workspace / "ufo-app"
    app.mkdir(parents=True)
    (app / "index.html").write_bytes(APPLICATION_INDEX)
    (app / "preview.html").write_bytes(PROJECT_PREVIEW_BYTES)
    (app / "vite.config.ts").write_bytes(PROJECT_CONFIG_BYTES)
    for filename, content in beside.items():
        (app / filename).write_bytes(content)
    validate_application_source((app / "app.tsx").read_text())
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
        f"{SANDBOX_UID}:{SANDBOX_GID}",
        "/workspace",
    )
    assert ownership[0] == 0, ownership[2]

    built = await _docker(
        "run",
        "--rm",
        "--user",
        f"{SANDBOX_UID}:{SANDBOX_GID}",
        "-v",
        f"{workspace}:/workspace",
        "-w",
        "/workspace/ufo-app",
        "--entrypoint",
        "sh",
        sandbox_image,
        "-c",
        "vite build",
    )

    assert built[0] == 0, (built[2] or built[1])[-3000:]
    assert (app / "dist" / "index.html").is_file(), name
