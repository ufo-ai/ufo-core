"""The web pack's search_vertical proof: the tool posts an Exa /search body carrying the vertical's
category through the sandbox egress seam, and it registers with the ported verbatim description and
the five-vertical enum. The Exa call rides `ctx.sandbox.bash` (the curl-through-proxy path), so a
CommandRecordingSandbox stands in for the container — a dependency, never the asserted thing."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import selfhost_ext_web as web

from selfhost.blob import FilesystemBlobStore
from selfhost.loop.subagents import subagent_system_prompt
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import SpawnResult, ToolContext


@dataclass
class CommandRecordingSandbox:
    stdout: str = "{}"
    exit_code: int = 0
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        return ExecResult(stdout=self.stdout, stderr="", exit_code=self.exit_code)


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("search_vertical does not spawn")


def _context(sandbox: CommandRecordingSandbox, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        memory=None,
        member_id=None,
        artifact_token_secret="",
    )


def _tool():
    return next(tool for tool in web.manifest().tools if tool.name == web.SEARCH_VERTICAL_TOOL)


def test_search_vertical_registers_with_verbatim_description_and_enum() -> None:
    tool = _tool()
    assert tool.description == web.SEARCH_VERTICAL_DESCRIPTION
    assert tool.untrusted is True
    schema = tool.schema().input_schema
    assert set(schema["properties"]["vertical"]["enum"]) == {
        "image",
        "people",
        "academic",
        "video",
        "shopping",
    }


async def test_search_vertical_academic_carries_the_research_paper_category(
    tmp_path: Path,
) -> None:
    sandbox = CommandRecordingSandbox(stdout=json.dumps({"results": [{"title": "paper"}]}))
    tool = _tool()
    result = await tool.handler(
        _context(sandbox, tmp_path),
        tool.input_model.model_validate(
            {"vertical": "academic", "query": "graph transformers", "user_description": "papers"}
        ),
    )
    assert result.is_error is False
    assert "research paper" in sandbox.commands[-1]
    assert json.loads(result.content[0].text) == {"results": [{"title": "paper"}]}


async def test_search_vertical_video_sends_no_category(tmp_path: Path) -> None:
    sandbox = CommandRecordingSandbox(stdout=json.dumps({"results": []}))
    tool = _tool()
    await tool.handler(
        _context(sandbox, tmp_path),
        tool.input_model.model_validate(
            {"vertical": "video", "query": "how to knit", "user_description": "videos"}
        ),
    )
    assert "research paper" not in sandbox.commands[-1]
    assert "linkedin profile" not in sandbox.commands[-1]


def test_web_registers_the_research_and_deep_research_profiles() -> None:
    profiles = {profile.name: profile for profile in web.manifest().subagents}
    assert set(profiles) == {"research", "deep_research"}
    research = profiles["research"]
    assert research.input_model.model_validate({"objective": "size the market"}).objective
    for tool_name in ("search_web", "search_vertical", "fetch_url"):
        assert tool_name in research.tool_names
    assert "ask_user" not in research.tool_names and "spawn_subagent" not in research.tool_names


def test_deep_research_lifts_its_round_budget_above_the_default() -> None:
    profiles = {profile.name: profile for profile in web.manifest().subagents}
    assert profiles["deep_research"].max_rounds == web.DEEP_RESEARCH_ROUND_LIMIT == 200
    assert profiles["research"].max_rounds < profiles["deep_research"].max_rounds


def test_research_profile_prompt_wraps_with_citation_and_fills_the_skill_index() -> None:
    prompt = subagent_system_prompt(web.RESEARCH_PROFILE)
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "<citation_instructions>" in prompt
    assert "search_vertical" in prompt
