"""The one rule every member-owned kind answers "who sees this row" from."""

from uuid import UUID

import pytest

from ufo.runtime.objects import ObjectOwner, readable
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)

ALICE = UUID(int=1)
BOB = UUID(int=2)


@pytest.mark.parametrize(
    ("audience", "owner", "reader", "admin", "disclosed", "expected"),
    (
        (SHARED_AUDIENCE, ALICE, BOB, False, False, True),
        (SHARED_AUDIENCE, None, None, False, False, True),
        (conversation_audience(ALICE), ALICE, ALICE, False, False, True),
        (conversation_audience(ALICE), None, ALICE, False, False, True),
        (conversation_audience(ALICE), BOB, BOB, False, False, True),
        (conversation_audience(ALICE), ALICE, BOB, False, False, False),
        (conversation_audience(ALICE), ALICE, None, False, False, False),
        (conversation_audience(ALICE), ALICE, BOB, True, False, True),
        (conversation_audience(ALICE), ALICE, BOB, False, True, True),
        (conversation_audience(ALICE), ALICE, BOB, False, False, False),
        (room_audience("slack", "C1"), ALICE, BOB, False, False, False),
        (room_audience("slack", "C1"), ALICE, ALICE, False, False, True),
        (room_audience("slack", "C1"), ALICE, None, False, False, False),
        (foreign_room_audience("slack", "C9"), ALICE, BOB, False, False, False),
        (foreign_room_audience("slack", "C9"), ALICE, BOB, True, False, True),
        (None, ALICE, ALICE, False, False, True),
        (None, ALICE, BOB, False, False, False),
        (None, None, BOB, True, False, True),
    ),
    ids=(
        "shared-other-member",
        "shared-no-reader",
        "own-audience-own-row",
        "own-audience-nobodys-row",
        "creator-outside-the-audience",
        "other-members-private-row",
        "private-row-no-reader",
        "admin-row",
        "admin-content-disclosed",
        "admin-content-undisclosed",
        "room-other-member",
        "room-own-row",
        "room-no-reader",
        "foreign-other-member",
        "foreign-admin-row",
        "no-audience-own-row",
        "no-audience-other-member",
        "no-audience-admin-row",
    ),
)
def test_readable_answers_from_the_audience_the_owner_and_the_admin_bit(
    audience: Audience | None,
    owner: UUID | None,
    reader: UUID | None,
    admin: bool,
    disclosed: bool,
    expected: bool,
) -> None:
    owned = ObjectOwner(member_id=owner, audience=audience)

    assert readable(owned, reader, admin=admin, disclosed=disclosed) is expected
