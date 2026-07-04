"""What the sites pack declares: the website tools and the website-building subagent profile.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can build and serve a site and `spawn_subagent("website_building", ...)`
runs a site-building child scoped to them. Its prompt section teaches the main agent to serve and
validate a site before handing it back."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, PromptSection
from selfhost_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from selfhost_ext_sites.tools import SITES_TOOLS

NAME = "sites"
VERSION = "0.1.0"

SECTION_NAME = "sites"
SECTION_BODY = (Path(__file__).parent / "sites_section.md").read_text().strip()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=SITES_TOOLS,
        subagents=(WEBSITE_BUILDING_PROFILE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
