import subprocess
from pathlib import Path

import pytest

from evals.sandbox_image import SandboxImagePlan, sandbox_image_plan


def test_plan_uses_the_existing_definition_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    script = repo / ".github/scripts/sandbox_image_key.sh"
    script.parent.mkdir(parents=True)
    script.write_text("")
    dockerfile = tmp_path / "sandbox.Dockerfile"
    seen: list[tuple[str, ...]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(tuple(argv))
        dockerfile.write_text("FROM example@sha256:abc\n")
        return subprocess.CompletedProcess(argv, 0, b"0123456789abcdef\n")

    monkeypatch.setattr("evals.sandbox_image.subprocess.run", run)
    monkeypatch.setattr("evals.sandbox_image.build_definition_digest", lambda _: "sha256:source")

    plan = sandbox_image_plan(repo, dockerfile)

    assert plan == SandboxImagePlan(
        dockerfile=dockerfile,
        reference="ufo-sandbox-eval:0123456789abcdef",
        definition_digest="sha256:source",
    )
    assert seen == [("bash", str(script), str(dockerfile))]


def test_prepare_reuses_a_compatible_content_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = SandboxImagePlan(
        tmp_path / "sandbox.Dockerfile",
        "ufo-sandbox-eval:0123456789abcdef",
        "sha256:source",
    )
    monkeypatch.setattr(SandboxImagePlan, "_compatible", lambda _: True)
    monkeypatch.setattr(
        "evals.sandbox_image.stage_client_binary",
        lambda: (_ for _ in ()).throw(AssertionError("cache miss")),
    )

    plan.prepare()


def test_prepare_replaces_a_stale_image_with_the_current_content_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "sandbox.Dockerfile"
    dockerfile.write_text("FROM example@sha256:abc\n")
    plan = SandboxImagePlan(
        dockerfile,
        "ufo-sandbox-eval:0123456789abcdef",
        "sha256:source",
    )
    compatible = iter((False, True))
    staged: list[str] = []
    commands: list[tuple[str, ...]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, b"")

    monkeypatch.setattr(SandboxImagePlan, "_compatible", lambda _: next(compatible))
    monkeypatch.setattr("evals.sandbox_image.stage_client_binary", lambda: staged.append("client"))
    monkeypatch.setattr("evals.sandbox_image.stage_system_skills", lambda: staged.append("skills"))
    monkeypatch.setattr("evals.sandbox_image.subprocess.run", run)

    plan.prepare()

    assert staged == ["client", "skills"]
    assert commands == [
        (
            "docker",
            "build",
            "-t",
            plan.reference,
            "-f",
            str(dockerfile),
            str(Path.cwd()),
        )
    ]
    assert "ufo-sandbox:latest" not in commands[0]
