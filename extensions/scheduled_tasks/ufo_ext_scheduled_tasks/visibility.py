from collections.abc import Container
from uuid import UUID

from ufo.sdk.subjects import member_subject, subject_shared
from ufo_ext_scheduled_tasks.schedules import ListedTask


def task_content_visible(
    listed: ListedTask, member_id: UUID | None, disclosed: Container[UUID] = ()
) -> bool:
    """Whether this member reads a task's prompt and description. A task reporting into a
    conversation the whole workspace reads is no more private than the replies it posts there, so
    its content answers every member; a task reporting into one member's own conversation answers
    that member alone. A task with no creator is decided the same way — by where it reports, never
    by the missing creator — so a creatorless task in a private conversation is not everyone's to
    read, and a caller with no member of their own reads only what the workspace shares.

    `disclosed` names the conversations this reader holds an open `read_private_transcript`
    acknowledgement on — an admin's one sanctioned way into another member's private content. A
    task is its conversation's, so the acknowledgement that opens the transcript opens the task
    reporting into it, for as long as that disclosure stands and on the same record. A read that
    cannot establish its reader is an admin passes none, which is every chat tool and every
    member's own read."""
    if subject_shared(listed.audience):
        return True
    if listed.task.conversation_id in disclosed:
        return True
    if member_id is None:
        return False
    if listed.audience == member_subject(member_id):
        return True
    return listed.task.created_by_member_id == member_id
