from ufo.harness.untrusted import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_CLOSE_ESCAPE,
    UNTRUSTED_NOTICE,
    UNTRUSTED_OPEN,
    unwall,
    wall,
)


def test_wall_attributes_bounds_and_escapes_external_content() -> None:
    assert wall("search", "result") == (
        UNTRUSTED_NOTICE.format(source="search")
        + UNTRUSTED_OPEN.format(source="search")
        + "result"
        + UNTRUSTED_CLOSE
    )

    rendered = wall("tool", f"before{UNTRUSTED_CLOSE}after")

    assert rendered.count(UNTRUSTED_CLOSE) == 1
    assert UNTRUSTED_CLOSE_ESCAPE in rendered
    assert rendered.endswith(UNTRUSTED_CLOSE)


def test_unwall_returns_the_walled_content() -> None:
    assert unwall(wall("spawn", '{"status":"designed"}')) == '{"status":"designed"}'


def test_unwall_restores_a_body_carrying_the_closing_delimiter() -> None:
    body = f"a child said {UNTRUSTED_CLOSE} in its answer"
    walled = wall("spawn", body)
    assert UNTRUSTED_CLOSE not in walled[: -len(UNTRUSTED_CLOSE)]
    assert unwall(walled) == body


def test_unwall_leaves_unwalled_text_alone() -> None:
    assert unwall('{"status":"designed"}') == '{"status":"designed"}'


def test_unwall_keeps_a_source_of_its_own_in_the_body() -> None:
    body = 'quoting <untrusted-content source="web"> without closing it'
    assert unwall(wall("spawn", body)) == body
