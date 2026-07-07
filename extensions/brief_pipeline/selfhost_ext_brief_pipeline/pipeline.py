"""The brief pipeline's typed subagent profiles: outline → draft → critic.

Each stage is a toolless writer — its typed output is the whole handoff, piped by the parent agent
through sequential foreground spawns (the skill teaches the chain). None can spawn, so the chain
has exactly the depth the parent drives."""

from pathlib import Path

from pydantic import BaseModel

from selfhost.sdk.manifest import SubagentProfile

OUTLINE_PROFILE_NAME = "brief_outline"
DRAFT_PROFILE_NAME = "brief_draft"
CRITIC_PROFILE_NAME = "brief_critic"
STAGE_ROUND_LIMIT = 4
PROMPTS = Path(__file__).parent / "prompts"


class BriefRequest(BaseModel):
    topic: str
    audience: str = "the team"


class BriefOutline(BaseModel):
    outline: str


class DraftRequest(BaseModel):
    topic: str
    outline: str


class BriefDraft(BaseModel):
    draft: str


class CritiqueRequest(BaseModel):
    draft: str


class BriefCritique(BaseModel):
    verdict: str
    improvements: str = ""


OUTLINE_PROFILE = SubagentProfile(
    name=OUTLINE_PROFILE_NAME,
    prompt=(PROMPTS / "subagent_outline.md").read_text(),
    tool_names=(),
    input_model=BriefRequest,
    output_model=BriefOutline,
    max_rounds=STAGE_ROUND_LIMIT,
)

DRAFT_PROFILE = SubagentProfile(
    name=DRAFT_PROFILE_NAME,
    prompt=(PROMPTS / "subagent_draft.md").read_text(),
    tool_names=(),
    input_model=DraftRequest,
    output_model=BriefDraft,
    max_rounds=STAGE_ROUND_LIMIT,
)

CRITIC_PROFILE = SubagentProfile(
    name=CRITIC_PROFILE_NAME,
    prompt=(PROMPTS / "subagent_critic.md").read_text(),
    tool_names=(),
    input_model=CritiqueRequest,
    output_model=BriefCritique,
    max_rounds=STAGE_ROUND_LIMIT,
)
