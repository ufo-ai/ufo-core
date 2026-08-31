"""The portal's shared keyset paging: the cursor's wire format and the page arithmetic every
listing reuses. The memory listing proves this against a real database; these pin the primitive
itself, so a second consumer inherits stated behaviour rather than a coincidence."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ufo.runtime.listings import ListingCursor, MalformedCursor, page_of

STAMP = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
ITEM = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@dataclass(frozen=True)
class Row:
    created_at: datetime
    id: str
    body: str


def _rows(count: int) -> list[Row]:
    """Newest first, one minute apart — the order `page_query` hands `page_of`."""
    return [
        Row(created_at=STAMP - timedelta(minutes=index), id=f"{index}", body=f"row {index}")
        for index in range(count)
    ]


def _page(rows: list[Row], cursor: ListingCursor | None, limit: int):
    return page_of(
        rows,
        cursor,
        limit,
        render=lambda row: row.body,
        position=lambda row: (row.created_at, row.id),
    )


def test_a_cursor_round_trips_through_its_token() -> None:
    for cursor in (
        ListingCursor(created_at=STAMP, item_id=ITEM),
        ListingCursor(created_at=STAMP, item_id=ITEM, newer=True),
    ):
        assert ListingCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize(
    "token",
    [
        "",
        "garbage",
        f"sideways|{STAMP.isoformat()}|{ITEM}",
        f"older|not-a-timestamp|{ITEM}",
        f"older|{STAMP.isoformat()}|not-a-uuid",
        f"older|{STAMP.isoformat()}",
        f"{STAMP.isoformat()}|{ITEM}",
    ],
)
def test_a_token_naming_no_position_is_refused(token: str) -> None:
    """Fail loud: a surface answers its client's error rather than serving some other page."""
    with pytest.raises(MalformedCursor):
        ListingCursor.decode(token)


def test_the_first_page_offers_only_older() -> None:
    page = _page(_rows(4), None, 3)
    assert page.rows == ("row 0", "row 1", "row 2")
    assert page.newer is None
    assert page.older == ListingCursor(
        created_at=STAMP - timedelta(minutes=2), item_id="2", newer=False
    )


def test_a_full_last_page_offers_no_older() -> None:
    """The row past the limit is the only thing that distinguishes these — without it a listing
    whose last page is exactly full offers an Older control onto nothing."""
    exact = _page(_rows(3), None, 3)
    assert exact.rows == ("row 0", "row 1", "row 2")
    assert exact.older is None


def test_a_page_reached_by_a_cursor_offers_the_way_back() -> None:
    walked = ListingCursor(created_at=STAMP, item_id=ITEM, newer=False)
    page = _page(_rows(2), walked, 3)
    assert page.older is None
    assert page.newer == ListingCursor(created_at=STAMP, item_id="0", newer=True)


def test_walking_newer_reverses_the_page_back_into_recency_order() -> None:
    """`page_query` inverts its ordering to walk toward newer rows, so the rows arrive
    oldest-first and the page hands them back newest-first — the order every listing renders."""
    ascending = list(reversed(_rows(3)))
    page = _page(ascending, ListingCursor(created_at=STAMP, item_id=OTHER, newer=True), 3)
    assert page.rows == ("row 0", "row 1", "row 2")
    assert page.older == ListingCursor(
        created_at=STAMP - timedelta(minutes=2), item_id="2", newer=False
    )


def test_an_empty_page_offers_no_control_at_all() -> None:
    page = _page([], ListingCursor(created_at=STAMP, item_id=ITEM), 3)
    assert page.rows == ()
    assert page.older is None
    assert page.newer is None
