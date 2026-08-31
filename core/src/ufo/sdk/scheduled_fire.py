"""Public re-export: the scheduled fire's admission-key shape — the builder every cron fire is
keyed through and the parser that resolves a run back to its task."""

from ufo.runtime.ext.scheduled_fire import (
    scheduled_fire_key as scheduled_fire_key,
)
from ufo.runtime.ext.scheduled_fire import (
    scheduled_fire_task_id as scheduled_fire_task_id,
)
