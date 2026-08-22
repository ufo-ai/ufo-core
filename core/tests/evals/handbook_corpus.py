"""A fabricated HANDBOOK.md checkout and its pin, so corpus and runner tests exercise the real
verification path without the upstream repository."""

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from evals.handbook.build import FILE_TOOL_SETS, LICENSE, REPOSITORY, TOOL_SETS
from evals.handbook.corpus import PinnedTask, UpstreamPin, tree_digest

REVISION = "0" * 40
TASK_ID = "finance_meridian_partners_19d57538"
DOCUMENTS_ONLY_TASK_ID = "insurance_vanguard_shield_mutual_82da8d17"
INSTRUCTION = "Process invoice INV-1 through the AP workflow and update ap_ledger.xlsx."
SYSTEM_PROMPT = "Today's date is, May, 21, 2025. You are an office assistant."
HANDBOOK = "<html><body>Hold any invoice whose variance exceeds 2%.</body></html>"
LEDGER = b"not really a spreadsheet, but stable bytes"
POLICY_PDF = b"%PDF-1.4 not really a pdf, but stable bytes"
VERIFIER_SOURCE = "print('scored')\n"


def fabricate_checkout(root: Path, rubrics: int = 2) -> Path:
    """A checkout with one task, shaped exactly as upstream's: instruction, preamble, rubrics,
    verifier, environment Dockerfile, seeded workspace, and seeded service state."""
    task = root / "tasks" / TASK_ID
    (task / "tests").mkdir(parents=True)
    (task / "environment" / "initial_workspace").mkdir(parents=True)
    (task / "environment" / "initial_external_services" / "slack").mkdir(parents=True)
    (task / "instruction.md").write_text(INSTRUCTION)
    (task / "system_prompt.md").write_text(SYSTEM_PROMPT)
    (task / "task.toml").write_text(
        "[environment]\n"
        'env = { INPUTDIR = "/data", WORLDBENCH_TOOL_SETS = "' + " ".join(TOOL_SETS) + '" }\n'
    )
    (task / "tests" / "rubrics.json").write_text(
        json.dumps([{"id": f"rubric-{index}", "verifier_code": "x"} for index in range(rubrics)])
    )
    (task / "tests" / "sop_verifier.py").write_text(VERIFIER_SOURCE)
    (task / "environment" / "Dockerfile").write_text("FROM handbook_base\n")
    (task / "environment" / "initial_workspace" / "SOP.html").write_text(HANDBOOK)
    (task / "environment" / "initial_workspace" / "ap_ledger.xlsx").write_bytes(LEDGER)
    (task / "environment" / "initial_external_services" / "slack" / "slack.json").write_text("{}")
    return root


def fabricate_documents_only_task(checkout: Path) -> str:
    """A second task whose seeded workspace holds policy documents and nothing else — three upstream
    insurance tasks have this shape, and it is the one the ingested arm has nothing left to stage
    for."""
    task = checkout / "tasks" / DOCUMENTS_ONLY_TASK_ID
    shutil.copytree(checkout / "tasks" / TASK_ID, task)
    (task / "environment" / "initial_workspace" / "ap_ledger.xlsx").unlink()
    (task / "environment" / "initial_workspace" / "Appeals_Policy.pdf").write_bytes(POLICY_PDF)
    return DOCUMENTS_ONLY_TASK_ID


def fabricate_pin(
    checkout: Path, rubrics: int = 2, task_ids: tuple[str, ...] = (TASK_ID,)
) -> UpstreamPin:
    return UpstreamPin(
        repository=REPOSITORY,
        revision=REVISION,
        license=LICENSE,
        tool_sets=TOOL_SETS,
        file_tool_sets=FILE_TOOL_SETS,
        verifier_sha256=f"sha256:{sha256(VERIFIER_SOURCE.encode()).hexdigest()}",
        tasks=tuple(
            PinnedTask(
                task_id=task_id,
                rubrics=rubrics,
                tree_sha256=tree_digest(checkout / "tasks" / task_id),
            )
            for task_id in task_ids
        ),
    )


def install_pin(pin: UpstreamPin, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.handbook.corpus.load_pin", lambda: pin)
    monkeypatch.setattr("evals.handbook.runner.load_pin", lambda: pin)
