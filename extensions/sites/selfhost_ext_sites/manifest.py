"""What the sites pack declares: the website tools, the website-building subagent profile, and the
website-building skills the agent loads before building.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can build and serve a site and `spawn_subagent("website_building", ...)`
runs a site-building child scoped to them. Its prompt section teaches the main agent to serve and
validate a site before handing it back. The skills join the loadable set: `website-building` routes
by project type and carries the shared design system; its two children — `website-building-webapp`
(fullstack template) and `website-building-game` (Three.js/2D Canvas) — depend on it."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, PromptSection, SkillSpec
from selfhost_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from selfhost_ext_sites.tools import SITES_TOOLS

NAME = "sites"
VERSION = "0.1.0"

SECTION_NAME = "sites"
SECTION_BODY = (Path(__file__).parent / "sites_section.md").read_text().strip()
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("website-building", "website-building-webapp", "website-building-game")


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=SITES_TOOLS,
        subagents=(WEBSITE_BUILDING_PROFILE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
