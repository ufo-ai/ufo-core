from uuid import uuid4

import pytest

from ufo.turns.audience import (
    SHARED_AUDIENCE,
    audience_member,
    audience_subjects,
    conversation_audience,
    foreign_room_audience,
    narrow_audience,
    parse_audience,
    room_audience,
)


def test_audience_atoms_round_trip() -> None:
    member = uuid4()
    audiences = (
        SHARED_AUDIENCE,
        conversation_audience(member),
        room_audience("slack", "C123"),
        foreign_room_audience("slack", "C456"),
    )

    assert tuple(parse_audience(audience) for audience in audiences) == audiences
    assert audience_member(conversation_audience(member)) == member
    assert audience_member(room_audience("slack", "C123")) is None


@pytest.mark.parametrize(
    "value",
    ("", "member", "member:nope", "room:slack", "room::C1", "foreign:slack:", "other:x:y"),
)
def test_invalid_audience_atoms_fail_loud(value: str) -> None:
    with pytest.raises(ValueError, match="audience"):
        parse_audience(value)


def test_foreign_audience_is_sealed_from_shared_subjects() -> None:
    room = room_audience("slack", "C1")
    foreign = foreign_room_audience("slack", "C1")

    assert audience_subjects(room) == frozenset({"shared", "room:slack:C1"})
    assert audience_subjects(foreign) == frozenset({"foreign:slack:C1"})
    assert narrow_audience(SHARED_AUDIENCE, room) == room
    assert narrow_audience(room, foreign) == foreign
    assert narrow_audience(foreign, SHARED_AUDIENCE) == foreign


def test_audience_cannot_move_between_rooms() -> None:
    with pytest.raises(ValueError, match="changed"):
        narrow_audience(room_audience("slack", "C1"), room_audience("slack", "C2"))
