"""The markdown page logic both gbrain backends share: the path filter, the utf-8 gate, and the
frontmatter/heading/path title chain — pure functions, no transport, no DB. No conftest: the shared
`ufo_testsupport` plugin covers fixtures."""

import re

import pytest
from ufo_ext_gbrain.pages import decoded, is_markdown_path, markdown_page

from ufo.sdk.sources import StreamFault

SOURCE_REF = "docs/guide.md"


def test_is_markdown_path() -> None:
    cases = (
        ("notes.md", True),
        ("README.MD", True),
        ("guide.markdown", True),
        ("docs/nested/deep.md", True),
        ("logo.png", False),
        (".hidden.md", False),
        (".git/config.md", False),
        ("docs/.obsidian/note.md", False),
        ("docs/../escape.md", False),
    )
    for relpath, expected in cases:
        assert is_markdown_path(relpath) is expected


def test_decoded_fault_names_the_file() -> None:
    with pytest.raises(StreamFault, match=re.escape("docs/bad.md is not utf-8 text")):
        decoded("docs/bad.md", b"\xff\xfe")


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


def test_empty_file_titles_from_the_path() -> None:
    page = markdown_page(SOURCE_REF, "")
    assert page.title == SOURCE_REF
    assert page.body == ""
