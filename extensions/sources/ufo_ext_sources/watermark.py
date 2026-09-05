from typing import Any

from ufo.sdk.sources import StreamSpec


def text_checkpoint(
    stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
) -> str | None:
    """Return the greatest text watermark, including the stored cursor."""
    if stream.cursor_field is None:
        return cursor
    values = [cursor] if cursor is not None else []
    values.extend(
        str(value)
        for record in records
        if isinstance(value := record.get(stream.cursor_field), (str, int))
        and not isinstance(value, bool)
    )
    return max(values, default=None)


def integer_checkpoint(
    stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
) -> str | None:
    """Return the greatest integer watermark as decimal text."""
    if stream.cursor_field is None:
        return cursor
    values = [cursor] if cursor is not None else []
    values.extend(
        str(value)
        for record in records
        if isinstance(value := record.get(stream.cursor_field), (str, int))
        and not isinstance(value, bool)
    )
    return max(values, key=int, default=None)
