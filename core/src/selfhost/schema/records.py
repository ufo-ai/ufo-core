"""Boundary records and the queue contract shared by surfaces and workers."""

from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, model_validator

TurnStatus = Literal["queued", "running", "parked", "done", "failed", "cancelled"]
TerminalStatus = Literal["done", "failed", "cancelled"]
NON_TERMINAL_STATUSES: tuple[TurnStatus, ...] = ("queued", "running", "parked")
PARKED: TurnStatus = "parked"

ProposalStatus = Literal["pending", "approved", "rejected"]
PENDING: ProposalStatus = "pending"
APPROVED: ProposalStatus = "approved"
REJECTED: ProposalStatus = "rejected"

DEFAULT_AGENT_NAME = "assistant"
TURN_QUEUE_NAME = "turns"
TURN_WORKFLOW_NAME = "turn"
DBOS_APP_NAME = "selfhost"
DBOS_APP_VERSION = "selfhost"


def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID:
    """The turn id doubles as the DBOS workflow id: one identity, replay-idempotent."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/{conversation_id}/{seq}")


def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str = "") -> UUID:
    """One billing write per turn per dimension per run attempt (the DBOS workflow id of the run
    that spent the tokens). Replay of the same attempt collapses on this id; a resumed run is a
    distinct attempt, so a turn parked and resumed bills both partial burns — the true provider
    total — as separate rows the cap sum and terminal read aggregate."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/turn/{turn_id}/{dimension}/{attempt}")


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
    parent_turn_id: UUID | None = None
    subagent_profile: str | None = None

    @model_validator(mode="after")
    def _terminal_matches_status(self) -> "Turn":
        if (self.status in NON_TERMINAL_STATUSES) != (self.terminal is None):
            raise ValueError("terminal is present exactly when the turn is terminal")
        if self.terminal is not None and self.terminal.status != self.status:
            raise ValueError("terminal.status must equal turn.status")
        return self


class AgentChange(BaseModel):
    """A proposed edit to an agent's prompt, based on the prompt digest the proposer diffed
    against; the digest is re-checked at approval so a moved base rejects the change."""

    agent_id: UUID
    new_prompt: str
    from_digest: str


@dataclass(frozen=True)
class ProposalRef:
    proposal_id: UUID


