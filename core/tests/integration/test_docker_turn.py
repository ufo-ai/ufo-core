"""A full turn loop that execs a tool in a LIVE Docker container — the real `DockerCarrier` end to
end, the gap the StandIn-carrier lifecycle tests leave. The model issues a real `bash` call, the
turn dispatches it through the real carrier into a real container built exactly as test_file_tools
builds it, and the container's real stdout is asserted from the durable transcript the turn wrote —
nothing here asserts a fake carrier.

`docker`-gated and serial (a live container + one database). Missing Docker or a failed image build
fails the required integration gate and skips an optional local run."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_docker import DockerCarrier

from ufo.access.connectors import ConnectorRegistry
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import HookChain
from ufo.hub import InProcessHub
from ufo.loop.compaction import Compaction
from ufo.loop.engine import TurnEngine
from ufo.loop.prompts.render import rendered_prompt
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    Usage,
)
from ufo.sandbox.session import SandboxHandle, SandboxSession
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult
from ufo.tools.registry import ToolRegistry
from ufo.turns.activity import ActivitySummarizer
from ufo.turns.audience import conversation_audience

pytestmark = pytest.mark.docker

MARKER = "sandbox-lives-42"


class _ActivityModel:
    model = "gpt-5.6-luna"

    async def complete(self, _request: ModelRequest) -> str:
        return "Working on the request."


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("the docker turn does not spawn subagents")


@dataclass
class BashThenAnswerModel:
    """Round one calls `bash` to echo a marker; round two — seeing the tool result — answers, so the
    turn runs a real command in the container and then commits done. Records the model-facing tool
    result it was handed so the test can assert the real container's stdout round-tripped."""

    seen_tool_result: str | None = None

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        for message in request.messages:
            if isinstance(message.content, tuple):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and isinstance(block.content, str):
                        self.seen_tool_result = block.content
        if self.seen_tool_result is not None:
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="b1", name="bash")
        yield ToolCallDelta(
            id="b1",
            partial_json=json.dumps(
                {
                    "command": f"echo {MARKER}",
                }
            ),
        )
        yield Usage(input_tokens=1, output_tokens=1)


@pytest.fixture
def live_container(sandbox_container: tuple[str, Path]) -> SandboxHandle:
    container, workspace = sandbox_container
    return SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        workspace_host_path=str(workspace),
    )


async def _seed_turn(conversation_id: UUID) -> Turn:
    workspace_id, member_id, agent_id, turn_id = (uuid4() for _ in range(4))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
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
                status="queued",
                inbound="run the marker",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="queued",
        inbound="run the marker",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )


async def test_turn_execs_bash_in_a_live_container(
    db: None, live_container: SandboxHandle, tmp_path: Path
) -> None:
    turn = await _seed_turn(live_container.conversation_id)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    model = BashThenAnswerModel()
    engine = TurnEngine(
        turn=turn,
        agent=Agent(prompt="run the marker", model="claude-opus-4-8"),
        byok=False,
        system_prompt=rendered_prompt("run the marker"),
        model=model,
        activity_summarizer=ActivitySummarizer(_ActivityModel()),
        provider="anthropic",
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model,
            model="claude-opus-4-8",
            blob=blob,
            conversation_id=turn.conversation_id,
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=DockerCarrier(), handle=live_container),
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=ToolRegistry(BUILTIN_TOOLS),
        tool_ext={},
        hooks=HookChain(),
        blob=blob,
        spawn=_unavailable_spawn,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=None,
    )

    frame = await engine.run()

    assert frame is not None and frame.status == "done"
    # The real container ran `echo` and its stdout reached the model as the tool result.
    assert model.seen_tool_result is not None
    assert MARKER in model.seen_tool_result
    # …and it is durable in the transcript the turn wrote.
    stored = await Transcript(blob=blob, conversation_id=turn.conversation_id).read()
    assert stored is not None
    tool_result = next(
        block
        for message in stored.messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock)
    )
    assert isinstance(tool_result.content, str) and MARKER in tool_result.content
    assert tool_result.is_error is False
