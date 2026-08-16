"""What the sites pack declares: the website tools, the `build_website` delegation tool, the
website-building subagent profile, the website-building skill the agent loads before building, the
`sites` surface that serves a hosted site's frame, and the `site` object kind chat re-gates one
through.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can build and serve a site, and `build_website` (or the generic
`spawn("website_building", ...)`) runs a site-building child scoped to them. Its prompt
section teaches the main agent to serve, validate, and hand back the hosted link. The surface and
the object kind are the two consumers of the one `hosted_site` registry the tools write: the frame
gates each viewer on the site's visibility and lets its creator change it in place, and the kind
gives chat the same act. One skill joins the loadable set: `website-building` routes by project type
and carries the shared design system. Its `webapp/` subdirectory is a nested child skill
(`website-building/webapp`, the fullstack template) the loader discovers and pulls in with its
parent; its `game/`, `shared/`, and `informational/` subdirectories are ordinary bundled files."""

from pathlib import Path

from ufo.sdk.manifest import Manifest, PromptSection, SkillSpec
from ufo_ext_sites.conversation_slot import SITES_SLOT
from ufo_ext_sites.delegation import DELEGATION_TOOLS
from ufo_ext_sites.objects import SITE_OBJECT
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from ufo_ext_sites.surface import SITES_SURFACE
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
        objects=(SITE_OBJECT,),
        subagents=(WEBSITE_BUILDING_PROFILE,),
        surfaces=(SITES_SURFACE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        skills=(SkillSpec(path=SKILLS_ROOT / SKILL_NAME),),
        conversation_slots=(SITES_SLOT,),
    )
