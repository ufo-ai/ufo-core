"""What the browser pack declares: the browser subagent and the delegation tools that reach it.

`serve` sources the tools into the turn's tool set and the profile into the SubagentRegistry. The
raw browser/computer-use tools are profile-only: a main agent never holds them — it delegates with
`browser_task`/`wide_browse`, and `spawn("browser", ...)` runs a web-automation child
turn scoped to the full browser surface. Its prompt section teaches the main agent to delegate,
and the subagent's own prompt teaches the operate-and-capture workflow."""

from pathlib import Path

from ufo.sdk.manifest import Manifest, PromptSection
from ufo_ext_browser.delegation import DELEGATION_TOOLS
from ufo_ext_browser.subagent import BROWSER_PROFILE
from ufo_ext_browser.tools import BROWSER_TOOLS

NAME = "browser"
VERSION = "0.1.0"

SECTION_NAME = "browser"
SECTION_BODY = (Path(__file__).parent / "prompts" / "browser_section.md").read_text().strip()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(*BROWSER_TOOLS, *DELEGATION_TOOLS),
        subagents=(BROWSER_PROFILE,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        requires=("cdp_providers",),
    )
