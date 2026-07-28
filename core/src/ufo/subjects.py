"""The shared and member subject atoms used by sources and conversation audiences."""

from uuid import UUID

SHARED_SUBJECT = "shared"
MEMBER_SUBJECT_PREFIX = "member:"


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"
