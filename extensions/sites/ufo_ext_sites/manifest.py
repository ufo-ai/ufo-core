"""What the sites pack declares: the website tools, the `build_website` delegation tool, the
website-building subagent profile, the website-building skill the agent loads before building, the
`sites` surface that serves a hosted site's frame, the `site` object kind chat re-gates one
through, and the sweep that releases the page a main agent held as its homepage once the chat app
becomes that agent.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can build and serve a site, and `build_website` (or the generic
`spawn("website_building", ...)`) runs a site-building child scoped to them. Its prompt
section teaches the main agent to serve, validate, and hand back the hosted link. The surface and
the object kind are the two consumers of the one `hosted_site` registry the tools write: the frame
gates each viewer on the site's visibility and lets its creator change it in place, and the kind
gives chat the same act. One skill joins the loadable set: `website-building` routes by project type
and carries the shared design system. It names core's `ufo-style` in `depends`, so a page of ours —
an app homepage, an internal screen — is built in the house style with no second load, while a site
with a subject of its own still takes its art direction from that subject. Its `webapp/`
subdirectory is a nested child skill
(`website-building/webapp`, the fullstack template) the loader discovers and pulls in with its
parent; its `game/`, `shared/`, and `informational/` subdirectories are ordinary bundled files."""

from pathlib import Path

from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import HookSpec, Manifest, PromptSection, SkillSpec
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_ACCEPT_DESIGN,
    APPLICATION_BUILDER_DELEGATION,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_DESIGN,
    APPLICATION_BUILDER_EDIT,
    APPLICATION_BUILDER_PROFILE,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_READ,
    APPLICATION_BUILDER_READ_TOOL,
    APPLICATION_BUILDER_WIREFRAME,
    APPLICATION_BUILDER_WRITE,
    enforce_application_builder_phase,
    enforce_application_creation_route,
    limit_application_builder_repair_reads,
    require_application_builder_qa,
)
from ufo_ext_sites.conversation_slot import SITES_SLOT
from ufo_ext_sites.delegation import DELEGATION_TOOLS
from ufo_ext_sites.main_homepage import (
    RELEASE_JOB_NAME,
    RELEASE_JOB_SCHEDULE,
    release_main_homepage,
    unreleased_main_homepage_workspaces,
)
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
        tools=(
            *SITES_TOOLS,
            *DELEGATION_TOOLS,
            APPLICATION_BUILDER_DELEGATION,
            APPLICATION_BUILDER_DESIGN,
            APPLICATION_BUILDER_ACCEPT_DESIGN,
            APPLICATION_BUILDER_EDIT,
            APPLICATION_BUILDER_READ,
            APPLICATION_BUILDER_WRITE,
            APPLICATION_BUILDER_WIREFRAME,
        ),
        objects=(SITE_OBJECT,),
        subagents=(WEBSITE_BUILDING_PROFILE, APPLICATION_BUILDER_PROFILE),
        surfaces=(SITES_SURFACE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        skills=(SkillSpec(path=SKILLS_ROOT / SKILL_NAME),),
        hooks=(
            HookSpec(event="pre_tool_use", handler=enforce_application_creation_route),
            HookSpec(
                event="pre_tool_use",
                handler=enforce_application_builder_phase,
                tools=(
                    "list_external_tools",
                    "describe_external_tools",
                    "search_connector_tools",
                    "call_external_tool",
                    APPLICATION_BUILDER_ACCEPT_DESIGN.name,
                    APPLICATION_BUILDER_DESIGN.name,
                    APPLICATION_BUILDER_READ.name,
                    APPLICATION_BUILDER_EDIT.name,
                    APPLICATION_BUILDER_WRITE.name,
                    APPLICATION_BUILDER_QA_TOOL,
                    APPLICATION_BUILDER_DEPLOY_TOOL,
                ),
            ),
            HookSpec(
                event="pre_tool_use",
                handler=limit_application_builder_repair_reads,
                tools=(APPLICATION_BUILDER_READ_TOOL, APPLICATION_BUILDER_EDIT.name),
            ),
            HookSpec(
                event="pre_tool_use",
                handler=require_application_builder_qa,
                tools=(APPLICATION_BUILDER_DEPLOY_TOOL,),
            ),
        ),
        conversation_slots=(SITES_SLOT,),
        jobs=(
            JobSpec(
                name=RELEASE_JOB_NAME,
                schedule=RELEASE_JOB_SCHEDULE,
                handler=release_main_homepage,
                candidates=unreleased_main_homepage_workspaces(NAME),
            ),
        ),
    )
