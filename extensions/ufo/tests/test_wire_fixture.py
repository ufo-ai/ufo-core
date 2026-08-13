"""The wire fixture is generated from the real producers and checked in where the clients read
it, so a directive-wire change is a fixture diff every client's own suite replays."""

from ufo_testsupport.wire_fixture import (
    FIXTURE_PATH,
    ONBOARD_WIRE,
    WORKSPACE_WIRE,
    fixture_rows,
    rendered,
)


def test_fixture_is_fresh() -> None:
    assert FIXTURE_PATH.read_text() == rendered(), (
        "stale wire fixture; regenerate: uv run python -m ufo_testsupport.wire_fixture"
    )


def test_fixture_covers_both_wires() -> None:
    workspace = {row["verb"] for row in fixture_rows() if row["wire"] == "workspace"}
    onboard = {row["verb"] for row in fixture_rows() if row["wire"] == "onboard"}
    assert workspace == WORKSPACE_WIRE
    assert onboard == ONBOARD_WIRE


def test_fixture_lines_carry_the_producers_escaping() -> None:
    for row in fixture_rows():
        assert "\n" not in row["line"]
        if any("\n" in field for field in row["fields"]):
            assert "\\n" in row["line"]
        if any("\t" in field for field in row["fields"]):
            assert "\\t" in row["line"]
