import pytest

from ufo.harness.tools import dispatch_segments


def test_segments_safe_runs_around_ordered_barriers() -> None:
    items = ("safe:1", "safe:2", "barrier:1", "safe:3", "barrier:2")

    segments = tuple(
        dispatch_segments(items, parallel_safe=lambda item: item.startswith("safe:"), limit=8)
    )

    assert segments == (("safe:1", "safe:2"), ("barrier:1",), ("safe:3",), ("barrier:2",))


def test_segments_split_a_safe_run_at_the_parallel_limit() -> None:
    segments = tuple(dispatch_segments(tuple(range(7)), parallel_safe=lambda _item: True, limit=3))

    assert segments == ((0, 1, 2), (3, 4, 5), (6,))


def test_segments_reject_a_non_positive_parallel_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        tuple(dispatch_segments((1,), parallel_safe=lambda _item: True, limit=0))
