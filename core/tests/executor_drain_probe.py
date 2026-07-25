"""Subprocess half of the executor-drain proof (driven by test_serve.py): serve's exact DBOS
topology — constructed without fastapi, launched from a sync context — with two queued async
workflows, one parked at an ordinary await and one that absorbs cancellation. Emits one JSON line
of evidence for the contract `_stop_executor` retires the fleet seat on: workflows run off the
main thread (DBOS's background loop, beyond any main-thread `asyncio.run` teardown), survive
exactly such a teardown, the parked one is drained boundedly and released by `DBOS.destroy`, and
the cancellation-resistant one outlives destroy with its active-set entry retained — the case the
keep-seat branch exists for."""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

from dbos import DBOS, Queue

WORKFLOW_SECONDS = 3600.0
DRAIN_SECONDS = 2
START_TIMEOUT_SECONDS = 10

dbos = DBOS(
    config={
        "name": "draincheck",
        "system_database_url": f"sqlite:///{Path(sys.argv[1])}",
        "run_admin_server": False,
        "executor_id": "probe-executor",
    }
)
queue = Queue("probe-q")
parked_started = threading.Event()
stubborn_started = threading.Event()
evidence: dict[str, object] = {}


@DBOS.workflow()
async def parked(seconds: float) -> str:
    evidence["on_main_thread"] = threading.current_thread() is threading.main_thread()
    parked_started.set()
    await asyncio.sleep(seconds)
    return "done"


@DBOS.workflow()
async def stubborn(seconds: float) -> str:
    stubborn_started.set()
    while True:
        try:
            await asyncio.sleep(seconds)
            return "done"
        except asyncio.CancelledError:
            continue


DBOS.launch()
queue.enqueue(parked, WORKFLOW_SECONDS)
stubborn_handle = queue.enqueue(stubborn, WORKFLOW_SECONDS)
if not (
    parked_started.wait(timeout=START_TIMEOUT_SECONDS)
    and stubborn_started.wait(timeout=START_TIMEOUT_SECONDS)
):
    raise RuntimeError("workflows never started")
evidence["active_while_parked"] = len(dbos._active_workflows_set.activeList())

asyncio.run(asyncio.sleep(0))
time.sleep(1)
evidence["active_after_main_loop_teardown"] = len(dbos._active_workflows_set.activeList())

begun = time.monotonic()
DBOS.destroy(workflow_completion_timeout_sec=DRAIN_SECONDS)
evidence["destroy_seconds"] = round(time.monotonic() - begun, 1)
evidence["active_after_destroy"] = dbos._active_workflows_set.activeList()
evidence["retained_is_stubborn"] = evidence["active_after_destroy"] == [stubborn_handle.workflow_id]
print(json.dumps(evidence))
