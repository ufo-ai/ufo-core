"""Keep a test's DBOS workflows inside the database fixture that admitted them.

A test can finish after observing its externally complete state while its workflow still owns a
database transaction. The next Postgres wipe can then deadlock with that transaction. SQLite hands
each test a private copy, but still owes workflow completion before disposing that copy's engine.
"""

import asyncio

from dbos import DBOSClient

WORKFLOW_DRAIN_SECONDS = 30.0
WORKFLOW_DRAIN_POLL_SECONDS = 0.05
UNSETTLED_STATUSES = ["PENDING", "ENQUEUED"]


async def drain_workflows(client: DBOSClient) -> None:
    """Wait until no workflow this test started is still pending or queued. Raises rather than
    returning on a workflow that never settles: a test leaking a writer into the next one is the
    condition this exists to refuse, and a silent timeout would hand the collision on."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WORKFLOW_DRAIN_SECONDS
    while True:
        unsettled = await client.list_workflows_async(status=UNSETTLED_STATUSES)
        if not unsettled:
            return
        if loop.time() >= deadline:
            raise RuntimeError(
                "workflows did not settle before teardown: "
                + ", ".join(f"{run.workflow_id} {run.status}" for run in unsettled)
            )
        await asyncio.sleep(WORKFLOW_DRAIN_POLL_SECONDS)
