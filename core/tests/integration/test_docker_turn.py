"""A full turn loop that execs a tool in a LIVE Docker container — the real `DockerCarrier` end to
end, the gap the StandIn-carrier lifecycle tests leave. The model issues a real `bash` call, the
turn dispatches it through the real carrier into a real container built exactly as test_file_tools
builds it, and the container's real stdout is asserted from the durable transcript the turn wrote —
nothing here asserts a fake carrier.

`docker`-gated and serial (a live container + one database). Skips with a clear reason when Docker
is absent or the image cannot build."""

import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_docker import DockerCarrier

from selfhost.blob import FilesystemBlobStore
from selfhost.browser import SandboxCdpProvider
from selfhost.db import workspace_tx
from selfhost.ext.loader import HookChain
from selfhost.hub import InProcessHub
from selfhost.loop.compaction import Compaction
from selfhost.loop.engine import TurnEngine
from selfhost.loop.prompts.render import rendered_prompt
from selfhost.loop.transcript import Transcript
from selfhost.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    Usage,
)
from selfhost.sandbox import session as session_module
from selfhost.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_UID,
    MountSpec,
    SandboxHandle,
    SandboxSession,
)
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult
from selfhost.tools.registry import ToolRegistry

pytestmark = pytest.mark.docker

SANDBOX_TEST_IMAGE = "selfhost-sandbox:test"
MARKER = "sandbox-lives-42"


@dataclass(frozen=True)
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


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
        yield ToolCallDelta(id="b1", partial_json=f'{{"command": "echo {MARKER}"}}')
        yield Usage(input_tokens=1, output_tokens=1)


@pytest.fixture(scope="module")
def sandbox_image() -> str:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    image_dir = Path(session_module.__file__).parent / "image"
    built = subprocess.run(
        ["docker", "build", "-t", SANDBOX_TEST_IMAGE, str(image_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.skip(f"cannot build the sandbox image: {built.stderr.strip()}")
    return SANDBOX_TEST_IMAGE


@pytest.fixture
def live_container(sandbox_image: str, tmp_path: Path) -> Iterator[SandboxHandle]:
    """A real container with a host-bind-mounted /workspace, set up as prod does: the mount is
    chowned to the sandbox uid (in a throwaway --user 0 container, so the test needs no host root)
    and the container then runs as the image's default non-root user."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chowned = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
            "chown",
            "-R",
            f"{SANDBOX_UID}:{SANDBOX_GID}",
            "/workspace",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if chowned.returncode != 0:
        pytest.skip(f"docker cannot chown the workspace mount: {chowned.stderr.strip()}")
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
            "sleep",
            "infinity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"docker cannot run the sandbox image: {started.stderr.strip()}")
    container = started.stdout.strip()
    try:
        yield SandboxHandle(
            conversation_id=uuid4(),
            container_id=container,
            mount=MountSpec(kind="filesystem", host_path=str(workspace)),
        )
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


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
        system_prompt=rendered_prompt("run the marker"),
        model=model,
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model,
            model="claude-opus-4-8",
            blob=blob,
            conversation_id=turn.conversation_id,
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=DockerCarrier(), handle=live_container),
        cdp_provider=SandboxCdpProvider(endpoint=None),
        tools=ToolRegistry(BUILTIN_TOOLS),
        tool_ext={},
        hooks=HookChain(),
        blob=blob,
        spawn=_unavailable_spawn,
        member_id=None,
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
