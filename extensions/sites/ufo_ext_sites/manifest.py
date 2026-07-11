"""What the sites pack declares: the website tools, the `build_website` delegation tool, the
website-building subagent profile, and the website-building skill the agent loads before building.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can build and serve a site, and `build_website` (or the generic
`spawn_subagent("website_building", ...)`) runs a site-building child scoped to them. Its prompt
section teaches the main agent to serve and validate a site before handing it back. One skill joins
the loadable set: `website-building` routes by project type and carries the shared design system.
Its `webapp/` subdirectory is a nested child skill (`website-building/webapp`, the fullstack
template) the loader discovers and pulls in with its parent; its `game/`, `shared/`, and
`informational/` subdirectories are ordinary bundled files."""

from pathlib import Path

from ufo.sdk.manifest import Manifest, PromptSection, SkillSpec
from ufo_ext_sites.delegation import DELEGATION_TOOLS
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from ufo_ext_sites.tools import SITES_TOOLS

NAME = "sites"
VERSION = "0.1.0"

SECTION_NAME = "sites"
SECTION_BODY = (Path(__file__).parent / "prompts" / "sites_section.md").read_text().strip()
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAME = "website-building"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(*SITES_TOOLS, *DELEGATION_TOOLS),
        subagents=(WEBSITE_BUILDING_PROFILE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        skills=(SkillSpec(path=SKILLS_ROOT / SKILL_NAME),),
    )
