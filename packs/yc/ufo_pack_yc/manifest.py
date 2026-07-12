"""The YC founder pack."""

from pathlib import Path

from ufo.sdk.manifest import Pack, SkillSpec

NAME = "yc"
VERSION = "0.1.0"
EXTENSIONS = (
    "yc_cli",
    "ufo",
    "memory",
    "index_default",
    "embed_openai",
    "knowledge_graph",
    "documents",
    "scheduled_tasks",
    "todos",
)
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("founder-operations", "company-diligence")


def pack() -> Pack:
    return Pack(
        name=NAME,
        version=VERSION,
        extensions=EXTENSIONS,
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
