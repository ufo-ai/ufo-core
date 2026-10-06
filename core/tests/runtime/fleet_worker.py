"""Subprocess phases of the fleet-split proof.

`fleet_worker.py <fleet>` launches one process under that fleet's queues, as serve does, on the
DBOS system database `FLEET_TEST_SYSTEM_URL` names, and prints its executor id, then the statuses
its phase reads, one JSON object per line. `jobs` offers markers to both sides and must run only its
own: the member-side markers are still ENQUEUED when it exits. `turns` claims the member queues and
must run the markers the jobs and api processes left. `api` claims no queue: it offers a marker to
every application queue and must run none, yet the recurring tick it schedules rides DBOS's internal
queue, which every fleet drains, and succeeds on its own executor. Together the phases make the
split a partition rather than a preference: no fleet reaches another's work, and nothing the
division leaves behind is stranded.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from uuid import uuid4

from dbos import DBOS, DBOSClient, EnqueueOptions, ScheduleInput

from ufo.harness.durability import ReplaySafeSerializer, replay_safe_client
from ufo.runtime.jobs import JOB_QUEUE_NAME, register_job_queue
from ufo.runtime.queue import register_turn_queues
from ufo.schema.records import (
    DBOS_APP_NAME,
    DBOS_APP_VERSION,
    EXPRESS_QUEUE_NAME,
    TURN_QUEUE_NAME,
    UNSCOPED_EXPRESS_QUEUE_NAME,
    UNSCOPED_TURN_QUEUE_NAME,
)
from ufo.serve import FLEETS

FLEET_MARKER_WORKFLOW = "fleet_marker"
API_TICK_SCHEDULE = "api-tick"
EVERY_SECOND = "* * * * * *"
WAIT_SECONDS = 60
POLL_SECONDS = 0.5
SUCCESS = "SUCCESS"
TIMEOUT_EXIT_CODE = 7


@DBOS.workflow(name=FLEET_MARKER_WORKFLOW)
async def fleet_marker(tag: str) -> str:
    return tag


@DBOS.workflow()
async def api_tick(scheduled_time: datetime, context: str) -> None:
    pass


def _launch(fleet: str, system_url: str) -> str:
    instance_id = uuid4()
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": system_url,
            "executor_id": str(instance_id),
            "serializer": ReplaySafeSerializer(),
        }
    )
    DBOS.listen_queues(FLEETS[fleet].queues)
    DBOS.launch()
    register_turn_queues()
    register_job_queue()
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


async def _await_tick(client: DBOSClient) -> dict[str, str | None]:
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        ticks = await client.list_workflows_async(
            schedule_name=API_TICK_SCHEDULE,
            status=SUCCESS,
            executor_id=DBOS.executor_id,
            load_input=False,
            load_output=False,
        )
        if ticks:
            return {"status": ticks[0].status, "executor": ticks[0].executor_id}
        await asyncio.sleep(POLL_SECONDS)
    return {}


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
    """Claim the member queues and finish the markers the jobs and api processes left."""
    client = replay_safe_client(system_url)
    member_markers = (turn_id, unscoped_turn_id, unscoped_express_id, f"{turn_id}-express")
    ran = all(await asyncio.gather(*(_await_success(client, marker) for marker in member_markers)))
    print(json.dumps(await _statuses(client, [job_id, *member_markers])))
    return 0 if ran else TIMEOUT_EXIT_CODE


async def _api_phase(
    system_url: str, job_id: str, turn_id: str, unscoped_turn_id: str, unscoped_express_id: str
) -> int:
    client = replay_safe_client(system_url)
    express_id = f"{turn_id}-express"
    await _enqueue(client, JOB_QUEUE_NAME, job_id, "job")
    await _enqueue(client, TURN_QUEUE_NAME, turn_id, "turn")
    await _enqueue(client, UNSCOPED_TURN_QUEUE_NAME, unscoped_turn_id, "unscoped-turn")
    await _enqueue(client, UNSCOPED_EXPRESS_QUEUE_NAME, unscoped_express_id, "unscoped-express")
    await _enqueue(client, EXPRESS_QUEUE_NAME, express_id, "express")
    await DBOS.apply_schedules_async(
        [
            ScheduleInput(
                schedule_name=API_TICK_SCHEDULE,
                workflow_fn=api_tick,
                schedule=EVERY_SECOND,
                context="api",
            )
        ]
    )
    scheduled = await _await_tick(client)
    await DBOS.delete_schedule_async(API_TICK_SCHEDULE)
    markers = await _statuses(
        client, [job_id, turn_id, unscoped_turn_id, unscoped_express_id, express_id]
    )
    print(json.dumps({"scheduled": scheduled, **markers}))
    return 0 if scheduled else TIMEOUT_EXIT_CODE


def main() -> int:
    fleet = sys.argv[1]
    system_url = os.environ["FLEET_TEST_SYSTEM_URL"]
    job_id = os.environ["FLEET_TEST_JOB_ID"]
    turn_id = os.environ["FLEET_TEST_TURN_ID"]
    unscoped_turn_id = os.environ["FLEET_TEST_UNSCOPED_TURN_ID"]
    unscoped_express_id = os.environ["FLEET_TEST_UNSCOPED_EXPRESS_ID"]
    executor = _launch(fleet, system_url)
    print(json.dumps({"executor": executor}))
    phase = {"jobs": _jobs_phase, "api": _api_phase}.get(fleet, _turns_phase)
    try:
        return asyncio.run(
            phase(system_url, job_id, turn_id, unscoped_turn_id, unscoped_express_id)
        )
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    sys.exit(main())
