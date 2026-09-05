"""Every `notify_triage` seed replaces the workspace member's whole inbox with its batch, and two
of the three batches share their routine rows. Two cases seeding at once collided on the shared
rows' ids and ended the shard before any case ran, so the task runs its cases one at a time."""

from evals.registry import TASKS


def test_notify_triage_runs_its_cases_one_at_a_time() -> None:
    task = next(task for task in TASKS if task.name == "notify_triage")

    assert task.exclusive
