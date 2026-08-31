from ufo.harness.untrusted import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_CLOSE_ESCAPE,
    UNTRUSTED_NOTICE,
    UNTRUSTED_OPEN,
    wall,
)


def test_wall_attributes_and_bounds_external_content() -> None:
    assert wall("search", "result") == (
        UNTRUSTED_NOTICE.format(source="search")
        + UNTRUSTED_OPEN.format(source="search")
        + "result"
        + UNTRUSTED_CLOSE
    )


def test_wall_cannot_be_closed_by_its_content() -> None:
    rendered = wall("tool", f"before{UNTRUSTED_CLOSE}after")

    assert rendered.count(UNTRUSTED_CLOSE) == 1
    assert UNTRUSTED_CLOSE_ESCAPE in rendered
    assert rendered.endswith(UNTRUSTED_CLOSE)
