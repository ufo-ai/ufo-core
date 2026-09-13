"""What every connector test needs to drive a declared stream: the reader of landed parent records
the sync driver threads into a run.

A provider test stands the provider in and asserts the pages the adapter lands, so the one thing it
cannot stand in for is the seam's own shape — a child stream is driven with a `ParentPages`, and a
test that built its own would be asserting against its own idea of one. It arrives as a fixture
rather than an import, because a `conftest` is importable under that bare name from every directory
holding one and the winner is whichever pytest put on the path first."""

from collections.abc import AsyncIterator, Callable, Mapping

import pytest

from ufo.sdk.sources import ParentPages, ParentRecord

Landed = Mapping[str, tuple[ParentRecord, ...]]


@pytest.fixture
def parents_reader() -> Callable[[Landed], ParentPages]:
    """Builds the reader the driver hands a run, over a canned set of landed parent pages. A stream
    the set does not name has landed nothing, which is what a parent row that has not synced yet
    answers."""

    def reader(landed: Landed) -> ParentPages:
        async def read(stream: str) -> AsyncIterator[ParentRecord]:
            for record in landed.get(stream, ()):
                yield record

        return read

    return reader
