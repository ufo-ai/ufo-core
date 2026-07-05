"""The skills-authoring extension: it contributes the `create-skill` skill, the workflow that walks
authoring an Agent Skill in the workspace and persisting it with the core `save_custom_skill` tool.

The tool itself is a core builtin — it feeds the core skill registry — so this extension ships only
the teaching skill. A deploy that wants member-driven skill authoring installs this one small
member; the tool is always present regardless."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, SkillSpec

NAME = "skills"
VERSION = "0.1.0"
SKILLS_DIR = Path(__file__).parent / "skills"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        skills=(SkillSpec(path=SKILLS_DIR / "create-skill"),),
    )
