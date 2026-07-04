"""The documents pack's contributed skills flow through the loader: each parses, indexes, and its
`depends` closure resolves — proving the ported content is well-formed against the live parser, and
that a skill-name collision across packs is refused where the registry is built."""

import pytest
import selfhost_ext_documents.manifest as documents

from selfhost.ext.loader import skill_registry


def test_documents_skills_parse_and_index() -> None:
    index = dict(skill_registry((documents.manifest(),)).index())
    for name in ("design-foundations", "office-docx", "pdf"):
        assert name in index


def test_pdf_pulls_its_design_foundations_dependency() -> None:
    registry = skill_registry((documents.manifest(),))
    assert [skill.name for skill in registry.tree("pdf")] == ["design-foundations", "pdf"]


def test_pdf_bundles_its_scripts_and_library_docs() -> None:
    pdf = skill_registry((documents.manifest(),)).named("pdf")
    bundled = {path for path, _ in pdf.files}
    assert "render.py" in bundled
    assert "libraries/reportlab.md" in bundled


def test_a_skill_name_collision_across_packs_is_refused() -> None:
    manifest = documents.manifest()
    with pytest.raises(ValueError, match="already registered"):
        skill_registry((manifest, manifest))
