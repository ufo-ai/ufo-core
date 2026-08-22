"""The shared and member subject atoms used by sources and conversation audiences."""

from uuid import UUID

SHARED_SUBJECT = "shared"
MEMBER_SUBJECT_PREFIX = "member:"


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"


def subject_shared(subject: str) -> bool:
    """Whether content disclosed to this subject is readable by every member of the workspace —
    the `shared` half of an object row's visibility, where a member-scoped row's own reader is
    answered by ownership instead. A room and an externally-shared channel are false for the same
    reason they are absent from `readable_audiences`: no membership fact exists for either."""
    return subject == SHARED_SUBJECT
