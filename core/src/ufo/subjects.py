"""The subject vocabulary: how memory visibility is scoped, shared by core and the memory extension.

A memory item or source page is scoped to one `subject` — the shared space every conversation
recalls, or one member's private space keyed by member id. Core owns this vocabulary because the
sync pipeline (`FolderSource`) and the source seam (`sdk.sources`) name the shared subject; the
memory extension derives a turn's recall subjects from the same primitives.
"""

from uuid import UUID

SHARED_SUBJECT = "shared"
MEMBER_SUBJECT_PREFIX = "member:"


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"
