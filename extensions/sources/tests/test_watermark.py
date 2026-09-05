import pytest
from ufo_ext_sources.watermark import integer_checkpoint, text_checkpoint

from ufo.sdk.sources import StreamSpec


@pytest.mark.parametrize(
    ("cursor", "values", "expected"),
    [(None, ["a", "c", "b"], "c"), ("z", ["a"], "z"), (None, [None, True], None)],
)
def test_text_checkpoint(cursor, values, expected) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="modified")
    assert text_checkpoint(stream, [{"modified": value} for value in values], cursor) == expected


@pytest.mark.parametrize(
    ("cursor", "values", "expected"),
    [
        ("998", [999, 1000], "1000"),
        ("1000", ["999"], "1000"),
        ("9007199254740992", [9007199254740993], "9007199254740993"),
        (None, [None, True], None),
    ],
)
def test_integer_checkpoint(cursor, values, expected) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="modified")
    assert integer_checkpoint(stream, [{"modified": value} for value in values], cursor) == expected


@pytest.mark.parametrize("checkpoint", [text_checkpoint, integer_checkpoint])
def test_stream_without_watermark_preserves_opaque_cursor(checkpoint) -> None:
    stream = StreamSpec(name="items", source_object="items")
    assert checkpoint(stream, [{"modified": 1000}], "encoded:checkpoint") == "encoded:checkpoint"
