"""What the research pack declares: the web-research tools, the research subagent profiles, the web
prompt section, and the research skills the agent loads on demand — over whatever search backend the
deploy selects.

`serve` sources the tools into the turn's tool set and the profiles into the SubagentRegistry, and
threads the selected `SearchProvider` onto each `ToolContext`. The pack owns no credential slot —
the search backend (exa) owns its own — so it declares `requires=("search_providers",)`: a deploy
that activates research with no search backend configured fails loud at boot rather than on the
first search."""

from pathlib import Path

from selfhost.sdk.manifest import Manifest, PromptSection, SkillSpec
from selfhost_ext_research.delegation import WIDE_RESEARCH_TOOL
from selfhost_ext_research.subagent import DEEP_RESEARCH_PROFILE, RESEARCH_PROFILE
from selfhost_ext_research.tools import RESEARCH_TOOLS

NAME = "research"
VERSION = "0.1.0"

SECTION_NAME = "web"
SECTION_BODY = (Path(__file__).parent / "prompts" / "web_section.md").read_text().strip()
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("research-assistant", "research-report")


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(*RESEARCH_TOOLS, WIDE_RESEARCH_TOOL),
        subagents=(RESEARCH_PROFILE, DEEP_RESEARCH_PROFILE),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        requires=("search_providers",),
    )
