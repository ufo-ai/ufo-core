from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError
from ufo_ext_skill_create.manifest import (
    SKILL_KIND,
    SKILL_SEARCH_ACTION,
    SKILL_SEARCH_LIMIT,
    SKILL_SEARCH_NO_MATCH,
    SkillSearchInput,
    manifest,
    skill_search,
)

from ufo.blob import FilesystemBlobStore
from ufo.host.ext.loader import turn_tools
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.queue import IMPLIED_GRANTS, _agent_actions, _subagent_actions
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, SkillRegistry
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ObjectBinding
from ufo.runtime.turns.activity import SKILL_LOAD_TOOL, SKILL_SEARCH_ACTION_ID
from ufo.schema.records import MEMBER_ADMISSION, Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.skills import SKILL_LINE_MAX_CHARS, RuntimeSkill

SKILL_SEARCH_ID = f"action:{SKILL_KIND}:{SKILL_SEARCH_ACTION}"


class _Task(BaseModel):
    objective: str


class _Finding(BaseModel):
    text: str


def _profile(tool_names: tuple[str, ...]) -> SubagentProfile:
    return SubagentProfile(
        name="probe",
        prompt="do the task\n\n{{skill_index}}",
        tool_names=tool_names,
        input_model=_Task,
        output_model=_Finding,
    )


def _member_tier(*skills: RuntimeSkill) -> SkillRegistry:
    rows = {skill.name: skill for skill in skills}

    async def materialize(name: str) -> RuntimeSkill | None:
        return rows.get(name)

    return CORE_SKILL_REGISTRY.with_member(tuple(skill.card() for skill in skills), materialize)


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
    dedup_key: str | None = None,
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this test")


def _ctx(tmp_path: Path, *skills: RuntimeSkill) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="find a skill",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        skills=_member_tier(*skills),
    )


async def _search(ctx: ToolContext, **args: object) -> str:
    result = await skill_search(ctx, SkillSearchInput.model_validate(args))
    assert result.is_error is False
    return result.content[0].text


def test_skill_search_is_a_parallel_safe_action_on_the_skill_collection() -> None:
    tools, _, verbs = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    assert SKILL_SEARCH_ACTION not in {tool.name for tool in tools}
    action = verbs.actions[SKILL_KIND][SKILL_SEARCH_ACTION].action
    assert action.bound == ObjectBinding(kind=SKILL_KIND, binding="collection")
    assert action.canonical_id == SKILL_SEARCH_ID == SKILL_SEARCH_ACTION_ID
    assert action.parallel_safe and not action.side_effecting and action.presentation is None


def test_an_allowlist_naming_load_skill_also_holds_skill_search() -> None:
    _, _, verbs = turn_tools((manifest(),), None, audience=SHARED_AUDIENCE)
    assert IMPLIED_GRANTS[SKILL_LOAD_TOOL] == (SKILL_SEARCH_ID,)
    paired = _agent_actions(verbs.actions, (SKILL_LOAD_TOOL,), MEMBER_ADMISSION)
    assert paired == frozenset({SKILL_SEARCH_ID})
    assert _agent_actions(verbs.actions, ("read",), MEMBER_ADMISSION) == frozenset()
    capable = _profile((SKILL_LOAD_TOOL,))
    assert _subagent_actions(verbs.actions, capable, frozenset()) == frozenset({SKILL_SEARCH_ID})
    granted = _profile(("read",))
    held = _subagent_actions(verbs.actions, granted, frozenset({SKILL_LOAD_TOOL}))
    assert held == frozenset({SKILL_SEARCH_ID})
    assert _subagent_actions(verbs.actions, granted, frozenset()) == frozenset()


async def test_skill_search_ranks_matches_across_both_tiers(tmp_path: Path) -> None:
    saved = RuntimeSkill(
        name="invoice-review",
        description="Load when a member asks to reconcile an invoice.",
        instructions="i",
    )
    ctx = _ctx(tmp_path, saved)
    lines = (await _search(ctx, query="reconcile an invoice")).splitlines()
    assert lines[0] == "invoice-review: Load when a member asks to reconcile an invoice."
    assert all(":" in line for line in lines)
    deploy_hit = await _search(ctx, query="sandbox container commands")
    assert deploy_hit.splitlines()[0].startswith("sandbox: ")


async def test_skill_search_with_no_match_answers_the_searchable_total(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    total = len(ctx.skills.all_cards())
    assert await _search(ctx, query="zzzznothing") == SKILL_SEARCH_NO_MATCH.format(total=total)


async def test_skill_search_clamps_its_limit_and_truncates_lines(tmp_path: Path) -> None:
    crowd = tuple(
        RuntimeSkill(
            name=f"billing-{i}", description="Load when billing " + "x" * 300, instructions="i"
        )
        for i in range(12)
    )
    with pytest.raises(ValidationError):
        SkillSearchInput.model_validate({"query": "billing", "limit": SKILL_SEARCH_LIMIT + 1})
    with pytest.raises(ValidationError):
        SkillSearchInput.model_validate({"query": "billing", "kind": SKILL_KIND})
    lines = (await _search(_ctx(tmp_path, *crowd), query="billing")).splitlines()
    assert len(lines) == SKILL_SEARCH_LIMIT
    assert all(len(line) <= SKILL_LINE_MAX_CHARS for line in lines)
