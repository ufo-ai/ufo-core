"""The immutable authority every execution carries from its origin to each capability it uses."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MemberAuthority:
    """Execution using one member's private credentials, subject to that member's live seat."""

    member_id: UUID


@dataclass(frozen=True, slots=True)
class WorkspaceAuthority:
    """Execution using no member's private credentials."""


type ExecutionAuthority = MemberAuthority | WorkspaceAuthority

WORKSPACE_AUTHORITY = WorkspaceAuthority()


class AuthorityUnavailable(PermissionError):
    """The immutable authority remains identified but cannot currently use capabilities."""


def authority_from_member_id(member_id: UUID | None) -> ExecutionAuthority:
    """Decode the nullable member representation stored on durable records and signed tokens."""
    return WORKSPACE_AUTHORITY if member_id is None else MemberAuthority(member_id)


def authority_member_id(authority: ExecutionAuthority) -> UUID | None:
    """Encode an execution authority for the existing nullable-member persistence shape."""
    match authority:
        case MemberAuthority(member_id):
            return member_id
        case WorkspaceAuthority():
            return None
        case _:
            raise TypeError("execution authority must be MemberAuthority or WorkspaceAuthority")


def turn_authority(
    speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None
) -> ExecutionAuthority:
    """Decode a turn's speaker or delegated member into its one execution authority."""
    if speaker_member_id is not None and on_behalf_of_member_id is not None:
        raise ValueError("a turn cannot carry both speaker and delegated member authority")
    return authority_from_member_id(
        speaker_member_id if speaker_member_id is not None else on_behalf_of_member_id
    )
