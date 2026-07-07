"""The documents pack's contributed skills flow through the loader: each parses, indexes, and its
`depends` closure resolves — proving the ported content is well-formed against the live parser, and
that a skill-name collision across packs is refused where the registry is built."""

import pytest
import ufo_ext_documents.manifest as documents

from ufo.ext.loader import skill_registry


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
    ):
        assert name in index


def test_pdf_pulls_its_design_foundations_dependency() -> None:
    registry = skill_registry((documents.manifest(),))
    assert [skill.name for skill in registry.tree("pdf")] == ["design-foundations", "pdf"]


def test_office_pptx_and_theme_factory_pull_design_foundations() -> None:
    registry = skill_registry((documents.manifest(),))
    for name in ("office-pptx", "theme-factory"):
        assert [skill.name for skill in registry.tree(name)] == ["design-foundations", name]


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
