"""Boundary records and the queue contract shared by surfaces and workers."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

TurnStatus = Literal["queued", "running", "parked", "done", "failed", "cancelled"]
TerminalStatus = Literal["done", "failed", "cancelled"]
TurnAdmissionSource = Literal["member", "internal", "scheduled"]
ReasoningEffort = Literal["off", "low", "medium", "high"]
DEFAULT_REASONING_EFFORT: ReasoningEffort = "high"
NON_TERMINAL_STATUSES: tuple[TurnStatus, ...] = ("queued", "running", "parked")
WritebackStatus = Literal["pending", "claimed", "delivered", "failed"]
WRITEBACK_PENDING: WritebackStatus = "pending"
WRITEBACK_CLAIMED: WritebackStatus = "claimed"
WRITEBACK_DELIVERED: WritebackStatus = "delivered"
WRITEBACK_FAILED: WritebackStatus = "failed"
RUNNING: TurnStatus = "running"
PARKED: TurnStatus = "parked"
MEMBER_ADMISSION: TurnAdmissionSource = "member"
INTERNAL_ADMISSION: TurnAdmissionSource = "internal"
SCHEDULED_ADMISSION: TurnAdmissionSource = "scheduled"

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


class CredentialPrompt(BaseModel):
    slot: str = Field(description="The credential slot to fill.")
    prompt: str = Field(description="What to show the member when asking for this value.")


class CredentialRequest(BaseModel):
    """The `request_credentials` tool's structured output and, verbatim, what a terminal frame
    carries when collecting secrets was the turn's final act. A capable surface prompts the member
    for each value privately and fulfills against the sealed grant — the entered secrets never
    touch the transcript or the sandbox."""

    reason: str
    prompts: tuple[CredentialPrompt, ...]
    sealed: str


class TerminalFrame(BaseModel):
    status: TerminalStatus
    text: str = ""
    error_class: str | None = None
    tokens: int = 0
    cost_micro_usd: int = 0
    cache_percent: int = Field(default=0, ge=0, le=100)
    model: str = ""
    reasoning: ReasoningEffort | None = None
    question: AskUserInput | None = None
    credential_request: CredentialRequest | None = None


class Agent(BaseModel):
    prompt: str
    model: str


class TurnContext(BaseModel):
    """Ambient facts the admitting surface knows about an inbound — who spoke and their IANA
    timezone — carried on the turn row and rendered by the engine as the <context> tag before the
    message. Both fields are made safe at construction: the sender (surface-reported free text) is
    flattened to one line without angle brackets so it cannot forge tag structure, and a bad zone
    fails at the surface, never mid-turn."""

    sender: str | None = None
    timezone: str | None = None

    @field_validator("sender")
    @classmethod
    def _tag_safe_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        flattened = " ".join(value.replace("<", "").replace(">", "").split())
        return flattened or None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown timezone: {value}") from error
        return value


class Turn(BaseModel):
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    seq: int
    status: TurnStatus
    inbound: str
    created_at: datetime
    admission_source: TurnAdmissionSource = INTERNAL_ADMISSION
    context: TurnContext | None = None
    terminal: TerminalFrame | None = None
    parent_turn_id: UUID | None = None
    subagent_profile: str | None = None
    traceparent: str | None = None

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        """The row's timestamp is UTC by construction; a driver that drops the marker (sqlite)
        hands it back naive, which `astimezone` would misread as local time."""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

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
