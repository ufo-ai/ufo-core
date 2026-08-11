from uuid import UUID

from ufo.sdk.scheduling import ListedTask
from ufo.sdk.subjects import subject_shared


def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool:
    """Whether this member reads a task's prompt and description. A task reporting into a
    conversation the whole workspace reads is no more private than the replies it posts there, so
    its content answers every member; a task reporting into one member's own conversation answers
    that member alone."""
    return (
        listed.task.created_by_member_id is None
        or listed.task.created_by_member_id == member_id
        or subject_shared(listed.audience)
    )
