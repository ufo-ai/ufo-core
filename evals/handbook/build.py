"""Write `data/upstream.json` from a HANDBOOK.md checkout.

Run this once against a checkout at the revision the suite should pin, then commit the pin. Every
later run verifies a checkout against it (`evals.handbook.corpus.load_corpus`), so the pin is what
makes a score attributable to a known corpus."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path

from evals.handbook.corpus import (
    RUBRICS,
    UPSTREAM_PIN,
    VERIFIER,
    PinnedTask,
    UpstreamPin,
    tree_digest,
)

REPOSITORY = "surge-ai/handbook"
LICENSE = "Apache-2.0"
TOOL_SETS = (
    "syntara_ds_all",
    "google_mail_contacts",
    "slack_core",
    "google_mail_core",
    "google_calendar_core",
    "jira_core",
    "shopify_core",
)
FILE_TOOL_SETS = ("syntara_ds_all",)
EXPECTED_TASKS = 65
TOOL_SETS_RE = re.compile(r'WORLDBENCH_TOOL_SETS = "([^"]+)"')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.handbook.build")
    parser.add_argument("--checkout", type=Path, required=True, help="HANDBOOK.md checkout")
    parser.add_argument("--out", type=Path, default=UPSTREAM_PIN, help="pin file to write")
    args = parser.parse_args(argv)
    pin = build_pin(args.checkout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pin.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    print(f"pinned {len(pin.tasks)} tasks at {pin.revision} · {args.out}")


def build_pin(checkout: Path) -> UpstreamPin:
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tasks_root = checkout / "tasks"
    task_roots = sorted(path for path in tasks_root.iterdir() if path.is_dir())
    if len(task_roots) != EXPECTED_TASKS:
        raise ValueError(f"expected {EXPECTED_TASKS} HANDBOOK.md tasks, found {len(task_roots)}")
    verifiers = {sha256((root / VERIFIER).read_bytes()).hexdigest() for root in task_roots}
    if len(verifiers) != 1:
        raise ValueError("HANDBOOK.md tasks do not share one verifier; the pin assumes they do")
    tool_sets = {_tool_sets(root / "task.toml") for root in task_roots}
    if tool_sets != {TOOL_SETS}:
        raise ValueError(f"HANDBOOK.md tool sets are not uniform: {sorted(tool_sets)}")
    tasks = tuple(
        PinnedTask(
            task_id=root.name,
            rubrics=len(json.loads((root / RUBRICS).read_text())),
            tree_sha256=tree_digest(root),
        )
        for root in task_roots
    )
    return UpstreamPin(
        repository=REPOSITORY,
        revision=revision,
        license=LICENSE,
        tool_sets=TOOL_SETS,
        file_tool_sets=FILE_TOOL_SETS,
        verifier_sha256=f"sha256:{verifiers.pop()}",
        tasks=tasks,
    )


def _tool_sets(task_toml: Path) -> tuple[str, ...]:
    found = TOOL_SETS_RE.search(task_toml.read_text())
    if found is None:
        raise ValueError(f"{task_toml} declares no WORLDBENCH_TOOL_SETS")
    return tuple(found.group(1).split())


if __name__ == "__main__":
    main()
