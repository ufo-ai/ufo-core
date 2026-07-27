"""The exact disclosure audience carried from a conversation into its turns."""

from typing import NewType
from uuid import UUID

from ufo.subjects import MEMBER_SUBJECT_PREFIX, SHARED_SUBJECT

Audience = NewType("Audience", str)
SHARED_AUDIENCE = Audience(SHARED_SUBJECT)


def conversation_audience(member_id: UUID | None) -> Audience:
    return SHARED_AUDIENCE if member_id is None else Audience(f"{MEMBER_SUBJECT_PREFIX}{member_id}")


def audience_member(audience: Audience) -> UUID | None:
    """Decode and validate an audience that may have crossed the extension SDK boundary."""
    if audience == SHARED_AUDIENCE:
        return None
    prefix, separator, value = audience.partition(":")
    if prefix != "member" or not separator:
        raise ValueError(f"invalid conversation audience {audience!r}")
    member_id = UUID(value)
    if audience != conversation_audience(member_id):
        raise ValueError(f"invalid conversation audience {audience!r}")
    return member_id
