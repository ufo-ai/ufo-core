"""Subprocess halves of the fleet-split proof (driven by test_fleet.py).

Two real processes against one DBOS system database, each launched under one of the fleets the
hosted Deployments name — the prod topology. `jobs` claims the background execution queue,
enqueues one marker on each side, and must run only its own: the member-side marker is still
ENQUEUED when it exits. `turns` then claims the member queues and must run the marker the jobs
process left. Both halves together are what makes the split a partition rather than a preference —
neither fleet can reach the other's work, and nothing the division leaves behind is stranded.
"""

import asyncio
import json
import os
import sys
from uuid import uuid4

from dbos import DBOS, DBOSClient, EnqueueOptions

from ufo.harness.durability import ReplaySafeSerializer, replay_safe_client
from ufo.runtime.jobs import JOB_QUEUE_NAME
from ufo.schema.records import (
    DBOS_APP_NAME,
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    UNSCOPED_EXPRESS_QUEUE_NAME,
    UNSCOPED_TURN_QUEUE_NAME,
)
from ufo.serve import FLEETS

FLEET_MARKER_WORKFLOW = "fleet_marker"
WAIT_SECONDS = 60
POLL_SECONDS = 0.5
SUCCESS = "SUCCESS"
TIMEOUT_EXIT_CODE = 7


@DBOS.workflow(name=FLEET_MARKER_WORKFLOW)
async def fleet_marker(tag: str) -> str:
    return tag


def _launch(fleet: str, system_url: str) -> str:
    instance_id = uuid4()
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": system_url,
            "executor_id": str(instance_id),
            "run_admin_server": False,
            "serializer": ReplaySafeSerializer(),
        }
    )
    DBOS.listen_queues(FLEETS[fleet].queues)
    DBOS.launch()
    return str(instance_id)


async def _enqueue(client: DBOSClient, queue: str, workflow_id: str, tag: str) -> None:
    options: EnqueueOptions = {
        "queue_name": queue,
        "workflow_name": FLEET_MARKER_WORKFLOW,
        "workflow_id": workflow_id,
        "app_version": DBOS_APP_VERSION,
    }
    await client.enqueue_async(options, tag)


async def _statuses(client: DBOSClient, ids: list[str]) -> dict[str, dict[str, str | None]]:
    rows = await client.list_workflows_async(workflow_ids=ids, load_input=False, load_output=False)
    found = {row.workflow_id: {"status": row.status, "executor": row.executor_id} for row in rows}
    return {workflow_id: found.get(workflow_id, {}) for workflow_id in ids}


async def _await_success(client: DBOSClient, workflow_id: str) -> bool:
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        seen = await _statuses(client, [workflow_id])
        if seen[workflow_id].get("status") == SUCCESS:
            return True
        await asyncio.sleep(POLL_SECONDS)
    return False


async def _jobs_phase(
    system_url: str, job_id: str, turn_id: str, unscoped_turn_id: str, unscoped_express_id: str
) -> int:
    """Claim the background execution queue, offer one marker to each side, and report both."""
    client = replay_safe_client(system_url)
    await _enqueue(client, JOB_QUEUE_NAME, job_id, "job")
    await _enqueue(client, TURN_QUEUE_NAME, turn_id, "turn")
    await _enqueue(client, UNSCOPED_TURN_QUEUE_NAME, unscoped_turn_id, "unscoped-turn")
    await _enqueue(client, UNSCOPED_EXPRESS_QUEUE_NAME, unscoped_express_id, "unscoped-express")
    ran = await _await_success(client, job_id)
    print(
        json.dumps(
            await _statuses(client, [job_id, turn_id, unscoped_turn_id, unscoped_express_id])
        )
    )
    return 0 if ran else TIMEOUT_EXIT_CODE


async def _turns_phase(
    system_url: str, job_id: str, turn_id: str, unscoped_turn_id: str, unscoped_express_id: str
) -> int:
    """Claim the member queues and finish the marker the jobs process could not reach."""
    client = replay_safe_client(system_url)
    ran = all(
        await asyncio.gather(
            *(
                _await_success(client, workflow_id)
                for workflow_id in (turn_id, unscoped_turn_id, unscoped_express_id)
            )
        )
    )
    print(
        json.dumps(
            await _statuses(client, [job_id, turn_id, unscoped_turn_id, unscoped_express_id])
        )
    )
    return 0 if ran else TIMEOUT_EXIT_CODE


def main() -> int:
    fleet = sys.argv[1]
    system_url = os.environ["FLEET_TEST_SYSTEM_URL"]
    job_id = os.environ["FLEET_TEST_JOB_ID"]
    turn_id = os.environ["FLEET_TEST_TURN_ID"]
    unscoped_turn_id = os.environ["FLEET_TEST_UNSCOPED_TURN_ID"]
    unscoped_express_id = os.environ["FLEET_TEST_UNSCOPED_EXPRESS_ID"]
    executor = _launch(fleet, system_url)
    print(json.dumps({"executor": executor}))
    phase = _jobs_phase if fleet == "jobs" else _turns_phase
    try:
        return asyncio.run(
            phase(system_url, job_id, turn_id, unscoped_turn_id, unscoped_express_id)
        )
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    sys.exit(main())
