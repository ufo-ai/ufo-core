import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / ".github" / "scripts" / "nightly_eval_matrix.py"


@pytest.fixture(scope="module")
def planner():
    spec = importlib.util.spec_from_file_location("nightly_eval_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_explicit_agent_model_shards_run_once(planner) -> None:
    jobs = planner.sweep_jobs(smoke=False)
    code_shards = tuple(shard for shard in planner.plan(smoke=False) if shard.agent == "code")

    assert code_shards
    for shard in code_shards:
        carried = tuple(job for job in jobs if job.shard == shard)
        assert len(carried) == 1
        assert carried[0].expected_model is None


def test_auto_agent_model_shards_run_for_each_nightly_model(planner) -> None:
    jobs = planner.sweep_jobs(smoke=False)
    chat_shards = tuple(shard for shard in planner.plan(smoke=False) if shard.agent == "chat")

    assert chat_shards
    for shard in chat_shards:
        carried = tuple(job for job in jobs if job.shard == shard)
        assert len(carried) == len(planner.NIGHTLY_MODELS)
        assert {job.expected_model for job in carried} == {
            model.id for model in planner.NIGHTLY_MODELS
        }


def test_manual_suites_do_not_enter_the_nightly_plan(planner) -> None:
    suites = {suite for shard in planner.plan(smoke=False) for suite in shard.suites}

    assert "repeated_input_coherence" not in suites


def test_a_shard_reaching_the_preview_service_names_it_and_no_other_shard_does(
    planner, tmp_path
) -> None:
    """The suites in DOCUMENT_RENDER_SUITES route through the preview service, and a shard that
    runs one without `preview_service` has its samples excluded rather than failed — the suite then
    scores 0 of 0 and the sweep reads as though it measured something. The workflow starts the
    service for exactly the shards this marks, so the config and the marking cannot disagree."""
    from evals.stack import DOCUMENT_RENDER_SUITES

    for shard in planner.plan(smoke=False):
        directory = tmp_path / shard.label
        planner.write(shard, directory, "z-ai/glm-5.3-flash", "auto")
        names_service = planner.PREVIEW_SERVICE in (directory / "ufo.toml").read_text()

        assert names_service == (not DOCUMENT_RENDER_SUITES.isdisjoint(shard.suites)), shard.label
