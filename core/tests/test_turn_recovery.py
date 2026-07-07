"""Crash recovery does not redo completed work.

The proof for RFC 0008 Level 1: a turn killed mid-run recovers under the same DBOS `workflow_id`,
replaying its recorded model-round and tool-dispatch steps from `operation_outputs` — so a completed
tool call is NOT re-executed and its tokens are NOT re-spent — and resumes at the first unrecorded
step to a correct terminal. The crash is a `BaseException` raised mid-round: DBOS records no outcome
for the in-flight step and never finalizes the workflow, leaving it PENDING for
`recover_pending_workflows` to re-dispatch — exactly a killed worker recovering on a peer."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOS, DBOSClient, SetWorkflowID
from ufo_ext_index_default import DefaultIndex

from ufo.accounting import CORE_PRICING
from ufo.blob import FilesystemBlobStore
from ufo.browser import SandboxCdpProvider
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.ext.manifest import ModelProviderSpec
from ufo.hub import InProcessHub
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.models.registry import ModelRegistry
from ufo.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Usage

RECOVERY_TIMEOUT_SECONDS = 30


class _WorkerCrash(BaseException):
    """A hard mid-turn crash. A BaseException, not Exception, so DBOS records no step outcome and
    never finalizes the workflow — it stays PENDING and recoverable, like a killed worker."""


@dataclass(frozen=True)
class _CrashOnceModel:
    """Round one calls bash; round two crashes once, then answers on recovery. The crash flag is a
    shared one-cell list so the recovered run (a fresh client instance) sees the crash already
    happened and answers instead of crashing again."""

    crashed: list[bool]

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        answered = any(
            isinstance(message.content, tuple)
            and any(isinstance(block, ToolResultBlock) for block in message.content)
            for message in request.messages
        )
        if answered:
            if not self.crashed[0]:
                self.crashed[0] = True
                raise _WorkerCrash("killed mid round two")
            yield TextDelta(text="recovered")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "echo hi"}')
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass(frozen=True)
class _CountingCarrier:
    """Records every exec so the test can prove the round-one bash call runs exactly once across the
    crash and the recovery."""

    execs: list[tuple[str, ...]]

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.execs.append(tuple(argv))
        return ExecResult(stdout="hi\n", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


async def _seed_turn(model: str = "claude-opus-4-8") -> UUID:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
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
                prompt="be brief",
                model=model,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=str(turn_id),
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
                inbound="recover me",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


def _install_runtime(config: Config, registry: ModelRegistry, carrier: _CountingCarrier) -> None:
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=FilesystemBlobStore(root=config.blob.root),
            workspace_fs=None,
            hub=InProcessHub(),
            carrier=carrier,
            cdp_provider=SandboxCdpProvider(endpoint=None),
            search_provider=None,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            dbos=DBOSClient(system_database_url=config.database.system_url),
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=registry,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(embed=_StubEmbed(), transaction=workspace_tx),
            embed=_StubEmbed(),
            artifact_token_secret="",
        )
    )


@dataclass(frozen=True)
class _StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


async def _await_terminal(turn_id: UUID) -> TerminalFrame:
    async with asyncio.timeout(RECOVERY_TIMEOUT_SECONDS):
        while True:
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).one()
            if row.terminal is not None:
                return TerminalFrame.model_validate(row.terminal)
            await asyncio.sleep(0.05)


@pytest.mark.serial
async def test_crash_mid_turn_recovers_without_re_executing_completed_work(
    db: None, dbos_launched: Config
) -> None:
    execs: list[tuple[str, ...]] = []
    crashed = [False]
    registry = ModelRegistry(
        providers=(
            ModelProviderSpec(
                name="crash",
                matches=lambda model: True,
                client=lambda model: _CrashOnceModel(crashed=crashed),
            ),
        ),
        pricing=CORE_PRICING,
        auto_model="claude-opus-4-8",
    )
    turn_id = await _seed_turn()

    saved = loop_queue._runtime
    loop_queue.reset_runtime()
    _install_runtime(dbos_launched, registry, _CountingCarrier(execs))
    try:
        with SetWorkflowID(str(turn_id)):
            with pytest.raises(_WorkerCrash):
                await loop_queue.turn_workflow(str(turn_id))

        assert execs == [("bash", "-lc", "echo hi")]
        assert crashed[0] is True

        DBOS._recover_pending_workflows(["local"])
        terminal = await _await_terminal(turn_id)

        assert terminal.status == "done"
        assert terminal.text == "recovered"
        assert execs == [("bash", "-lc", "echo hi")]
        assert terminal.tokens == 6
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.ledger.c.amount).where(tables.ledger.c.turn_id == turn_id)
                )
            ).all()
        assert [int(row.amount) for row in rows] == [6]
    finally:
        loop_queue._runtime.dbos.destroy()
        loop_queue.reset_runtime()
        if saved is not None:
            loop_queue.init_runtime(saved)
