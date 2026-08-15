"""The scheduled fire's admission key: `<task-id>:<occurrence>`, the durable link between a turn
and the task whose fire admitted it. One builder and one parser so the two ends cannot drift — the
scheduled-tasks runner keys every fire through the builder, and the portal's runs feed resolves a
run back to its task through the parser. The occurrence keeps `isoformat()`'s `+00:00` because the
key is a dedupe contract: a turn admitted before a deploy roll was keyed with exactly this string.
A scheduled turn keyed another way (a durable pause's timer resume) parses to None."""

from datetime import datetime
from uuid import UUID

KEY_SEPARATOR = ":"


def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str:
    """The idempotency key one occurrence of one task admits under."""
    return f"{task_id}{KEY_SEPARATOR}{fire_at.isoformat()}"


def scheduled_fire_task_id(key: str) -> UUID | None:
    """The task a fire key names, or None for a key that is not a scheduled fire's."""
    named, _, _ = key.partition(KEY_SEPARATOR)
    try:
        return UUID(named)
    except ValueError:
        return None
