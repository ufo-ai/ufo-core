from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from pydantic import BaseModel

from selfhost.config import Config
from selfhost.db import workspace_tx
from selfhost.ext.manifest import SUBAGENT_ROUND_LIMIT, SubagentProfile
from selfhost.loop.profiles import CORE_SUBAGENT_PROFILES, GENERAL_PURPOSE
from selfhost.loop.subagents import SubagentRegistry, Subagents, subagent_system_prompt
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame, Turn, turn_id_for
from selfhost.tools.builtins import BUILTIN_TOOLS


class _Task(BaseModel):
    task: str


class _Finding(BaseModel):
    finding: str


def _profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=name,
        prompt=f"{name} instructions",
        tool_names=("bash", "read"),
        input_model=_Task,
        output_model=_Finding,
    )


def test_registry_rejects_duplicate_profiles() -> None:
    with pytest.raises(ValueError, match="duplicate subagent profiles: dup"):
        SubagentRegistry((_profile("dup"), _profile("dup")))


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown subagent profile: missing"):
        SubagentRegistry((_profile("a"),)).get("missing")


def test_registry_get_returns_named_profile() -> None:
    registry = SubagentRegistry((_profile("a"), _profile("b")))
    assert registry.get("b").name == "b"
    assert registry.get("b").tool_names == ("bash", "read")


def test_system_prompt_carries_instructions_and_output_schema() -> None:
    prompt = subagent_system_prompt(_profile("research"))
    assert "research instructions" in prompt
    assert "finding" in prompt
    assert "JSON" in prompt


def test_core_ships_a_general_purpose_profile_the_registry_resolves() -> None:
    registry = SubagentRegistry(CORE_SUBAGENT_PROFILES)
    profile = registry.get(GENERAL_PURPOSE)
    assert profile.name == GENERAL_PURPOSE
    assert profile.input_model.model_validate({"task": "look into X"}).task == "look into X"
    assert profile.output_model.model_validate({"result": "done"}).result == "done"


def test_general_purpose_carries_the_subagent_round_budget() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    assert profile.max_rounds == SUBAGENT_ROUND_LIMIT == 50


def test_a_deep_profile_lifts_its_round_budget_above_the_subagent_default() -> None:
    deep = SubagentProfile(
        name="deep",
        prompt="p",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
        max_rounds=200,
    )
    assert deep.max_rounds == 200 > SUBAGENT_ROUND_LIMIT


def test_general_purpose_tool_subset_excludes_the_tools_a_subagent_must_not_hold() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    assert "load_skill" in profile.tool_names
    assert {"ask_user", "spawn_subagent", "connect_account"}.isdisjoint(profile.tool_names)


def test_general_purpose_tool_names_all_resolve_to_real_builtins() -> None:
    """The queue projects a subagent's tool set by filtering the builtins on these names — a name
    with no builtin would silently vanish, leaving the subagent short a tool."""
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    assert set(profile.tool_names) <= builtin_names


def test_general_purpose_prompt_lists_the_loadable_skills_and_binds_its_output() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    prompt = subagent_system_prompt(profile)
    assert "<available_skills>" in prompt
    for skill in ("sandbox", "delegation"):
        assert skill in prompt
    assert "result" in prompt and "JSON" in prompt


def test_core_ships_only_the_general_purpose_profile() -> None:
    assert {profile.name for profile in CORE_SUBAGENT_PROFILES} == {GENERAL_PURPOSE}


def test_subagent_prompt_wraps_the_profile_with_the_shared_citation_discipline() -> None:
    prompt = subagent_system_prompt(_profile("research"))
    assert "research instructions" in prompt
    assert "<citation_instructions>" in prompt


def test_subagent_prompt_fills_the_skill_index_slot() -> None:
    profile = SubagentProfile(
        name="slotted",
        prompt="do the task\n\n{{skill_index}}",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
    )
    prompt = subagent_system_prompt(profile)
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt


def test_subagent_prompt_fails_loud_on_an_unfilled_slot() -> None:
    profile = SubagentProfile(
        name="stray",
        prompt="do it {{mystery}}",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
    )
    with pytest.raises(ValueError, match="unresolved slots: mystery"):
        subagent_system_prompt(profile)


def test_profile_model_defaults_to_none_meaning_inherit_the_parent() -> None:
    assert _profile("a").model is None


def test_general_purpose_inherits_the_parent_model() -> None:
    assert SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE).model is None


def test_a_profile_can_pin_a_distinct_model() -> None:
    """The queue resolves `profile.model or agent.model`, so a set model overrides the parent's and
    None falls back — proven end-to-end by the billing test; here the field carries the choice."""
    pinned = SubagentProfile(
        name="pinned",
        prompt="p",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
        model="gpt-5.4",
    )
    assert pinned.model == "gpt-5.4"
    assert (pinned.model or "claude-opus-4-8") == "gpt-5.4"
    assert (_profile("a").model or "claude-opus-4-8") == "claude-opus-4-8"


@dataclass
class _RecordingClient:
    """Records the workflow argument (the follow-up turn id) each enqueue carries, so the message
    test reads back which turn the workflow placed on the queue — never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_arg: str) -> None:
        self.enqueued.append(workflow_arg)


async def _workspace_agent() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="parent",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _running_child(workspace_id: UUID, agent_id: UUID, parent_id: UUID) -> tuple[UUID, UUID]:
    conversation_id = uuid4()
    child_id = turn_id_for(workspace_id, conversation_id, 1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="subagent",
                queue_key=str(child_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=child_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="do the task",
                terminal=None,
                parent_turn_id=parent_id,
                subagent_profile=GENERAL_PURPOSE,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return child_id, conversation_id


async def test_message_admits_the_running_childs_next_turn_and_enqueues_it(
    db: None, dbos_launched: Config
) -> None:
    """A follow-up message becomes the child's next turn (seq+1) on its own conversation, carrying
    the child's subagent profile so the continuation runs as the subagent, and lands on the turn
    queue — the running child receives it once the turn in flight ends."""
    workspace_id, agent_id = await _workspace_agent()
    parent = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="parent",
    )
    child_id, child_conversation = await _running_child(workspace_id, agent_id, parent.id)
    client = _RecordingClient()
    subagents = Subagents(client=client, registry=SubagentRegistry(()), parent=parent)
    status = await subagents.message(child_id, "also summarize the risks")
    assert status.status == "queued"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.conversation_id,
                    tables.turn.c.seq,
                    tables.turn.c.status,
                    tables.turn.c.inbound,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.parent_turn_id,
                ).where(tables.turn.c.id == status.turn_id)
            )
        ).one()
    assert row.conversation_id == child_conversation
    assert (row.seq, row.status, row.inbound) == (2, "queued", "also summarize the risks")
    assert row.subagent_profile == GENERAL_PURPOSE
    assert row.parent_turn_id == parent.id
    assert client.enqueued == [str(status.turn_id)]


async def test_message_refuses_a_turn_this_parent_did_not_spawn(
    db: None, dbos_launched: Config
) -> None:
    workspace_id, agent_id = await _workspace_agent()
    parent = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="parent",
    )
    stranger, _ = await _running_child(workspace_id, agent_id, uuid4())
    subagents = Subagents(client=_RecordingClient(), registry=SubagentRegistry(()), parent=parent)
    with pytest.raises(ValueError, match="not a subagent this turn spawned"):
        await subagents.message(stranger, "hello")


async def _finished_child(workspace_id: UUID, agent_id: UUID, text: str) -> UUID:
    turn_id = uuid4()
    conversation_id = uuid4()
    terminal = TerminalFrame(status="done", text=text)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="subagent",
                queue_key=str(turn_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="x",
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_wait_reports_every_already_finished_childs_status(
    db: None, dbos_launched: Config
) -> None:
    """`wait` folds each child's terminal into a SubagentStatus in turn_id order — the only way a
    background-spawn loop collects more than one child. A stray `await` inside the folding
    generator turns it into an async generator `tuple()` cannot drain, which crashes on every
    multi-child wait without ever showing up in a single-child smoke test."""
    workspace_id = uuid4()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="parent",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    first = await _finished_child(workspace_id, agent_id, "first done")
    second = await _finished_child(workspace_id, agent_id, "second done")
    parent = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="parent",
    )
    client = DBOSClient(system_database_url=dbos_launched.database.system_url)
    subagents = Subagents(client=client, registry=SubagentRegistry(()), parent=parent)
    statuses = await subagents.wait((first, second))
    assert [status.turn_id for status in statuses] == [first, second]
    assert [status.text for status in statuses] == ["first done", "second done"]
    assert all(status.status == "done" for status in statuses)
