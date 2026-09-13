"""The shared and member subject atoms used by sources and conversation audiences."""

from uuid import UUID

SHARED_SUBJECT = "shared"
MEMBER_SUBJECT_PREFIX = "member:"


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"


def connection_subject(shared: bool, owner_member_id: UUID | None) -> str:
    """The disclosure every page synced through this connection is stamped with: the workspace's
    shared subject where the connection is shared, else its owner's. A connection nobody owns is
    always shared — the schema's `connection_shared` check — so the pair cannot be both."""
    if shared:
        return SHARED_SUBJECT
    if owner_member_id is None:
        raise RuntimeError("a private connection with no owner discloses nothing")
    return member_subject(owner_member_id)
