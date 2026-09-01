"""The documents pack's contributed skills flow through the loader: each parses, indexes, and its
`depends` closure resolves — proving the ported content is well-formed against the live parser, and
that a skill-name collision across packs is refused where the registry is built."""

import re

import pytest
import ufo_ext_documents.manifest as documents

from ufo.host.ext.loader import skill_registry

DESIGN_FOUNDATIONS_DEPENDENTS = ("office-docx", "office-pptx", "pdf", "theme-factory")
HOUSE_STYLE = "ufo-style"
PHRASE_LENGTHS = (3, 4, 5)
FINANCE_SPECIALIZATION = "specializations/finance.md"
QUERY_FORMULATION = re.compile(r"^### Query Formulation$(.*?)^### ", re.M | re.S)
PERIOD_IN_AN_EXAMPLE = re.compile(r"\b(19|20)\d{2}\b|\{year\}")
WINDOW_FILTERS_PUBLICATION = re.compile(r"published[^.]*not the period it reports")


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _phrases(text: str) -> set[str]:
    words = _words(text).split()
    return {
        " ".join(words[start : start + length])
        for length in PHRASE_LENGTHS
        for start in range(len(words) - length + 1)
    }


def test_documents_skills_parse_and_index() -> None:
    index = dict(skill_registry((documents.manifest(),)).index())
    for name in (
        "design-foundations",
        "document-review",
        "office-docx",
        "office-pptx",
        "office-xlsx",
        "pdf",
        "theme-factory",
        "writing-drafts",
    ):
        assert name in index


def test_design_foundations_defaults_to_the_house_style_and_says_what_overrides_it() -> None:
    """An artifact nobody gave a style direction for is drawn in the house palette, so
    `design-foundations` pulls `ufo-style` and every skill that builds on it inherits that default.
    The member's own direction has to stay the stated exception, or the default becomes a mandate
    that repaints a member's own brand."""
    registry = skill_registry((documents.manifest(),))
    assert [ref.card.name for ref in registry.closure("design-foundations")] == [
        "design-foundations",
        HOUSE_STYLE,
    ]
    body = registry.named("design-foundations").instructions
    assert HOUSE_STYLE in body
    assert "wins over the house style" in body


def test_document_review_puts_the_period_it_checks_in_the_query_text() -> None:
    """The published-date window filters on when a page was published, so bounding it to the year a
    figure covers drops the sources that report that year: the FY2023 filing behind this skill's own
    evidence example is filed in Feb 2024. Phase 3 has to send the period into the query sentence,
    and say what the window actually filters on, or every dated claim comes back inconclusive."""
    review = skill_registry((documents.manifest(),)).named("document-review")
    assert WINDOW_FILTERS_PUBLICATION.search(review.instructions)
    year_selection = next(
        line for line in review.instructions.splitlines() if "**Year selection**" in line
    )
    assert PERIOD_IN_AN_EXAMPLE.search(year_selection), year_selection

    finance = dict(review.files)[FINANCE_SPECIALIZATION].decode()
    (formulation,) = QUERY_FORMULATION.findall(finance)
    bullets = [line for line in formulation.splitlines() if line.startswith("- ")]
    assert len(bullets) == 6
    for bullet in bullets:
        assert PERIOD_IN_AN_EXAMPLE.search(bullet), bullet


def test_document_review_bundles_its_annotation_scripts() -> None:
    review = skill_registry((documents.manifest(),)).named("document-review")
    bundled = {path for path, _ in review.files}
    assert "scripts/manage_state.py" in bundled
    assert "scripts/annotate_pdf.py" in bundled


def test_pdf_bundles_its_scripts_and_library_docs() -> None:
    pdf = skill_registry((documents.manifest(),)).named("pdf")
    bundled = {path for path, _ in pdf.files}
    assert "render.py" in bundled
    assert "libraries/reportlab.md" in bundled


def test_a_skill_name_collision_across_packs_is_refused() -> None:
    manifest = documents.manifest()
    with pytest.raises(ValueError, match="already registered"):
        skill_registry((manifest, manifest))
