"""The exact disclosure audience carried from a conversation into its turns."""

from typing import NewType
from uuid import UUID

from ufo.turns.subjects import MEMBER_SUBJECT_PREFIX, SHARED_SUBJECT

Audience = NewType("Audience", str)
SHARED_AUDIENCE = Audience(SHARED_SUBJECT)
ROOM_AUDIENCE_PREFIX = "room:"
FOREIGN_AUDIENCE_PREFIX = "foreign:"


def conversation_audience(member_id: UUID | None) -> Audience:
    return SHARED_AUDIENCE if member_id is None else Audience(f"{MEMBER_SUBJECT_PREFIX}{member_id}")


def room_audience(surface: str, room: str) -> Audience:
    return _room_audience(ROOM_AUDIENCE_PREFIX, surface, room)


def foreign_room_audience(surface: str, room: str) -> Audience:
    return _room_audience(FOREIGN_AUDIENCE_PREFIX, surface, room)


def _room_audience(prefix: str, surface: str, room: str) -> Audience:
    if not surface or ":" in surface or not room or ":" in room:
        raise ValueError("audience surface and room must be nonempty and contain no colon")
    return Audience(f"{prefix}{surface}:{room}")


def parse_audience(value: str) -> Audience:
    audience = Audience(value)
    if audience == SHARED_AUDIENCE:
        return audience
    prefix, separator, remainder = value.partition(":")
    if not separator:
        raise ValueError(f"invalid conversation audience {value!r}")
    if prefix == "member":
        try:
            member_id = UUID(remainder)
        except ValueError as error:
            raise ValueError(f"invalid conversation audience {value!r}") from error
        if audience != conversation_audience(member_id):
            raise ValueError(f"invalid conversation audience {value!r}")
        return audience
    surface, separator, room = remainder.partition(":")
    if prefix not in {"room", "foreign"} or not separator:
        raise ValueError(f"invalid conversation audience {value!r}")
    expected = (
        room_audience(surface, room) if prefix == "room" else foreign_room_audience(surface, room)
    )
    if audience != expected:
        raise ValueError(f"invalid conversation audience {value!r}")
    return audience


def readable_audiences(member_id: UUID) -> tuple[Audience, ...]:
    """Every conversation audience whose content this member reads: the workspace-shared one and
    their own. A room and an externally-shared channel are absent by construction — the workspace
    holds no fact about who is in either — so this is the one definition every member-facing read
    answers from, whether it lists conversations or the objects anchored to them."""
    return (SHARED_AUDIENCE, conversation_audience(member_id))


def audience_member(audience: Audience) -> UUID | None:
    parsed = parse_audience(audience)
    if not parsed.startswith(MEMBER_SUBJECT_PREFIX):
        return None
    return UUID(parsed.removeprefix(MEMBER_SUBJECT_PREFIX))


def audience_subjects(audience: Audience) -> frozenset[str]:
    """The subjects a conversation of this audience may read. An externally-shared room reads only
    itself — never the workspace-shared atom — so nothing internal is ever recalled into a channel
    another organization sits in."""
    parsed = parse_audience(audience)
    if parsed.startswith(FOREIGN_AUDIENCE_PREFIX):
        return frozenset({parsed})
    return frozenset({SHARED_SUBJECT, parsed})


def narrow_audience(current: Audience, requested: Audience) -> Audience:
    current = parse_audience(current)
    requested = parse_audience(requested)
    if current == requested or requested == SHARED_AUDIENCE:
        return current
    if current == SHARED_AUDIENCE:
        return requested
    current_kind, _, current_key = current.partition(":")
    requested_kind, _, requested_key = requested.partition(":")
    if (
        current_kind in {"room", "foreign"}
        and requested_kind in {"room", "foreign"}
        and current_key == requested_key
    ):
        return current if current_kind == "foreign" else requested
    raise ValueError(f"conversation audience changed from {current!r} to {requested!r}")
