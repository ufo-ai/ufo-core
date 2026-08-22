"""Fabricated WANDR upstream and snapshot fixtures: a miniature dataset tree with the upstream
manifest and per-task packages whose fake verifier obeys the real `test.sh` contract, so wandr
suite tests exercise selection, snapshot, runner, and grading against real files."""

from pathlib import Path

from evals.wandr.build import SnapshotBuilder
from evals.wandr.models import Selection
from evals.wandr.selection import SelectionBuilder
from evals.wandr.snapshot import task_content_digest

TEST_SEED = "wandr-test-seed"
TEST_REVISION = "a" * 40
PER_LABEL = 16
CAPTURED_SOFT_F1 = 0.5
CAPTURED_HARD_F1 = 0.25

FAKE_TEST_SH = """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$LOGS_DIR"
if [[ "${WANDR_FAKE_FAIL:-0}" == "1" ]]; then
  printf '{"error": "fake verifier exploded"}' > "$LOGS_DIR/error.json"
  exit 1
fi
if compgen -G "$WORKSPACE_DIR/results_*.jsonl" > /dev/null; then
  soft=0.5; hard=0.25
else
  soft=0.0; hard=0.0
fi
printf '{"reward": %s, "soft_f1_full": %s, "soft_recall_full": %s, "hard_f1_full": %s}' \\
  "$soft" "$soft" "$soft" "$hard" > "$LOGS_DIR/reward.json"
: > "$LOGS_DIR/.complete"
"""


def fabricate_upstream(root: Path) -> Path:
    dataset = root / "datasets" / "wandr"
    names = ["smoke"] + [
        f"{label}-task-{index:02d}"
        for label in ("low", "medium", "high")
        for index in range(PER_LABEL)
    ]
    for name in names:
        _task_package(dataset / name, name, None if name == "smoke" else name.split("-")[0])
    entries = "\n".join(
        f'[[tasks]]\nname = "wandr/{name}"\ndigest = "{task_content_digest(dataset / name)}"\n'
        for name in names
    )
    (dataset / "dataset.toml").write_text(entries)
    return root


def fabricate_selection(upstream: Path) -> Selection:
    return SelectionBuilder(upstream, TEST_REVISION, TEST_SEED).build()


def materialized_snapshot(upstream: Path, output_root: Path, selection: Selection) -> Path:
    return SnapshotBuilder(output_root, selection, source=upstream).build()


def required_file(name: str) -> str:
    return f"results_{name.replace('-', '_')}.jsonl"


def _task_package(task_dir: Path, name: str, label: str | None) -> None:
    tests = task_dir / "tests"
    (tests / "wandr_core").mkdir(parents=True)
    (task_dir / "instruction.md").write_text(f"Research entities for {name} and cite evidence.\n")
    difficulty = "" if label is None else f'difficulty-label = "{label}"\n'
    (task_dir / "task.toml").write_text(
        f'[metadata]\nrequired_file_paths = ["{required_file(name)}"]\n{difficulty}'
    )
    (tests / "wandr_core" / "uv.lock").write_text("fake-lock v1\n")
    script = tests / "test.sh"
    script.write_text(FAKE_TEST_SH)
    script.chmod(0o755)
