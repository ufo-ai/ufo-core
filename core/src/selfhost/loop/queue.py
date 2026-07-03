"""Durable turn execution: partitioned queue, the turn workflow, per-process runtime."""

import os
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, Queue

from selfhost.blob import BlobStore
from selfhost.config import Config
from selfhost.db import workspace_tx
from selfhost.hub import Hub
from selfhost.loop.engine import TurnEngine
from selfhost.loop.transcript import Transcript
from selfhost.models import ModelClient
from selfhost.models.anthropic import AnthropicClient, anthropic_sdk_client
from selfhost.models.openai import OpenAIClient, openai_sdk_client
from selfhost.schema import tables
from selfhost.schema.records import (
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    Agent,
    TerminalFrame,
    Turn,
)

TURN_QUEUE_POLL_SECONDS = 0.1
TURN_QUEUE = Queue(
    TURN_QUEUE_NAME,
    concurrency=1,
    partition_queue=True,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


@dataclass(frozen=True)
class Runtime:
    config: Config
    blob: BlobStore
    hub: Hub


_runtime: Runtime | None = None


def init_runtime(runtime: Runtime) -> None:
    global _runtime
    if _runtime is not None:
        raise RuntimeError("runtime already initialized")
    _runtime = runtime


@DBOS.step(preemptible=True)
async def _execute_turn(turn_id: str) -> str:
    runtime = _runtime
    if runtime is None:
        raise RuntimeError("runtime not initialized (init_runtime runs in serve)")
    turn, agent = await _load_turn(UUID(turn_id))
    engine = TurnEngine(
        turn=turn,
        agent=agent,
        model=_model_client(agent.model, runtime.config),
        transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
        hub=runtime.hub,
    )
    frame = await engine.run()
    return frame.status


@DBOS.workflow(name=TURN_WORKFLOW_NAME)
async def turn_workflow(turn_id: str) -> str:
    return await _execute_turn(turn_id)


async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.workspace_id,
                    tables.turn.c.conversation_id,
                    tables.turn.c.agent_id,
                    tables.turn.c.seq,
                    tables.turn.c.status,
                    tables.turn.c.inbound,
                    tables.turn.c.terminal,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                )
                .select_from(tables.turn.join(tables.agent))
                .where(tables.turn.c.id == turn_id)
            )
        ).one()
    turn = Turn(
        id=row.id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        agent_id=row.agent_id,
        seq=row.seq,
        status=row.status,
        inbound=row.inbound,
        terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
    )
    return turn, Agent(prompt=row.prompt, model=row.model)


def _model_client(model: str, config: Config) -> ModelClient:
    if model.startswith(ANTHROPIC_MODEL_PREFIXES):
        key = _api_key(config.models.anthropic_api_key_env)
        return AnthropicClient(client=anthropic_sdk_client(key))
    if model.startswith(OPENAI_MODEL_PREFIXES):
        key = _api_key(config.models.openai_api_key_env)
        return OpenAIClient(client=openai_sdk_client(key))
    raise ValueError(f"no provider serves model {model!r}")


def _api_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if not key:
        raise RuntimeError(f"model api key env var {env_name} is not set")
    return key
