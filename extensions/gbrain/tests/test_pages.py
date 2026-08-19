"""The markdown page logic both gbrain backends share: the path filter, the utf-8 gate, and the
frontmatter/heading/path title chain — pure functions, no transport, no DB. No conftest: the shared
`ufo_testsupport` plugin covers fixtures."""

import re

import pytest
from ufo_ext_gbrain.pages import PAGE_STREAM, decoded, is_markdown_path, markdown_page

from ufo.sdk.sources import StreamFault

SOURCE_REF = "docs/guide.md"


@pytest.mark.parametrize(
    ("relpath", "expected"),
    [
        ("notes.md", True),
        ("README.MD", True),
        ("guide.markdown", True),
        ("docs/nested/deep.md", True),
        ("logo.png", False),
        (".hidden.md", False),
        (".git/config.md", False),
        ("docs/.obsidian/note.md", False),
        ("docs/../escape.md", False),
    ],
)
def test_is_markdown_path(relpath: str, expected: bool) -> None:
    assert is_markdown_path(relpath) is expected


def test_decoded_returns_utf8_text() -> None:
    assert decoded(SOURCE_REF, "héllo".encode()) == "héllo"


def test_decoded_fault_names_the_file() -> None:
    with pytest.raises(StreamFault, match=re.escape("docs/bad.md is not utf-8 text")):
        decoded("docs/bad.md", b"\xff\xfe")


def test_frontmatter_title_outranks_the_heading() -> None:
    page = markdown_page(SOURCE_REF, "---\ntitle: Guide\n---\n\n# Other\n\nBody.\n")
    assert page.title == "Guide"
    assert page.body == "# Other\n\nBody.\n"
    assert page.stream == PAGE_STREAM
    assert page.source_ref == SOURCE_REF


def test_first_heading_titles_without_frontmatter() -> None:
    page = markdown_page(SOURCE_REF, "intro\n\n# First\n\n# Second\n")
    assert page.title == "First"
    assert page.body == "intro\n\n# First\n\n# Second\n"


def test_path_titles_without_frontmatter_or_heading() -> None:
    assert markdown_page(SOURCE_REF, "just prose\n").title == SOURCE_REF


def test_frontmatter_without_title_falls_to_the_heading() -> None:
    page = markdown_page(SOURCE_REF, "---\ntags: [a]\n---\n# From Heading\n")
    assert page.title == "From Heading"
    assert page.body == "# From Heading\n"


def test_malformed_frontmatter_is_content() -> None:
    text = "---\ntitle: [unclosed\n---\nBody.\n"
    page = markdown_page(SOURCE_REF, text)
    assert page.title == SOURCE_REF
    assert page.body == text


def test_unterminated_frontmatter_is_content() -> None:
    text = "---\ntitle: Lost\nno terminator\n"
    page = markdown_page(SOURCE_REF, text)
    assert page.title == SOURCE_REF
    assert page.body == text


def test_dots_terminate_frontmatter() -> None:
    page = markdown_page(SOURCE_REF, "---\ntitle: Dots\n...\nBody.\n")
    assert page.title == "Dots"
    assert page.body == "Body.\n"


def test_empty_file_titles_from_the_path() -> None:
    page = markdown_page(SOURCE_REF, "")
    assert page.title == SOURCE_REF
    assert page.body == ""
