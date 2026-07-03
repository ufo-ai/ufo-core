"""Boundary records and the queue contract shared by surfaces and workers."""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, model_validator

TurnStatus = Literal["queued", "running", "done", "failed", "cancelled"]
TerminalStatus = Literal["done", "failed", "cancelled"]

DEFAULT_AGENT_NAME = "assistant"
TURN_QUEUE_NAME = "turns"
TURN_WORKFLOW_NAME = "turn"
DBOS_APP_NAME = "selfhost"
DBOS_APP_VERSION = "selfhost"


def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID:
    """The turn id doubles as the DBOS workflow id: one identity, replay-idempotent."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/{conversation_id}/{seq}")


def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str) -> UUID:
    """One billing write per turn per dimension, at terminal commit — replay collapses on id."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/turn/{turn_id}/{dimension}")


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class TerminalFrame(BaseModel):
    status: TerminalStatus
    text: str = ""
    error_class: str | None = None
    tokens: int = 0
    cost_micro_usd: int = 0
    model: str = ""


class Agent(BaseModel):
    prompt: str
    model: str


class Turn(BaseModel):
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    seq: int
    status: TurnStatus
    inbound: str
    terminal: TerminalFrame | None = None

    @model_validator(mode="after")
    def _terminal_matches_status(self) -> "Turn":
        if (self.status in ("queued", "running")) != (self.terminal is None):
            raise ValueError("terminal is present exactly when the turn is terminal")
        if self.terminal is not None and self.terminal.status != self.status:
            raise ValueError("terminal.status must equal turn.status")
        return self
