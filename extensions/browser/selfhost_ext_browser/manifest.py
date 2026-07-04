"""What the browser pack declares: the browser/computer-use tools and the browser subagent profile.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry, so an
agent granted these tools can drive the sandbox browser and `spawn_subagent("browser", ...)` runs a
web-automation child turn scoped to them. Its prompt section teaches the main agent when to reach
for the browser instead of search."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, PromptSection
from selfhost_ext_browser.subagent import BROWSER_PROFILE
from selfhost_ext_browser.tools import BROWSER_TOOLS

NAME = "browser"
VERSION = "0.1.0"

SECTION_NAME = "browser"
SECTION_BODY = (Path(__file__).parent / "browser_section.md").read_text().strip()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=BROWSER_TOOLS,
        subagents=(BROWSER_PROFILE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
