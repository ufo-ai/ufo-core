import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parents[2] / "docs" / "handbook" / "strip_dead_links.py"
_SPEC = importlib.util.spec_from_file_location("strip_dead_links", _PATH)
strip = importlib.util.module_from_spec(_SPEC)
sys.modules["strip_dead_links"] = strip
_SPEC.loader.exec_module(strip)

EXISTING = frozenset({"stage-1.md", "stage-9.2.md"})


def test_keeps_links_to_existing_pages():
    text = "See [Turn loop](stage-9.2.md) and [Boot](stage-1.md)."
    assert strip.unlink_dangling(text, EXISTING) == text


def test_unlinks_dangling_leaving_label():
    text = "See [Teardown](stage-14.md) now."
    assert strip.unlink_dangling(text, EXISTING) == "See Teardown now."


def test_mixed_line_unlinks_only_dangling():
    text = "[A](stage-1.md), [B](stage-14.2.md), [C](stage-9.2.md)"
    assert strip.unlink_dangling(text, EXISTING) == "[A](stage-1.md), B, [C](stage-9.2.md)"


def test_heading_and_its_prose_survive_no_orphan():
    text = "## [Gone](stage-99.md) `stage-99` — 0 files\n\nDescription paragraph.\n"
    expected = "## Gone `stage-99` — 0 files\n\nDescription paragraph.\n"
    assert strip.unlink_dangling(text, EXISTING) == expected


def test_non_stage_links_untouched():
    text = "[overview](overview.md), [register](register.md), [ext](https://x/stage-1.md)"
    assert strip.unlink_dangling(text, EXISTING) == text
