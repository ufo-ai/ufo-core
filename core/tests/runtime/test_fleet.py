import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from dbos._dbos import _get_or_create_dbos_registry
from dbos._utils import INTERNAL_QUEUE_NAME
from sqlalchemy.engine import make_url

from ufo.runtime.jobs import JOB_QUEUE_NAME
from ufo.schema.records import (
    EXPRESS_QUEUE_NAME,
    TURN_QUEUE_NAME,
    UNSCOPED_EXPRESS_QUEUE_NAME,
    UNSCOPED_TURN_QUEUE_NAME,
)
from ufo.serve import FLEETS, JOBS_FLEET, TURNS_FLEET, WHOLE_FLEET

WORKER = Path(__file__).with_name("fleet_worker.py")
HOSTED_TEMPLATE = Path(__file__).resolve().parents[3] / "infra/templates/hosted.yaml.tpl"
DEPLOYED_FLEET = re.compile(r"args: \[serve, --fleet, (?P<fleet>[a-z]+)\]")
WORKER_TIMEOUT_SECONDS = 180
ENQUEUED = "ENQUEUED"
SUCCESS = "SUCCESS"


def test_the_fleets_partition_every_queue_the_deploy_registers() -> None:
    """Importing `ufo.serve` is what a serve process does, and it registers every queue the deploy
    runs. A queue no fleet names is one no pod would ever dequeue — work enqueued onto it would sit
    forever — and a fleet naming a queue nothing registers listens to silence, which looks exactly
    like a healthy pod running nothing. Both are invisible at runtime, so the partition is asserted
    here instead."""
    registered = set(_get_or_create_dbos_registry().queue_info_map) - {INTERNAL_QUEUE_NAME}
    claimed = [queue for fleet in (TURNS_FLEET, JOBS_FLEET) for queue in fleet.queues]

    assert registered
    assert registered == {
        TURN_QUEUE_NAME,
        EXPRESS_QUEUE_NAME,
        UNSCOPED_TURN_QUEUE_NAME,
        UNSCOPED_EXPRESS_QUEUE_NAME,
        JOB_QUEUE_NAME,
    }
    assert len(claimed) == len(set(claimed)), "a queue two fleets both claim is not a split"
    assert set(claimed) == registered
    assert set(WHOLE_FLEET.queues) == registered
    assert set(FLEETS) == {"all", "turns", "jobs"}


def test_only_the_turns_fleet_carries_the_surfaces() -> None:
    """An inbound surface listener is one connection admitted across all replicas by a fenced
    lease. A jobs process holding it would land a member's messages on the fleet that runs the
    indexer — the coupling the split exists to remove."""
    assert TURNS_FLEET.surfaces
    assert WHOLE_FLEET.surfaces
    assert not JOBS_FLEET.surfaces


def test_the_hosted_deployments_name_the_fleets_the_cli_serves() -> None:
    """A pod's args are where the split becomes real, so both ends have to agree. A name `ufoctl
    serve` does not accept kills the container at boot; a deployed set that misses a fleet leaves
    its queues to nobody, which no manifest review would show."""
    deployed = set(DEPLOYED_FLEET.findall(HOSTED_TEMPLATE.read_text()))

    assert deployed == {TURNS_FLEET.name, JOBS_FLEET.name}
    assert deployed <= set(FLEETS)


@pytest.mark.serial
def test_neither_fleet_can_dequeue_the_other_s_work(database_url: str) -> None:
    """The split, proven across two real processes on one DBOS store: the jobs process runs the
    marker offered to the background queue and leaves the member one ENQUEUED — it cannot see it —
    and the turns process then runs exactly that leftover. Executor ids show which pod did which."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prod-topology fleet proof runs on postgres")
    system_url = _reset_private_system_db(database_url)
    job_id, turn_id, unscoped_turn_id, unscoped_express_id = (uuid4().hex for _ in range(4))
    env = {
        **os.environ,
        "FLEET_TEST_SYSTEM_URL": system_url,
        "FLEET_TEST_JOB_ID": job_id,
        "FLEET_TEST_TURN_ID": turn_id,
        "FLEET_TEST_UNSCOPED_TURN_ID": unscoped_turn_id,
        "FLEET_TEST_UNSCOPED_EXPRESS_ID": unscoped_express_id,
    }

    jobs = _run_worker("jobs", env)
    assert jobs.returncode == 0, f"jobs fleet never ran its own work\n{jobs.stdout}\n{jobs.stderr}"
    jobs_executor, jobs_seen = _report(jobs.stdout)
    assert jobs_seen[job_id] == {"status": SUCCESS, "executor": jobs_executor}
    assert jobs_seen[turn_id]["status"] == ENQUEUED
    assert jobs_seen[turn_id]["executor"] != jobs_executor
    assert jobs_seen[unscoped_turn_id]["status"] == ENQUEUED
    assert jobs_seen[unscoped_express_id]["status"] == ENQUEUED

    turns = _run_worker("turns", env)
    assert turns.returncode == 0, (
        f"turns fleet never ran the work jobs left\n{turns.stdout}\n{turns.stderr}"
    )
    turns_executor, turns_seen = _report(turns.stdout)
    assert turns_executor != jobs_executor
    assert turns_seen[turn_id] == {"status": SUCCESS, "executor": turns_executor}
    assert turns_seen[unscoped_turn_id] == {"status": SUCCESS, "executor": turns_executor}
    assert turns_seen[unscoped_express_id] == {"status": SUCCESS, "executor": turns_executor}


def _run_worker(fleet: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), fleet],
        env=env,
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT_SECONDS,
    )


def _report(stdout: str) -> tuple[str, dict[str, dict[str, str]]]:
    """The worker prints its executor id at launch and the two statuses at exit, one JSON object
    per line. Read the last two, so DBOS's own logging cannot be mistaken for either."""
    lines = [line for line in stdout.splitlines() if line.startswith("{")]
    executor = json.loads(lines[0])["executor"]
    return executor, json.loads(lines[-1])


def _reset_private_system_db(database_url: str) -> str:
    """A per-test DBOS system database, so these subprocess fleets never share queues with the
    session's own DBOS launch. Returns the psycopg-driver url the workers take."""
    from ufo_testsupport.plugin import reset_postgres_database

    base = make_url(database_url)
    name = f"{base.database}_fleet_sys"
    asyncio.run(reset_postgres_database(name))
    return base.set(database=name, drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )
