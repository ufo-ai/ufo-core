from datetime import UTC, datetime
from uuid import UUID

from ufo.runtime.ext.scheduled_fire import scheduled_fire_key, scheduled_fire_task_id

TASK_ID = UUID("7f3b9c2e-4d18-4a65-8b07-1e92c5d0f4a3")


def test_a_fire_key_round_trips_its_task() -> None:
    fire_at = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    key = scheduled_fire_key(TASK_ID, fire_at)
    assert key == f"{TASK_ID}:2026-08-14T09:00:00+00:00"
    assert scheduled_fire_task_id(key) == TASK_ID


def test_a_scheduled_turn_keyed_another_way_parses_to_no_task() -> None:
    assert scheduled_fire_task_id(f"pause-fired:{TASK_ID}") is None
    assert scheduled_fire_task_id("not a key") is None
