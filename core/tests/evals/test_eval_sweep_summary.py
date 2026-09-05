"""The nightly cohort gate: which exclusions it requires, accepts, and refuses."""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / ".github" / "scripts" / "eval_sweep_summary.py"
LABEL = "nightly-assistant-eval"
SUITE = "document_read"
MODEL = "claude-opus-5"


@pytest.fixture(scope="module")
def summary():
    spec = importlib.util.spec_from_file_location("eval_sweep_summary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cohort(summary, monkeypatch, tmp_path) -> Path:
    """One planned run of the document-read suite, and nothing else to complete."""
    monkeypatch.setattr(summary, "_planned_runs", lambda smoke, memory: ((LABEL, SUITE, MODEL),))
    return tmp_path


def _record(
    root: Path, summary, excluded: tuple[str, ...], provider_faults: tuple[str, ...] = ()
) -> None:
    task = next(task for task in summary.TASKS if task.name == SUITE)
    run = {
        "id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "label": LABEL,
        "agent": "main",
        "ufo_version": "0.0.0",
        "revision": "0" * 12,
        "reports": [
            {
                "name": SUITE,
                "suite": SUITE,
                "digest": "0" * 12,
                "target_model": MODEL,
                "cases": [
                    {
                        "name": case,
                        "passed": case not in excluded,
                        "reason": "recorded",
                        "evidence": {},
                        "excluded": case in excluded,
                        "provider_fault": case in provider_faults,
                    }
                    for case in task.cases
                ],
            }
        ],
    }
    path = root / "runs" / f"{run['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run))


def test_the_cohort_accepts_an_excluded_document_read_page(summary, cohort) -> None:
    _record(cohort, summary, ("docx-second-page",))

    summary.require_comparable(cohort, smoke=False)


def test_the_cohort_accepts_a_scored_document_read_page(summary, cohort) -> None:
    """A page excludes only when the target model asked for it. A model that answers from another
    tool leaves the case scored, and the night keeps its trend point."""
    _record(cohort, summary, ())

    summary.require_comparable(cohort, smoke=False)


def test_the_cohort_refuses_an_exclusion_it_does_not_list(summary, cohort) -> None:
    _record(cohort, summary, ("xlsx-formula-structure",))

    with pytest.raises(RuntimeError, match="unexpected exclusions"):
        summary.require_comparable(cohort, smoke=False)


def test_the_cohort_accepts_an_exclusion_the_provider_owns(summary, cohort) -> None:
    """A provider timeout lands on whichever case it lands on, so no list can name it. The night
    keeps its trend point and the summary names the case."""
    _record(
        cohort,
        summary,
        ("xlsx-formula-structure",),
        provider_faults=("xlsx-formula-structure",),
    )

    summary.require_comparable(cohort, smoke=False)

    assert "1 cases excluded on provider faults" in summary.render(cohort, smoke=False)
    assert "xlsx-formula-structure" in summary.render(cohort, smoke=False)
