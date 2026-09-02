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
