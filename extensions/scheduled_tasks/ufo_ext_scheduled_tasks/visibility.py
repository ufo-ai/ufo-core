from uuid import UUID

from ufo.sdk.scheduling import ListedTask
from ufo.sdk.subjects import member_subject, subject_shared


def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool:
    """Whether this member reads a task's prompt and description. A task reporting into a
    conversation the whole workspace reads is no more private than the replies it posts there, so
    its content answers every member; a task reporting into one member's own conversation answers
    that member alone. A task with no creator is decided the same way — by where it reports, never
    by the missing creator — so a creatorless task in a private conversation is not everyone's to
    read, and a caller with no member of their own reads only what the workspace shares."""
    if subject_shared(listed.audience):
        return True
    if member_id is None:
        return False
    if listed.audience == member_subject(member_id):
        return True
    return listed.task.created_by_member_id == member_id
