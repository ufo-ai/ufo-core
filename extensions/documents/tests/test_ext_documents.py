"""The documents pack's contributed skills flow through the loader: each parses, indexes, and its
`depends` closure resolves — proving the ported content is well-formed against the live parser, and
that a skill-name collision across packs is refused where the registry is built."""

import re

import pytest
import ufo_ext_documents.manifest as documents

from ufo.ext.loader import skill_registry
from ufo.sandbox.session import WORKSPACE_DIR
from ufo.skills.runtime import SKILL_MD, mount_skill

DESIGN_FOUNDATIONS_DEPENDENTS = ("office-docx", "office-pptx", "pdf", "theme-factory")
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


def test_pdf_pulls_its_design_foundations_dependency() -> None:
    registry = skill_registry((documents.manifest(),))
    assert [ref.card.name for ref in registry.closure("pdf")] == ["pdf", "design-foundations"]


def test_office_pptx_and_theme_factory_pull_design_foundations() -> None:
    registry = skill_registry((documents.manifest(),))
    for name in ("office-pptx", "theme-factory"):
        closure = [ref.card.name for ref in registry.closure(name)]
        assert closure == [name, "design-foundations"]


@pytest.mark.parametrize("dependent", DESIGN_FOUNDATIONS_DEPENDENTS)
async def test_loading_a_dependent_mounts_every_file_in_its_closure(dependent: str) -> None:
    """The split moved the palette, type and chart guidance into `references/`, and each dependent
    cites those files by mounted path. Every load has to put all three on disk alongside every file
    the dependent brings itself, or the workflow it injects points at nothing."""
    registry = skill_registry((documents.manifest(),))
    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    loaded = await registry.materialize(registry.closure(dependent))
    for entry in loaded:
        await mount_skill(_Sandbox(), entry.skill)

    for reference in ("color", "typography", "dataviz"):
        assert f"/workspace/.skills/design-foundations/references/{reference}.md" in written
    expected = {
        f"{entry.skill.mount_root()}/{path}"
        for entry in loaded
        for path in entry.skill.mounted_files()
    }
    assert written.keys() == expected


async def test_every_design_foundations_path_a_dependent_cites_is_one_it_mounts() -> None:
    """A citation naming a file that the load does not mount is a dead end the agent cannot follow.
    Every `.skills/design-foundations/...` path written in a dependent's own files must resolve to a
    path that dependent's closure actually mounts."""
    registry = skill_registry((documents.manifest(),))
    for name in DESIGN_FOUNDATIONS_DEPENDENTS:
        mounted = {
            f"{entry.skill.mount_root()}/{path}"
            for entry in await registry.materialize(registry.closure(name))
            for path in entry.skill.mounted_files()
        }
        skill = registry.named(name)
        sources = {SKILL_MD: skill.raw_skill_md.encode(), **dict(skill.files)}
        for source, content in sources.items():
            if not source.endswith(".md"):
                continue
            for cited in re.findall(r"\.skills/design-foundations/[\w./-]+\.md", content.decode()):
                assert f"{WORKSPACE_DIR}/{cited}" in mounted, f"{name}:{source} cites {cited}"


def test_the_references_table_routes_each_topic_to_the_file_that_holds_it() -> None:
    """`design-foundations` sends the agent to one of three reference files by what a row claims it
    covers, so a row pointing at the wrong file is a silent misroute that no mount or citation check
    can see. Every phrase in a row that occurs in exactly one reference file must occur in that
    row's own file — swapping two rows puts each one's phrases in the other and fails here."""
    skill = skill_registry((documents.manifest(),)).named("design-foundations")
    bodies = {
        path.removeprefix("references/"): _words(content.decode())
        for path, content in skill.files
        if path.startswith("references/")
    }
    assert set(bodies) == {"color.md", "typography.md", "dataviz.md"}

    rows = re.findall(r"^\|\s*`references/([\w.-]+)`\s*\|([^|]*)\|", skill.instructions, re.M)
    assert {name for name, _ in rows} == set(bodies)

    for name, covers in rows:
        located = {}
        for phrase in _phrases(covers):
            holders = {f for f, body in bodies.items() if phrase in body}
            if len(holders) == 1:
                located[phrase] = holders.pop()
        assert located, f"the {name} row claims nothing that identifies a reference file"
        misrouted = {p: holder for p, holder in located.items() if holder != name}
        assert not misrouted, f"the {name} row claims text that lives elsewhere: {misrouted}"


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
