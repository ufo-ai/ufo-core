"""Boundary records and the queue contract shared by surfaces and workers."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from ufo.object_name import ObjectRef

TurnStatus = Literal["queued", "running", "parked", "done", "failed", "cancelled"]
TerminalStatus = Literal["done", "failed", "cancelled"]
IncompleteReason = Literal["round_budget"]
TurnAdmissionSource = Literal["member", "internal", "scheduled", "intent"]
ReasoningEffort = Literal["auto", "off", "low", "medium", "high"]
DEFAULT_REASONING_EFFORT: ReasoningEffort = "auto"
SandboxSize = Literal["small", "medium", "large"]
DEFAULT_SANDBOX_SIZE: SandboxSize = "small"
AgentVisibility = Literal["private", "workspace"]
DEFAULT_AGENT_VISIBILITY: AgentVisibility = "private"
NON_TERMINAL_STATUSES: tuple[TurnStatus, ...] = ("queued", "running", "parked")
ResultDelivery = Literal["pending", "delivered"]
DELIVERY_PENDING: ResultDelivery = "pending"
DELIVERY_DELIVERED: ResultDelivery = "delivered"
WritebackStatus = Literal["pending", "claimed", "delivered", "failed"]
WRITEBACK_PENDING: WritebackStatus = "pending"
WRITEBACK_CLAIMED: WritebackStatus = "claimed"
WRITEBACK_DELIVERED: WritebackStatus = "delivered"
WRITEBACK_FAILED: WritebackStatus = "failed"
RUNNING: TurnStatus = "running"
PARKED: TurnStatus = "parked"
CANCELLED: TerminalStatus = "cancelled"
ROUND_BUDGET_INCOMPLETE: IncompleteReason = "round_budget"
MEMBER_ADMISSION: TurnAdmissionSource = "member"
INTERNAL_ADMISSION: TurnAdmissionSource = "internal"
SCHEDULED_ADMISSION: TurnAdmissionSource = "scheduled"
INTENT_ADMISSION: TurnAdmissionSource = "intent"
SPAWN_RESULT_KEY_PREFIX = "subagent-result:"
SUBAGENT_SURFACE = "subagent"
PORTAL_SURFACE = "web"
EXTENSION_SURFACE_PREFIX = "extension:"

TABLER_ICON_MAX_LENGTH = 64
TABLER_ICON_PATTERN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
TablerIcon = Annotated[
    str,
    StringConstraints(pattern=rf"^{TABLER_ICON_PATTERN}$", max_length=TABLER_ICON_MAX_LENGTH),
]
"""An app icon's name. `AGENT_ICONS` is the set the portal offers and draws; any other name a
tabler outline mark answers still draws, so a name written before the portal's own pack arrived
keeps its mark."""

AGENT_ICONS: tuple[TablerIcon, ...] = (
    "propylon",
    "nabatu",
    "gibil",
    "adyton",
    "dingir",
    "akhet",
    "deltoton",
    "aten",
    "omphalos",
    "lekythos",
    "anthemion",
    "stele",
    "nirah",
    "nochtli",
    "ziggurat",
    "menhir",
    "nephele",
    "carnyx",
    "osculum",
    "denticulus",
    "gnomon",
    "triglyph",
    "acanthus",
    "ostrakon",
    "hydria",
    "wedjat",
    "krepis",
    "patera",
    "ashnan",
    "kylix",
    "furcula",
    "kalyx",
    "kardia",
    "gorgoneion",
    "thyrsus",
    "flabellum",
    "cedrus",
    "sesen",
    "shushan",
    "atef",
)
"""The icons the portal's picker offers, in the order it shows them: the element pack the portal
draws, the set a member chooses from by eye and the set an unnamed icon is dealt from. The
product's own mark is not one of them, so it is neither offered nor dealt."""

MAIN_AGENT_ICON: TablerIcon = "ufo"
"""The product's own mark, the icon the workspace's main agent is created with. It is reserved: the
picker never offers it and no agent is ever dealt it, while a row already holding it keeps drawing
it."""

DEFAULT_AGENT_ICON: TablerIcon = "propylon"
"""The icon a row inserted without one carries."""

AGENT_ICON_KEYWORDS: dict[str, TablerIcon] = {
    "support": "kardia",
    "help": "kardia",
    "desk": "kalyx",
    "sales": "krepis",
    "revenue": "krepis",
    "research": "omphalos",
    "analysis": "patera",
    "analytics": "patera",
    "data": "ziggurat",
    "sql": "ziggurat",
    "code": "stele",
    "dev": "stele",
    "engineer": "stele",
    "build": "stele",
    "bug": "nochtli",
    "qa": "nochtli",
    "ops": "menhir",
    "infra": "menhir",
    "deploy": "nabatu",
    "finance": "ashnan",
    "billing": "ashnan",
    "invoice": "ashnan",
    "legal": "atef",
    "contract": "atef",
    "people": "furcula",
    "hr": "furcula",
    "recruit": "furcula",
    "team": "furcula",
    "design": "sesen",
    "brand": "sesen",
    "write": "shushan",
    "writer": "shushan",
    "content": "shushan",
    "copy": "shushan",
    "mail": "carnyx",
    "email": "carnyx",
    "inbox": "carnyx",
    "calendar": "denticulus",
    "schedule": "gnomon",
    "task": "triglyph",
    "note": "acanthus",
    "doc": "ostrakon",
    "file": "hydria",
    "search": "wedjat",
    "scout": "aten",
    "security": "gorgoneion",
    "travel": "flabellum",
    "shop": "kylix",
    "map": "thyrsus",
    "idea": "akhet",
    "lab": "lekythos",
}

NAME_TOKENS = re.compile(r"[^a-z0-9]+")


def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon:
    """The icon a new agent starts with, drawn from `AGENT_ICONS`: the first name token a
    keyword names, else the name's hash over the icons the workspace has not used yet, so agents of
    one workspace read apart at a glance and a given name always lands on the same icon. Once every
    icon is taken the workspace repeats one."""
    seed = int.from_bytes(sha256(name.encode()).digest(), "big")
    keyword = next(
        (
            AGENT_ICON_KEYWORDS[token]
            for token in NAME_TOKENS.split(name.lower())
            if token in AGENT_ICON_KEYWORDS
        ),
        None,
    )
    if keyword is not None and keyword not in taken:
        return keyword
    free = tuple(icon for icon in AGENT_ICONS if icon not in taken)
    if not free:
        return keyword if keyword is not None else AGENT_ICONS[seed % len(AGENT_ICONS)]
    return free[seed % len(free)]


class ToolIntent(BaseModel):
    """A prepared panel mutation: one named tool call the turn dispatches verbatim — no model
    round, so the submitted values apply exactly or the refusal returns, never a paraphrase. The
    turn row is the audit record: the intent serializes as its inbound, the speaker is the
    submitting member, and the result commits as its terminal frame. The Literal is the closed
    whitelist of wire tools; a verb joins it with its panel producer, never ahead of one, and every
    bound action rides `object_action` with its kind, name, and input in the envelope — the
    credential collection's `request_credentials` mints the sealed private prompt a panel's set or
    replace fulfills against, so the secret itself never rides an intent."""

    tool: Literal[
        "object_apply",
        "object_delete",
        "object_action",
        "connect_account",
    ]
    input: dict[str, JsonValue]


BILLING_ACTION = ("workspace", "manage_billing")


def admits_spent_balance(intent: ToolIntent) -> bool:
    """Whether a prepared intent is the one a spent balance still admits: the workspace object's
    `manage_billing` action. Arranging a refill is what lifts the refusal, so a gate that refused it
    would refuse the only act that ends the refusal — the shape this system has built three times
    and had to unbuild. It is safe to admit because a prepared intent runs no model round: the turn
    dispatches this action verbatim and terminates, so an overdrawn workspace cannot spend against
    it, and the action's own admin gate still decides who may."""
    return (
        intent.tool == "object_action"
        and (
            intent.input.get("kind"),
            intent.input.get("action"),
        )
        == BILLING_ACTION
    )


ProposalStatus = Literal["pending", "approved", "rejected"]
PENDING: ProposalStatus = "pending"
APPROVED: ProposalStatus = "approved"
REJECTED: ProposalStatus = "rejected"

DEFAULT_AGENT_NAME = "chat"
TURN_QUEUE_NAME = "turns"
TURN_WORKFLOW_NAME = "turn"
DBOS_APP_NAME = "ufo"
DBOS_APP_VERSION = "ufo"
DBOS_MAX_EXECUTOR_THREADS = 8192
SURFACE_COMMENT_ROUND_INDEX = -1


def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID:
    """The turn id doubles as the DBOS workflow id: one identity, replay-idempotent."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/{conversation_id}/{seq}")


def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str = "") -> UUID:
    """One billing write per turn per dimension per run attempt (the DBOS workflow id of the run
    that spent the tokens). Replay of the same attempt collapses on this id; a resumed run is a
    distinct attempt, so a turn parked and resumed bills both partial burns — the true provider
    total — as separate rows the cap sum and terminal read aggregate."""
    return uuid5(NAMESPACE_URL, f"{workspace_id}/turn/{turn_id}/{dimension}/{attempt}")


def mid_turn_reply_id_for(
    turn_id: UUID, round_index: int, span_index: int, attempt: str = ""
) -> UUID:
    """The identity of one reply a turn speaks before it ends: the turn, the run attempt that wrote
    it (the DBOS workflow id of that run), the round, and the span's position in that round. A
    recovered workflow replays its recorded rounds under the same attempt and derives the same id
    for the same span, so the delivery record it writes again is the one already delivered and the
    member reads that reply once. A resumed run is a distinct attempt: its step log is empty, it
    rebuilds the window and counts its rounds from one again, so the spans it marks are new words
    under round and span numbers the parked attempt already used — they take ids of their own rather
    than collapsing onto replies the member has read."""
    return uuid5(NAMESPACE_URL, f"{turn_id}/reply/{attempt}/{round_index}/{span_index}")


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_30m_tokens: int = 0
    cache_write_1h_tokens: int = 0


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
    chosen: str | None = Field(
        default=None,
        description=(
            "The answer the member's own words already settle, so they read back what you took "
            "them to mean and correct it in place rather than answering twice. An option's exact "
            "label opens on that option; anything else opens in the row they type into. Leave it "
            "unset where their words do not carry the answer — a guess costs them a correction."
        ),
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
    icon: TablerIcon | None = Field(
        default=None,
        description=(
            "A tabler icon name drawing what the ask is about. It is the mark beside the title on "
            "a surface that draws one."
        ),
    )


class CredentialPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


class ConnectRequest(BaseModel):
    """The `connect_account` tool's structured terminal handoff; `shared` carries the model's
    disclosure decision to the grant. The exact requester is durable while the authorization URL
    is minted only after that member privately claims it.

    `grantee_agent_id` names the agent the connection is granted to when that is not the asking
    agent — the main agent connecting an account on behalf of an agent that cannot ask for itself.
    It is resolved and gated when the request is made, so the durable request already names the
    agent the seal will bind, and no later step re-decides it."""

    provider: str
    requester_member_id: UUID
    shared: bool = False
    grantee_agent_id: UUID | None = None


TERMINAL_ERROR_MESSAGE_MAX_CHARS = 2_000

FinalActRule = Literal["last_call", "pending"]
LAST_CALL_ACT: FinalActRule = "last_call"
PENDING_ACT: FinalActRule = "pending"

FINAL_ACT_FIELDS: dict[type[BaseModel], tuple[str, FinalActRule]] = {
    AskUserInput: ("question", LAST_CALL_ACT),
    CredentialRequest: ("credential_request", PENDING_ACT),
    ConnectRequest: ("connect_request", PENDING_ACT),
}
"""Every final act a turn can leave open: the payload model a callable declares as
`final_act_model`, the `TerminalFrame` field that carries it, and the rule that reads it from the
round. A question is stale unless it was the round's last call; a credential or connect handoff is
owed from anywhere in the round and persists until the turn ends, because only the member
discharges it. A declared `final_act_model` outside this mapping fails boot — the frame has no
field to carry it."""


class TerminalFrame(BaseModel):
    status: TerminalStatus
    text: str = ""
    incomplete_reason: IncompleteReason | None = None
    error_class: str | None = None
    error_message: str | None = None
    tokens: int = 0
    cost_micro_usd: int = 0
    cache_percent: int = Field(default=0, ge=0, le=100)
    model: str = ""
    reasoning: ReasoningEffort | None = None
    question: AskUserInput | None = None
    credential_request: CredentialRequest | None = None
    connect_request: ConnectRequest | None = None
    created: tuple[ObjectRef, ...] = ()
    """The workspace objects a completed turn created — an `object_apply` that landed on a name no
    object held — in the order it created them. What happened, not how to draw it: a surface
    decides which kinds it draws and what it draws them as."""


class RuntimeIdentity(BaseModel):
    """The service and sandbox runtime one remote turn ran against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: str | None = Field(default=None, min_length=1)
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    config_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sandbox_backend: str = Field(min_length=1)
    sandbox_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _artifact_pair(self) -> "RuntimeIdentity":
        if (self.revision is None) != (self.image_digest is None):
            raise ValueError("runtime revision and image digest must be set together")
        return self


class RuntimeAttestation(BaseModel):
    """A turn-ending frame's actual model settings bound to its running service identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: RuntimeIdentity
    model: str = ""
    reasoning: ReasoningEffort | None = None


class Agent(BaseModel):
    prompt: str
    model: str
    reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT
    is_main: bool = False
    """Whether this is the workspace's one main agent — the only agent that may grant an account to
    another, so the only one told which shipped agents are still waiting."""
    tools: tuple[str, ...] | None = None
    """The agent's tool allowlist, or None for the member-facing set. A name the live registry does
    not answer is absent rather than an error: an extension the deploy stopped installing leaves the
    agent short a tool, never unable to take a turn."""
    output_schema: dict[str, object] | None = None
    """The agent's declared output contract as raw JSON Schema, or None for the default result
    contract. A spawned turn of this agent binds it as the finish tool's input schema."""
    internet_access_allowed: bool = True
    """Whether this agent's sandbox reaches the public internet — the per-agent narrowing the proxy
    also gates on. Governs whether the turn's sandbox is routed through the egress cache."""
    use_workspace_skills: bool = True
    """Whether this agent's turns load the workspace skill set — the member-authored skills the
    portal's workspace page manages. Off, no member-authored skill reaches its turns."""
    name: str = ""
    """The agent's stable name — what a skill's frontmatter `agents` targeting names. Empty where
    no row backs the record, which matches no targeting, so a targeted skill never loads there."""


class TurnContext(BaseModel):
    """Ambient facts the admitting surface knows about an inbound — who spoke, their IANA timezone,
    the question their message answers when the surface knew one, and where they said it — carried
    on the turn row and rendered by the engine as the <context> tag before the message. `source` is
    one line naming the request's origin in whatever form the surface has: a permalink to the
    message itself where the surface addresses messages, else the client and the member's address.
    The free-text fields are made safe at construction: sender, question, and source (all
    surface-reported) are flattened to one line without angle brackets so they cannot forge tag
    structure, and a bad zone fails at the surface, never mid-turn."""

    sender: str | None = None
    timezone: str | None = None
    question: str | None = None
    source: str | None = None

    @field_validator("sender", "question", "source")
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
    updated_at: datetime | None = None
    admission_source: TurnAdmissionSource = INTERNAL_ADMISSION
    speaker_member_id: UUID | None = None
    on_behalf_of_member_id: UUID | None = None
    context: TurnContext | None = None
    terminal: TerminalFrame | None = None
    created_refs: tuple[ObjectRef, ...] = ()
    """The objects this turn has created so far, written the round that created each — durable
    ahead of any terminal, so a resume seeds from it and a canceller names it. The terminal's
    `created` is this record's final copy."""
    parent_turn_id: UUID | None = None
    subagent_profile: str | None = None
    subagent_name: str | None = None
    connect_landed_at: datetime | None = None
    """When the connect this turn asked for landed, stamped by the callback that recorded the grant.
    What says one request was answered, where the account alone cannot: a member may hold two on one
    provider, and may reconnect from another conversation entirely."""
    result_delivery: ResultDelivery | None = None
    sandbox_conversation_id: UUID | None = None
    traceparent: str | None = None

    @property
    def spawned(self) -> bool:
        """True for a turn a spawn admitted — a profile child (`subagent_profile` set) or an agent
        child (parent linkage alone). Only the spawn path writes `parent_turn_id`."""
        return self.parent_turn_id is not None

    @field_validator("created_refs", mode="before")
    @classmethod
    def _nothing_created(cls, value: object) -> object:
        """The column is nullable and only a round that created something writes it, so a turn
        that created nothing carries SQL NULL — which reads as the empty set."""
        return () if value is None else value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        """The row's timestamp is UTC by construction; a driver that drops the marker (sqlite)
        hands it back naive, which `astimezone` would misread as local time."""
        if value is None:
            return None
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
