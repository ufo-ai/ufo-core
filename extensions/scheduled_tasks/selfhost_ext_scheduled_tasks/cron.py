"""Cron for scheduled tasks: validate a 5-field expression and compute its next fire.

The core store orders on a `next_run_at` datetime and knows no cron; the cadence dialect lives here,
with the extension that owns it. `next_fire` advances strictly past `after`, so a runner behind on
its poll collapses every missed window into a single catch-up fire rather than a backlog burst."""

from datetime import datetime

from croniter import croniter

CRON_FIELD_COUNT = 5


def validate_cron(schedule: str) -> str:
    if len(schedule.split()) != CRON_FIELD_COUNT:
        raise ValueError(f"schedule must be a 5-field cron expression: {schedule!r}")
    if not croniter.is_valid(schedule):
        raise ValueError(f"invalid cron schedule: {schedule!r}")
    return schedule


def next_fire(schedule: str, after: datetime) -> datetime:
    return croniter(schedule, after).get_next(datetime)
