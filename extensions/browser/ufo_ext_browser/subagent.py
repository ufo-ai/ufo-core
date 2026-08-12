"""The browser subagent profile: a web-automation child turn scoped to the browser tools.

Its prompt is the ported `subagent_browser` instructions verbatim — no skill-index slot, since the
browser subagent teaches its own workflow rather than loading skills. Its tool subset is the pack's
browser tools plus the core file builtins it uses to save findings and screenshots into the shared
workspace for the parent agent to read back.

A browser child runs under the main agent's round ceiling, because a session spends its rounds a
page at a time — log in, page through a table, fill a form — and one that exhausts its budget
mid-session hands the parent a partial answer no follow-up can resume. So `extended_context`
defaults on rather than being asked for per spawn. `wide_browse` is the one caller that sends it
off: a fan-out is one short extraction per entity, up to 128 of them, and it keeps the ordinary
subagent budget this profile declares."""

from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import SubagentProfile
from ufo_ext_browser.tools import BROWSER_TOOL_NAMES

BROWSER_SUBAGENT_NAME = "browser"
BROWSER_SUBAGENT_PROMPT = (Path(__file__).parent / "prompts" / "subagent_browser.md").read_text()
BROWSER_SUBAGENT_TOOL_NAMES = (*BROWSER_TOOL_NAMES, "read", "write", "edit", "search_web")


class BrowserTask(BaseModel):
    task: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )
    url: str | None = None
    task_name: str | None = None
    extended_context: bool = True


class BrowserResult(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


BROWSER_PROFILE = SubagentProfile(
    name=BROWSER_SUBAGENT_NAME,
    prompt=BROWSER_SUBAGENT_PROMPT,
    tool_names=BROWSER_SUBAGENT_TOOL_NAMES,
    input_model=BrowserTask,
    output_model=BrowserResult,
    untrusted_output=True,
    model="claude-sonnet-5",
)
