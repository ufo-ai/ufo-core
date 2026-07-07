"""What the brief-pipeline extension declares: three typed subagent stages and the skill that
teaches the parent agent to chain them — outline piped into draft piped into critic, all
foreground, with the parent applying the critique itself."""

from pathlib import Path

from ufo.sdk.manifest import Manifest, SkillSpec
from ufo_ext_brief_pipeline.pipeline import CRITIC_PROFILE, DRAFT_PROFILE, OUTLINE_PROFILE

NAME = "brief_pipeline"
VERSION = "0.1.0"
SKILL_DIR = Path(__file__).parent / "skills" / "brief-pipeline"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        subagents=(OUTLINE_PROFILE, DRAFT_PROFILE, CRITIC_PROFILE),
        skills=(SkillSpec(path=SKILL_DIR),),
    )
