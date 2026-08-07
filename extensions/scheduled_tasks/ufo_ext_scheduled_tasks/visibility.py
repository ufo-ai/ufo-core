from uuid import UUID

from ufo.sdk.scheduling import ScheduledTask


def task_content_visible(task: ScheduledTask, member_id: UUID | None) -> bool:
    return task.created_by_member_id is None or task.created_by_member_id == member_id
