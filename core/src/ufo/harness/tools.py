from collections.abc import Callable, Iterator


def dispatch_segments[ItemT](
    items: tuple[ItemT, ...], *, parallel_safe: Callable[[ItemT], bool], limit: int
) -> Iterator[tuple[ItemT, ...]]:
    """Preserve call order while batching consecutive parallel-safe calls up to `limit`."""
    if limit < 1:
        raise ValueError("parallel dispatch limit must be positive")
    segment: list[ItemT] = []
    for item in items:
        if parallel_safe(item):
            if len(segment) == limit:
                yield tuple(segment)
                segment = []
            segment.append(item)
            continue
        if segment:
            yield tuple(segment)
            segment = []
        yield (item,)
    if segment:
        yield tuple(segment)
