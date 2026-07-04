"""What the documents pack contributes: document-production skills the agent loads on demand.

Each skill is a folder under `skills/` — its `SKILL.md` workflow plus the scripts and assets it
references — that the loader parses into the loadable-skill registry and mounts into the sandbox
under `.skills/<name>/` when `load_skill` resolves it. `office-docx`, `office-pptx`, `pdf`, and
`theme-factory` build on `design-foundations`, the shared visual baseline, which each names in its
`depends` so loading any of them pulls it too. `document-review` reviews any of the office formats,
loading their skills at runtime to annotate."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, SkillSpec

NAME = "documents"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = (
    "design-foundations",
    "document-review",
    "office-docx",
    "office-pptx",
    "office-xlsx",
    "pdf",
    "theme-factory",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
