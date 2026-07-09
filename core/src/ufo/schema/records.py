"""Boundary records and the queue contract shared by surfaces and workers."""

from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, Field, model_validator

TurnStatus = Literal["queued", "running", "parked", "done", "failed", "cancelled"]
TerminalStatus = Literal["done", "failed", "cancelled"]
NON_TERMINAL_STATUSES: tuple[TurnStatus, ...] = ("queued", "running", "parked")
WritebackStatus = Literal["pending", "claimed", "delivered", "failed"]
WRITEBACK_PENDING: WritebackStatus = "pending"
WRITEBACK_CLAIMED: WritebackStatus = "claimed"
WRITEBACK_DELIVERED: WritebackStatus = "delivered"
WRITEBACK_FAILED: WritebackStatus = "failed"
RUNNING: TurnStatus = "running"
PARKED: TurnStatus = "parked"

ProposalStatus = Literal["pending", "approved", "rejected"]
PENDING: ProposalStatus = "pending"
APPROVED: ProposalStatus = "approved"
REJECTED: ProposalStatus = "rejected"

DEFAULT_AGENT_NAME = "assistant"
TURN_QUEUE_NAME = "turns"
TURN_WORKFLOW_NAME = "turn"
DBOS_APP_NAME = "ufo"
DBOS_APP_VERSION = "ufo"


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


MAX_USER_QUESTIONS = 4


class QuestionOption(BaseModel):
    label: str = Field(description="The choice shown to the user.")
    description: str | None = Field(
        default=None, description="Optional explanation of what the choice means."
    )


class AskQuestion(BaseModel):
    question: str = Field(description="The question to ask.")
    options: tuple[QuestionOption, ...] | None = Field(
        default=None, description="The choices to present; omit for a free-text answer."
    )
    multi_select: bool | None = Field(
        default=None, description="Allow selecting more than one option."
    )
    free_text_only: bool | None = Field(
        default=None, description="Force a free-text answer even when options are given."
    )
    header: str | None = Field(default=None, description="Short label shown above the question.")
    allow_attachments: bool | None = Field(
        default=None, description="Let the user attach files in their answer."
    )


class AskUserInput(BaseModel):
    """The `ask_user` tool's input and, verbatim, the structured question a terminal frame carries
    when asking was the turn's final act — one record from the model's call to the surface that
    renders it (buttons on Slack), never re-shaped in between."""

    title: str = Field(
        description="Brief, friendly prompt explaining why you need more info, shown at the top. "
        "Should feel conversational and explain the value of answering."
    )
    questions: tuple[AskQuestion, ...] = Field(
        min_length=1, max_length=MAX_USER_QUESTIONS, description="1-4 questions to ask."
    )


class TerminalFrame(BaseModel):
    status: TerminalStatus
    text: str = ""
    error_class: str | None = None
    tokens: int = 0
    cost_micro_usd: int = 0
    model: str = ""
    question: AskUserInput | None = None


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
    traceparent: str | None = None

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
