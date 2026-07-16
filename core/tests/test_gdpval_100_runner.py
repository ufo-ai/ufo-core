from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evals.__main__ import main as eval_main
from evals.gdpval_100.models import RubricItem, SnapshotAsset, SnapshotCase
from evals.gdpval_100.runner import (
    GDPvalCalibration,
    _submission_grader,
    _workspace_reference_paths,
    load_calibration,
)
from evals.gdpval_100.snapshot import GDPVAL_UPSTREAM, content_digest, write_snapshot
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    SharedArtifactReference,
    ToolInvocation,
    grading_statement,
)
from evals.harness.target import TargetResult

REFERENCE = b"month,revenue\nJan,100\n"


@pytest.mark.parametrize(
    ("arguments", "error"),
    (
        (
            ("--gdpval-100", "snapshot"),
            "--gdpval-100 and --gdpval-treatment must be provided together",
        ),
        (
            ("--gdpval-treatment", "gdpval_core"),
            "--gdpval-100 and --gdpval-treatment must be provided together",
        ),
        (
            ("--gdpval-task", "task-id"),
            "--gdpval-task requires --gdpval-100",
        ),
    ),
)
def test_gdpval_cli_rejects_incomplete_calibration_arguments(
    arguments: tuple[str, ...], error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        eval_main(list(arguments))

    assert error in capsys.readouterr().err


def test_gdpval_cli_requires_the_treatment_pack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "evals.__main__.load_calibration",
        lambda *_args: GDPvalCalibration("gdpval_full", ()),
    )
    monkeypatch.setattr(
        "evals.__main__.load_config",
        lambda: SimpleNamespace(pack=SimpleNamespace(name="gdpval_core")),
    )

    with pytest.raises(SystemExit):
        eval_main(
            [
                "--gdpval-100",
                "snapshot",
                "--gdpval-treatment",
                "gdpval_full",
            ]
        )

    assert (
        "GDPval treatment 'gdpval_full' requires [pack] name = 'gdpval_full', "
        "found 'gdpval_core'" in capsys.readouterr().err
    )


@dataclass
class SubmittedTarget:
    seen: CapabilityCase | None = None

    async def run(self, case: CapabilityCase) -> TargetResult:
        self.seen = case
        return TargetResult(
            CapabilityOutput(
                "Done.",
                (
                    ToolInvocation(
                        "share_file",
                        {"file_path": "/workspace/forecast.xlsx"},
                        '{"name":"forecast.xlsx"}',
                        has_result=True,
                    ),
                ),
                artifact_references=(
                    SharedArtifactReference(
                        "forecast.xlsx",
                        "artifacts/run/forecast.xlsx",
                        content_digest(b"workbook"),
                        8,
                    ),
                ),
            ),
            clean=True,
        )


def _snapshot(tmp_path: Path, materialized: bool = True) -> tuple[Path, str]:
    staging = tmp_path / "staging"
    staging.mkdir()
    first_id = str(uuid4())
    digest = content_digest(REFERENCE)
    snapshot_path = f"objects/sha256/{digest.removeprefix('sha256:')}"
    if materialized:
        path = staging / snapshot_path
        path.parent.mkdir(parents=True)
        path.write_bytes(REFERENCE)
    reference = SnapshotAsset(
        relative_path=f"reference_files/{first_id}/forecast.csv",
        snapshot_path=snapshot_path if materialized else None,
        size_bytes=len(REFERENCE) if materialized else None,
        sha256=digest if materialized else None,
    )
    rubric = (
        RubricItem(
            score=1,
            criterion="The forecast is correct.",
            rubric_item_id=str(uuid4()),
            tags=(),
        ),
    )
    cases: list[SnapshotCase] = []
    for occupation in range(44):
        for ordinal in range(5):
            task_id = first_id if occupation == 0 and ordinal == 0 else str(uuid4())
            cases.append(
                SnapshotCase(
                    task_id=task_id,
                    sector="Information",
                    occupation=f"Occupation {occupation}",
                    prompt="Build the monthly forecast.",
                    references=(reference,) if task_id == first_id else (),
                    deliverables=(),
                    rubric=rubric,
                )
            )
    return (
        write_snapshot(
            staging,
            tmp_path / "snapshots",
            GDPVAL_UPSTREAM,
            tuple(cases),
            (first_id,) if materialized else (),
        ),
        first_id,
    )


async def test_calibration_case_preserves_prompt_stages_references_and_captures_submission(
    tmp_path: Path,
) -> None:
    snapshot_root, task_id = _snapshot(tmp_path)
    calibration = load_calibration(snapshot_root, "gdpval_full", (task_id,))
    target = SubmittedTarget()

    report = await calibration.tasks[0].run(target)

    assert report.passed
    assert target.seen is not None
    assert target.seen.message.startswith("Build the monthly forecast.\n\n---\n")
    assert "- references/forecast.csv" in target.seen.message
    assert (
        "Submit every requested deliverable with a separate share_file call." in target.seen.message
    )
    assert target.seen.references[0].path == "forecast.csv"
    assert target.seen.references[0].source.read_bytes() == REFERENCE
    attempt = report.cases[0].evidence["attempts"][0]
    assert isinstance(attempt, dict)
    assert attempt["artifactReferences"] == [
        {
            "name": "forecast.xlsx",
            "blobKey": "artifacts/run/forecast.xlsx",
            "digest": content_digest(b"workbook"),
            "sizeBytes": 8,
        }
    ]


def test_calibration_rejects_an_unmaterialized_case_reference(tmp_path: Path) -> None:
    snapshot_root, task_id = _snapshot(tmp_path, materialized=False)

    try:
        load_calibration(snapshot_root, "gdpval_core", (task_id,))
    except ValueError as error:
        assert str(error) == f"GDPval task assets are not materialized: {task_id}"
    else:
        raise AssertionError("unmaterialized reference was accepted")


def test_calibration_defaults_to_materialized_cases(tmp_path: Path) -> None:
    snapshot_root, task_id = _snapshot(tmp_path)

    calibration = load_calibration(snapshot_root, "gdpval_full")

    assert tuple(task.cases for task in calibration.tasks) == ((task_id,),)


def test_workspace_reference_paths_disambiguates_colliding_basenames() -> None:
    assets = (
        SnapshotAsset(relative_path="first/forecast.csv"),
        SnapshotAsset(relative_path="second/forecast.csv"),
    )

    assert _workspace_reference_paths(assets) == (
        "01-forecast.csv",
        "02-forecast.csv",
    )


async def test_submission_grader_rejects_an_absent_share_file_submission() -> None:
    verdict = await _submission_grader(CapabilityOutput("Done.", ()))

    assert not verdict.passed
    assert verdict.reason == "no artifact was submitted through share_file"
    assert verdict.evidence == {"submissionCount": 0, "submissionDigests": []}
    assert grading_statement(_submission_grader) == (
        "at least one deliverable is submitted through share_file and captured durably; "
        "deliverable quality is judged offline against the task rubric"
    )


async def test_submission_grader_rejects_share_calls_without_matching_durable_artifacts() -> None:
    digest = content_digest(b"workbook")
    verdict = await _submission_grader(
        CapabilityOutput(
            "Done.",
            (
                ToolInvocation(
                    "share_file",
                    {"file_path": "/workspace/forecast.xlsx"},
                    '{"name":"forecast.xlsx"}',
                    has_result=True,
                ),
            ),
            artifact_references=(
                SharedArtifactReference(
                    "forecast.xlsx",
                    "artifacts/run/forecast.xlsx",
                    digest,
                    8,
                ),
                SharedArtifactReference(
                    "forecast.csv",
                    "artifacts/run/forecast.csv",
                    digest,
                    8,
                ),
            ),
        )
    )

    assert not verdict.passed
    assert verdict.reason == (
        "successful share_file calls do not match durable submitted artifacts"
    )
    assert verdict.evidence == {
        "submissionCount": 2,
        "submissionDigests": [digest, digest],
    }
