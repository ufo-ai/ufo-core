"""A real streamed Anthropic turn through the DBOS queue — the live-model proof the StandIn
lifecycle tests can't give. The model client is the real `AnthropicClient` (not monkeypatched), so
a turn admitted through `MemberAdmission` runs on the DBOS worker against the live API, and the
durable turn row, the priced ledger row, and the streamed frames are asserted from what the real
call produced. Gated on `ANTHROPIC_API_KEY`; the sandbox is the local carrier over a tmp workspace
root (this proves the model path, not a container — test_docker_turn proves that).

Runs only where the key is set (skips with a clear reason otherwise), and serially — it shares the
process-singleton DBOS executor with the rest of the suite."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.hub import Hub, InProcessHub, Terminal, TextDelta
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import tail_frames
from ufo.workspace import ws

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY for a live model turn"
)

STREAM_TIMEOUT_SECONDS = 120
LIVE_MODEL = "claude-opus-4-8"
LIVE_PROMPT = "Reply with exactly the single word: pong. Do not use any tools."


@dataclass(frozen=True)
class Seed:
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


@pytest.fixture
async def live_runtime(
    db: None, dbos_launched: Config, tmp_path: Path
) -> AsyncIterator[tuple[Hub, FilesystemBlobStore]]:
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    runtime_dbos = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=ConversationSandbox(
                carrier=LocalCarrier(),
                backend="local",
                off_cluster=False,
                image_ref=SANDBOX_IMAGE_REF,
                proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
                workspace_root=tmp_path / "workspaces",
            ),
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"live-model-test-secret"),
            dbos=runtime_dbos,
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=ModelRegistry(
                specs={spec.id: spec for spec in CORE_MODEL_SPECS},
                pricing=CORE_PRICING,
                auto_model=LIVE_MODEL,
            ),
            skills=skill_registry(()),
            credentials=None,
            index=None,
            embed=None,
            artifact_token_secret="",
        )
    )
    yield hub, blob
    runtime_dbos.destroy()
    loop_queue.reset_runtime()


async def _bootstrap() -> Seed:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                email=f"{member_id.hex[:8]}@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="You are a terse assistant.",
                model=LIVE_MODEL,
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
    return Seed(workspace_id, member_id, agent_id, conversation_id)


async def _admit(seed: Seed, body: str) -> UUID:
    admission = Admission(dbos=loop_queue._runtime.dbos, durable_surfaces=frozenset())
    admitted = await MemberAdmission(admission=admission, workspace_id=seed.workspace_id).admit(
        seed.conversation_id, body, speaker_member_id=seed.member_id
    )
    return admitted.turn_id


async def _consume(hub: Hub, seed: Seed, turn_id: UUID) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    with ws(seed.workspace_id):
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            async for _cursor, frame in tail_frames(hub, turn_id):
                match frame:
                    case TextDelta():
                        deltas.append(frame.text)
                    case Terminal():
                        return "".join(deltas), frame.frame.model_dump(mode="json")
    raise AssertionError("stream ended without a terminal frame")


async def test_live_anthropic_turn_streams_and_bills(
    live_runtime: tuple[Hub, FilesystemBlobStore],
) -> None:
    hub, _ = live_runtime
    seed = await _bootstrap()
    turn_id = await _admit(seed, LIVE_PROMPT)

    streamed, terminal = await _consume(hub, seed, turn_id)

    assert terminal["status"] == "done", terminal
    assert terminal["model"] == LIVE_MODEL
    assert int(terminal["tokens"]) > 0
    assert streamed.strip() != ""
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
        billed = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert status == "done"
    assert int(billed.amount) > 0
    assert int(billed.priced_micro_usd) > 0
    assert billed.model == LIVE_MODEL
