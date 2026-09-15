"""Member authorization for an exact agent effect selected from a multi-speaker turn."""

import asyncio
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from unicodedata import category
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.models.interface import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.harness.o11y import REDACTED, JsonValue, emit_metric, log, redact_value
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import AskQuestion, AskUserInput, QuestionOption, ReasoningEffort

AuthorizationDecision = Literal["allow", "always", "deny", "revoke", "ask"]
AuthorizationBasis = Literal["selected_message", "pending_answer", "standing", "none"]
AuthorizationAccess = Literal["read", "write"]

MEMBER_AUTHORIZATION_MODEL = "gpt-5.6-luna"
MEMBER_AUTHORIZATION_JOB = "core:member_authorization"
MEMBER_AUTHORIZATION_TOOL = "decide_authorization"
MEMBER_AUTHORIZATION_MAX_TOKENS = 512
MEMBER_AUTHORIZATION_MESSAGE_CHARS = 2_000
MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS = 2_000
MEMBER_AUTHORIZATION_ACTIVE_MESSAGES = 8
MEMBER_AUTHORIZATION_RECENT_MESSAGES = 12
MEMBER_AUTHORIZATION_CONTEXT_CHARS = 8_000
MEMBER_AUTHORIZATION_EFFECT_CHARS = 8_000
MEMBER_AUTHORIZATION_FACT_VALUE_CHARS = 80
MEMBER_AUTHORIZATION_FACT_CHARS = 1_200
MEMBER_AUTHORIZATION_EVIDENCE_CHARS = 500
MEMBER_AUTHORIZATION_SUMMARY_CHARS = 240
MEMBER_AUTHORIZATION_TIMEOUT_SECONDS = 12
MEMBER_AUTHORIZATION_REASONING: ReasoningEffort = "low"
SUPERSEDED_AUTHORIZATION_DECISION = "superseded"
MEMBER_AUTHORIZATION_UNDISCLOSABLE = (
    "This request contains details that cannot be safely shown for approval."
)
MEMBER_AUTHORIZATION_ANSWER_INVALID = "This approval does not match the pending request."
MEMBER_AUTHORIZATION_SECRET_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "passcode",
    "credential",
    "cookie",
    "privatekey",
    "authorization",
    "apikey",
    "accesskey",
    "clientkey",
    "signingkey",
)
MEMBER_AUTHORIZATION_SYSTEM = (
    (Path(__file__).parent.parent / "prompts" / "member_authorization.md").read_text().strip()
)


def _fact_lines(values: dict[str, JsonValue], prefix: str = "") -> list[str]:
    """One labeled line per leaf field, nested objects flattened under their path — a connector
    write keeps its provider payload under `arguments`, and the recipient inside it is the fact the
    member is approving. A key the model supplied renders as words only while it is plain text;
    otherwise it renders quoted with its control characters escaped, so no key or value can break
    a line and forge a fact beside the real one."""
    lines: list[str] = []
    for key, value in values.items():
        words = key.replace("_", " ")
        label = prefix + (words if _plain(words) else json.dumps(key)[:_QUOTED_LABEL_CHARS])
        match value:
            case dict() if value:
                lines.extend(_fact_lines(value, f"{label} "))
            case list() if value and all(isinstance(item, dict) for item in value):
                for index, item in enumerate(value, 1):
                    match item:
                        case dict():
                            lines.extend(_fact_lines(item, f"{label} {index} "))
            case _:
                lines.append(f"{label[:1].upper()}{label[1:]}: {_fact_value(value)}")
    return lines


_QUOTED_LABEL_CHARS = MEMBER_AUTHORIZATION_FACT_VALUE_CHARS + 2


def _plain(text: str) -> bool:
    """Short text that renders as one line everywhere: no control character, and neither
    U+2028 nor U+2029, which `str.splitlines` and browsers break on while Unicode files them under
    separators rather than controls."""
    return len(text) <= MEMBER_AUTHORIZATION_FACT_VALUE_CHARS and not any(
        category(character)[0] == "C" or category(character) in ("Zl", "Zp") for character in text
    )


def _fact_value(value: JsonValue) -> str:
    match value:
        case None | [] | {}:
            return "none"
        case bool():
            return "true" if value else "false"
        case int() | float():
            return str(value)
        case str() if _plain(value):
            return value
        case str():
            return f"{len(value):,} characters"
        case list() if all(not isinstance(item, dict | list) for item in value):
            joined = ", ".join(_fact_value(item) for item in value)
            return joined if _plain(joined) else f"{len(value)} items"
        case list():
            return f"{len(value)} items"
        case dict():
            return f"{len(value)} fields"


def _disclose_value(value: JsonValue) -> tuple[JsonValue, bool]:
    match value:
        case None:
            return None, True
        case dict():
            disclosed: dict[str, JsonValue] = {}
            complete = True
            for key, item in value.items():
                normalized = "".join(
                    character for character in key.casefold() if character.isalnum()
                )
                if normalized == "auth" or any(
                    marker in normalized for marker in MEMBER_AUTHORIZATION_SECRET_MARKERS
                ):
                    disclosed[key] = REDACTED
                    complete = False
                    continue
                disclosed[key], item_complete = _disclose_value(item)
                complete = complete and item_complete
            return disclosed, complete
        case list():
            disclosed_items: list[JsonValue] = []
            complete = True
            for item in value:
                disclosed_item, item_complete = _disclose_value(item)
                disclosed_items.append(disclosed_item)
                complete = complete and item_complete
            return disclosed_items, complete
        case str():
            if value == "**********":
                return REDACTED, False
            disclosed_text = redact_value(value)
            assert isinstance(disclosed_text, str)
            return disclosed_text, disclosed_text == value
        case _:
            return value, True


class AuthorizationEffect(BaseModel):
    """The exact validated call, arguments, and requested object target one decision covers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call: str
    arguments: dict[str, JsonValue]
    target: dict[str, JsonValue] | None = None

    def digest(self, key: bytes) -> str:
        """The keyed stable identity of these exact JSON semantics."""
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return hmac.digest(key, b"ufo/member-authorization\0" + encoded, "sha256").hex()

    def stored(self) -> dict[str, JsonValue]:
        """Return the exact persistable projection, rejecting any withheld value."""
        disclosed, complete = self.disclosure()
        if not complete:
            raise ValueError("undisclosable authorization effects cannot be persisted")
        return disclosed

    def disclosure(self) -> tuple[dict[str, JsonValue], bool]:
        """Return the safe semantic projection and whether it preserves the exact effect."""
        arguments, arguments_complete = _disclose_value(self.arguments)
        target, target_complete = _disclose_value(self.target)
        assert isinstance(arguments, dict)
        assert target is None or isinstance(target, dict)
        return {
            "call": self.call,
            "arguments": arguments,
            "target": target,
        }, arguments_complete and target_complete


class AuthorizationScope(BaseModel):
    """One code-defined connected-account operation a standing permission covers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=512)
    operation: str = Field(min_length=1, max_length=256)
    access: AuthorizationAccess

    def digest(self, key: bytes) -> str:
        """The keyed stable identity of this reusable authority."""
        encoded = json.dumps(
            self.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        ).encode()
        return hmac.digest(key, b"ufo/member-permission\0" + encoded, "sha256").hex()


class AuthorizationBinding(BaseModel):
    """The opaque live connector generation and classification one execution resolved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: UUID
    grant_id: UUID
    provider: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=512)
    operation: str = Field(min_length=1, max_length=256)
    access: AuthorizationAccess

    def digest(self, key: bytes) -> str:
        """The keyed stable identity of this exact execution binding."""
        encoded = json.dumps(
            self.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        ).encode()
        return hmac.digest(key, b"ufo/member-authorization-binding\0" + encoded, "sha256").hex()


class AuthorizationContextMessage(BaseModel):
    """One bounded, attributed message in an authorization decision's context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    member_id: UUID | None
    reply_to: str | None = Field(default=None, max_length=128)
    text: str = Field(max_length=MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS)


class AuthorizationContext(BaseModel):
    """The bounded structural conversation context one authorization model may use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: AuthorizationContextMessage
    direct_reply_parent: AuthorizationContextMessage | None = None
    assistant_proposal: AuthorizationContextMessage | None = None
    active_messages: tuple[AuthorizationContextMessage, ...] = Field(
        default=(), max_length=MEMBER_AUTHORIZATION_ACTIVE_MESSAGES
    )
    recent_messages: tuple[AuthorizationContextMessage, ...] = Field(
        default=(), max_length=MEMBER_AUTHORIZATION_RECENT_MESSAGES
    )

    @model_validator(mode="after")
    def validate_context(self) -> "AuthorizationContext":
        if self.selected.role != "user" or self.selected.member_id is None:
            raise ValueError("authorization selection must be an attributed user message")
        if self.direct_reply_parent is not None and (
            self.selected.reply_to != self.direct_reply_parent.ref
        ):
            raise ValueError("authorization reply parent is not linked to the selected message")
        if self.assistant_proposal is not None and self.assistant_proposal.role != "assistant":
            raise ValueError("authorization proposal must be an assistant message")
        if self.assistant_proposal is not None and (
            self.selected.reply_to != self.assistant_proposal.ref
        ):
            raise ValueError("authorization proposal is not linked to the selected message")
        messages = (
            self.selected,
            *((self.direct_reply_parent,) if self.direct_reply_parent is not None else ()),
            *((self.assistant_proposal,) if self.assistant_proposal is not None else ()),
            *self.active_messages,
            *self.recent_messages,
        )
        unique: dict[str, AuthorizationContextMessage] = {}
        for message in messages:
            if message.ref in unique and unique[message.ref] != message:
                raise ValueError("authorization context repeats a message ref with different data")
            unique[message.ref] = message
        if (
            sum(len(message.text) for message in unique.values())
            > MEMBER_AUTHORIZATION_CONTEXT_CHARS
        ):
            raise ValueError("authorization context exceeds its text bound")
        return self


class AuthorizationVerdict(BaseModel):
    """One forced model decision and its grounded current-message evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: AuthorizationDecision
    basis: AuthorizationBasis
    evidence: str = Field(max_length=MEMBER_AUTHORIZATION_EVIDENCE_CHARS)
    selected_message_ref: UUID
    supporting_message_ref: str | None = Field(default=None, max_length=128)
    request_summary: str = Field(min_length=1, max_length=MEMBER_AUTHORIZATION_SUMMARY_CHARS)
    scope_summary: str | None = Field(default=None, max_length=MEMBER_AUTHORIZATION_SUMMARY_CHARS)

    @field_validator("request_summary", "scope_summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = " ".join(value.split())
        if (
            not clean
            or "<" in clean
            or ">" in clean
            or any(category(character).startswith("C") for character in clean)
        ):
            raise ValueError("authorization summaries must be one line of plain text")
        return clean


class AuthorizationAnswer(BaseModel):
    """One authenticated structured choice for the exact pending authorization question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: UUID
    choice: Literal["allow", "deny", "always"]


@dataclass(frozen=True)
class AuthorizationRequest:
    """One selected member message requesting authority for one exact effect."""

    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    agent_name: str
    member_id: UUID
    dispatch_key: str
    message_ref: UUID
    message: str
    context: AuthorizationContext
    effect: AuthorizationEffect
    scope: AuthorizationScope | None
    binding: AuthorizationBinding | None
    selected_from_multiple: bool
    message_complete: bool = True
    answer: AuthorizationAnswer | None = None

    def __post_init__(self) -> None:
        selected = self.context.selected
        if (
            selected.ref != str(self.message_ref)
            or selected.member_id != self.member_id
            or selected.text != self.message
        ):
            raise ValueError("authorization context does not match its selected message")
        if (self.scope is None) != (self.binding is None):
            raise ValueError("authorization scope and execution binding must be provided together")
        if self.scope is not None and self.binding is not None:
            stable = AuthorizationScope(
                provider=self.binding.provider,
                account_id=self.binding.account_id,
                operation=self.binding.operation,
                access=self.binding.access,
            )
            if self.scope != stable:
                raise ValueError("authorization scope does not match its execution binding")


@dataclass(frozen=True)
class AuthorizationResolution:
    """The dispatch gate's admitted, denied, or member-question outcome."""

    decision: Literal["allow", "deny", "ask"]
    question: AskUserInput | None = None
    refusal: str | None = None
    requesting_message_ref: UUID | None = None


@dataclass(frozen=True)
class AuthorizationAttempt:
    """One side-model verdict bound to the exact request and durable state it classified."""

    request: AuthorizationRequest
    verdict: AuthorizationVerdict | None = None
    pending_id: UUID | None = None
    permission_id: UUID | None = None
    replay: Literal["allow", "deny", "ask"] | None = None
    consumed: bool = False
    refusal: str | None = None


class AuthorizationModel(Protocol):
    """The forced-tool model seam used for member authorization decisions."""

    @property
    def model(self) -> str: ...

    async def turn(self, request: ModelRequest) -> Message: ...


class AuthorizationGate(Protocol):
    """Adjudicate exact member effects and report pending conversation decisions."""

    async def preflight(self, request: AuthorizationRequest) -> AuthorizationAttempt: ...

    async def authorize(
        self, request: AuthorizationRequest, attempt: AuthorizationAttempt | None = None
    ) -> AuthorizationResolution: ...

    async def has_pending(
        self, workspace_id: UUID, conversation_id: UUID, member_ids: frozenset[UUID]
    ) -> bool: ...


@dataclass(frozen=True)
class _PendingAuthorization:
    id: UUID
    requested_by: UUID
    call: str
    effect_digest: str
    effect: dict[str, JsonValue]
    binding_digest: str | None
    request_summary: str | None
    scope_summary: str | None


@dataclass(frozen=True)
class _AuthorizationState:
    email: str
    pending: _PendingAuthorization | None
    permission_id: UUID | None
    replay: Literal["allow", "deny", "ask"] | None
    replay_requesting_message_ref: UUID | None
    consumed: bool


class _SettlementConflict(Exception):
    pass


@dataclass(frozen=True)
class MemberAuthorization:
    """Adjudicate and persist member consent before private capabilities reach a tool call."""

    model: AuthorizationModel
    digest_key: bytes

    def __post_init__(self) -> None:
        if not self.digest_key:
            raise ValueError("member authorization digest key is required")

    async def has_pending(
        self, workspace_id: UUID, conversation_id: UUID, member_ids: frozenset[UUID]
    ) -> bool:
        if not member_ids:
            return False
        async with workspace_tx() as connection:
            return bool(
                (
                    await connection.execute(
                        sa.select(tables.member_authorization.c.id)
                        .where(
                            tables.member_authorization.c.workspace_id == workspace_id,
                            tables.member_authorization.c.conversation_id == conversation_id,
                            tables.member_authorization.c.member_id.in_(member_ids),
                            tables.member_authorization.c.decision.is_(None),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
            )

    async def preflight(self, request: AuthorizationRequest) -> AuthorizationAttempt:
        effect = self._effect_json(request.effect)
        if effect is None:
            return AuthorizationAttempt(
                request=request,
                refusal=MEMBER_AUTHORIZATION_UNDISCLOSABLE,
            )
        state = await self._state(request)
        verdict = await self._candidate(request, state, effect)
        return AuthorizationAttempt(
            request=request,
            verdict=verdict,
            pending_id=None if state.pending is None else state.pending.id,
            permission_id=state.permission_id,
            replay=state.replay,
            consumed=state.consumed,
        )

    async def authorize(
        self, request: AuthorizationRequest, attempt: AuthorizationAttempt | None = None
    ) -> AuthorizationResolution:
        prepared = attempt if attempt is not None and attempt.request == request else None
        if prepared is None:
            prepared = await self.preflight(request)
        if prepared.refusal is not None:
            return AuthorizationResolution("deny", refusal=prepared.refusal)
        state = await self._state(request)
        if state.replay is not None:
            return AuthorizationResolution(
                state.replay,
                self._question(request, state) if state.replay == "ask" else None,
                requesting_message_ref=state.replay_requesting_message_ref,
            )
        if request.answer is not None:
            return await self._answer(request, state)
        return await self._authorize_selected(request, prepared, state)

    async def _authorize_selected(
        self,
        request: AuthorizationRequest,
        prepared: AuthorizationAttempt,
        state: _AuthorizationState,
    ) -> AuthorizationResolution:
        if state.pending is not None and state.pending.requested_by == request.message_ref:
            return AuthorizationResolution("ask", self._question(request, state))
        if state.pending is not None and not self._pending_matches(request, state):
            await self._supersede_pending(request)
            state = await self._state(request)
            if state.replay is not None:
                return AuthorizationResolution(
                    state.replay,
                    self._question(request, state) if state.replay == "ask" else None,
                    requesting_message_ref=state.replay_requesting_message_ref,
                )
            if state.pending is not None:
                return AuthorizationResolution("ask", self._question(request, state))
        if state.pending is not None:
            return AuthorizationResolution("ask", self._question(request, state))
        if state.consumed and state.permission_id is None:
            return AuthorizationResolution("deny")
        verdict = await self._verdict(request, prepared, state)
        if verdict is None and not request.selected_from_multiple:
            return AuthorizationResolution("allow", requesting_message_ref=request.message_ref)
        if verdict is None or not self._grounded(verdict, request, state):
            request_summary, scope_summary = self._fallback_summaries(request)
            verdict = AuthorizationVerdict(
                decision="ask",
                basis="none",
                evidence="",
                selected_message_ref=request.message_ref,
                request_summary=request_summary,
                scope_summary=scope_summary,
            )
        if verdict.decision == "ask":
            await self._ensure_pending(
                request,
                None if state.pending is None else state.pending.id,
                verdict,
            )
            fresh = await self._state(request)
            if fresh.replay is not None:
                return AuthorizationResolution(
                    fresh.replay,
                    self._question(request, fresh) if fresh.replay == "ask" else None,
                    requesting_message_ref=fresh.replay_requesting_message_ref,
                )
            if fresh.pending is None:
                return AuthorizationResolution("deny")
            return AuthorizationResolution("ask", self._question(request, fresh))
        if not await self._settle(request, state, verdict):
            fresh = await self._state(request)
            if fresh.replay is not None:
                return AuthorizationResolution(
                    fresh.replay,
                    self._question(request, fresh) if fresh.replay == "ask" else None,
                    requesting_message_ref=fresh.replay_requesting_message_ref,
                )
            return AuthorizationResolution("deny")
        if verdict.decision in ("deny", "revoke"):
            return AuthorizationResolution("deny")
        return AuthorizationResolution("allow", requesting_message_ref=request.message_ref)

    async def _answer(
        self,
        request: AuthorizationRequest,
        state: _AuthorizationState,
    ) -> AuthorizationResolution:
        answer = request.answer
        assert answer is not None
        if (
            state.pending is None
            or answer.authorization_id != state.pending.id
            or not self._pending_matches(request, state)
            or state.pending.requested_by == request.message_ref
            or (answer.choice == "always" and request.scope is None)
        ):
            return AuthorizationResolution("deny", refusal=MEMBER_AUTHORIZATION_ANSWER_INVALID)
        verdict = AuthorizationVerdict(
            decision=answer.choice,
            basis="pending_answer",
            evidence=answer.choice,
            selected_message_ref=request.message_ref,
            request_summary=(state.pending.request_summary or self._fallback_summaries(request)[0]),
            scope_summary=(state.pending.scope_summary or self._fallback_summaries(request)[1]),
        )
        if not await self._settle(request, state, verdict):
            fresh = await self._state(request)
            if fresh.replay is not None:
                return AuthorizationResolution(
                    fresh.replay,
                    self._question(request, fresh) if fresh.replay == "ask" else None,
                    requesting_message_ref=fresh.replay_requesting_message_ref,
                )
            return AuthorizationResolution("deny", refusal=MEMBER_AUTHORIZATION_ANSWER_INVALID)
        return AuthorizationResolution(
            "deny" if answer.choice == "deny" else "allow",
            requesting_message_ref=state.pending.requested_by,
        )

    async def _verdict(
        self,
        request: AuthorizationRequest,
        prepared: AuthorizationAttempt,
        state: _AuthorizationState,
    ) -> AuthorizationVerdict | None:
        if self._attempt_matches(prepared, state):
            return prepared.verdict
        if prepared.verdict is not None and prepared.verdict.basis == "standing":
            return None
        effect_json = self._effect_json(request.effect)
        if effect_json is None:
            return None
        return await self._candidate(request, state, effect_json)

    def _attempt_matches(self, attempt: AuthorizationAttempt, state: _AuthorizationState) -> bool:
        return (
            attempt.pending_id == (None if state.pending is None else state.pending.id)
            and attempt.permission_id == state.permission_id
            and attempt.replay == state.replay
            and (state.permission_id is not None or attempt.consumed == state.consumed)
        )

    async def _state(self, request: AuthorizationRequest) -> _AuthorizationState:
        exact = (
            tables.member_authorization.c.workspace_id == request.workspace_id,
            tables.member_authorization.c.member_id == request.member_id,
            tables.member_authorization.c.agent_id == request.agent_id,
            tables.member_authorization.c.call == request.effect.call,
            tables.member_authorization.c.effect_digest == self._digest(request.effect),
        )
        async with workspace_tx(snapshot=True) as connection:
            email = (
                await connection.execute(
                    sa.select(tables.member.c.email).where(
                        tables.member.c.workspace_id == request.workspace_id,
                        tables.member.c.id == request.member_id,
                    )
                )
            ).scalar_one()
            replayed = (
                await connection.execute(
                    sa.select(
                        tables.member_authorization.c.member_id,
                        tables.member_authorization.c.agent_id,
                        tables.member_authorization.c.conversation_id,
                        tables.member_authorization.c.call,
                        tables.member_authorization.c.effect_digest,
                        tables.member_authorization.c.binding_digest,
                        tables.member_authorization.c.decision,
                        tables.member_authorization.c.request_key,
                        tables.member_authorization.c.decision_key,
                        tables.member_authorization.c.requested_by,
                        tables.member_authorization.c.decided_by,
                    )
                    .where(
                        tables.member_authorization.c.workspace_id == request.workspace_id,
                        sa.or_(
                            tables.member_authorization.c.request_key == request.dispatch_key,
                            tables.member_authorization.c.decision_key == request.dispatch_key,
                        ),
                    )
                    .limit(2)
                )
            ).all()
            pending = (
                await connection.execute(
                    sa.select(
                        tables.member_authorization.c.id,
                        tables.member_authorization.c.requested_by,
                        tables.member_authorization.c.call,
                        tables.member_authorization.c.effect_digest,
                        tables.member_authorization.c.effect,
                        tables.member_authorization.c.binding_digest,
                        tables.member_authorization.c.request_summary,
                        tables.member_authorization.c.scope_summary,
                    )
                    .where(
                        tables.member_authorization.c.workspace_id == request.workspace_id,
                        tables.member_authorization.c.member_id == request.member_id,
                        tables.member_authorization.c.agent_id == request.agent_id,
                        tables.member_authorization.c.conversation_id == request.conversation_id,
                        tables.member_authorization.c.decision.is_(None),
                    )
                    .order_by(tables.member_authorization.c.created_at.desc())
                    .limit(1)
                )
            ).one_or_none()
            permission_id = None
            if request.scope is not None:
                scope_digest = request.scope.digest(self.digest_key)
                permission_id = (
                    await connection.execute(
                        sa.select(tables.member_permission.c.id)
                        .where(
                            tables.member_permission.c.workspace_id == request.workspace_id,
                            tables.member_permission.c.member_id == request.member_id,
                            tables.member_permission.c.agent_id == request.agent_id,
                            tables.member_permission.c.call == request.effect.call,
                            tables.member_permission.c.scope_digest == scope_digest,
                            tables.member_permission.c.revoked_at.is_(None),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
            consumed = (
                await connection.execute(
                    sa.select(tables.member_authorization.c.id)
                    .where(
                        *exact,
                        sa.or_(
                            tables.member_authorization.c.requested_by == request.message_ref,
                            tables.member_authorization.c.decided_by == request.message_ref,
                        ),
                        tables.member_authorization.c.decision.is_not(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        replay: Literal["allow", "deny", "ask"] | None = None
        replay_requesting_message_ref: UUID | None = None
        if replayed:
            [recorded] = replayed[:1]
            request_match = (
                recorded.member_id == request.member_id
                and recorded.agent_id == request.agent_id
                and recorded.conversation_id == request.conversation_id
                and recorded.call == request.effect.call
                and recorded.effect_digest == self._digest(request.effect)
                and recorded.binding_digest == self._binding_digest(request)
            )
            if len(replayed) != 1 or not request_match:
                replay = "deny"
            elif recorded.decision_key == request.dispatch_key:
                replay_requesting_message_ref = recorded.requested_by
                replay = (
                    "deny"
                    if recorded.decided_by != request.message_ref
                    or recorded.decision
                    in (
                        "deny",
                        "revoke",
                        SUPERSEDED_AUTHORIZATION_DECISION,
                    )
                    else "allow"
                )
            elif recorded.request_key == request.dispatch_key:
                replay_requesting_message_ref = recorded.requested_by
                replay = (
                    "ask"
                    if recorded.requested_by == request.message_ref and recorded.decision is None
                    else "deny"
                )
        pending_state = None
        if pending is not None:
            pending_effect = AuthorizationEffect.model_validate(pending.effect).model_dump(
                mode="json"
            )
            pending_state = _PendingAuthorization(
                pending.id,
                pending.requested_by,
                pending.call,
                pending.effect_digest,
                pending_effect,
                pending.binding_digest,
                pending.request_summary,
                pending.scope_summary,
            )
        return _AuthorizationState(
            email,
            pending_state,
            permission_id,
            replay,
            replay_requesting_message_ref,
            consumed is not None,
        )

    def _pending_matches(self, request: AuthorizationRequest, state: _AuthorizationState) -> bool:
        return bool(
            state.pending is not None
            and state.pending.call == request.effect.call
            and state.pending.effect_digest == self._digest(request.effect)
            and state.pending.binding_digest == self._binding_digest(request)
        )

    def _effect_json(self, effect: AuthorizationEffect) -> tuple[str, bool] | None:
        """The disclosed effect as the classifier reads it, clipped to
        `MEMBER_AUTHORIZATION_EFFECT_CHARS` with the flag that says whether it saw all of it — the
        prompt answers `ask` to an incomplete request. None where a value cannot be disclosed."""
        disclosed, complete = effect.disclosure()
        if not complete:
            return None
        text = json.dumps(disclosed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(text) <= MEMBER_AUTHORIZATION_EFFECT_CHARS:
            return text, True
        return text[:MEMBER_AUTHORIZATION_EFFECT_CHARS], False

    def _digest(self, effect: AuthorizationEffect) -> str:
        return effect.digest(self.digest_key)

    def _binding_digest(self, request: AuthorizationRequest) -> str | None:
        return None if request.binding is None else request.binding.digest(self.digest_key)

    async def _candidate(
        self,
        request: AuthorizationRequest,
        state: _AuthorizationState,
        effect: tuple[str, bool],
    ) -> AuthorizationVerdict | None:
        if (
            state.replay is not None
            or (state.consumed and state.permission_id is None)
            or not request.message_complete
            or len(request.message) > MEMBER_AUTHORIZATION_MESSAGE_CHARS
            or request.answer is not None
            or (
                state.pending is not None
                and (
                    not self._pending_matches(request, state)
                    or state.pending.requested_by == request.message_ref
                )
            )
            or (not request.selected_from_multiple and state.pending is None)
        ):
            return None
        return await self._classify(request, state, effect)

    async def _classify(
        self,
        request: AuthorizationRequest,
        state: _AuthorizationState,
        effect: tuple[str, bool],
    ) -> AuthorizationVerdict:
        effect_json, effect_complete = effect
        packet = redact_value(
            {
                "member": state.email,
                "agent": request.agent_name,
                "context": request.context.model_dump(mode="json"),
                "pending_request": False,
                "standing_authorization": state.permission_id is not None,
                "reusable_scope": (
                    None if request.scope is None else request.scope.model_dump(mode="json")
                ),
                "request": effect_json,
                "request_complete": effect_complete,
            }
        )
        assert isinstance(packet, dict)
        payload = json.dumps(
            packet,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            async with asyncio.timeout(MEMBER_AUTHORIZATION_TIMEOUT_SECONDS):
                reply = await self.model.turn(
                    ModelRequest(
                        model=self.model.model,
                        system=MEMBER_AUTHORIZATION_SYSTEM,
                        messages=(Message(role="user", content=payload),),
                        max_tokens=MEMBER_AUTHORIZATION_MAX_TOKENS,
                        conversation_cache_ttl="5m",
                        tools=(
                            ToolSchema(
                                name=MEMBER_AUTHORIZATION_TOOL,
                                description="Record the member's authorization decision.",
                                input_schema=AuthorizationVerdict.model_json_schema(),
                            ),
                        ),
                        tool_choice=MEMBER_AUTHORIZATION_TOOL,
                        reasoning=MEMBER_AUTHORIZATION_REASONING,
                    )
                )
            blocks = () if isinstance(reply.content, str) else reply.content
            recorded = tuple(block for block in blocks if isinstance(block, ToolUseBlock))
            if len(recorded) != 1 or recorded[0].name != MEMBER_AUTHORIZATION_TOOL:
                raise ValueError(
                    f"authorization recorded {len(recorded)} {MEMBER_AUTHORIZATION_TOOL} calls"
                )
            verdict = AuthorizationVerdict.model_validate(recorded[0].input)
            if (request.scope is None) != (verdict.scope_summary is None):
                raise ValueError("authorization scope summary does not match the request")
            return verdict
        except Exception as error:
            emit_metric("member_authorization_failed_total", error_class=type(error).__name__)
            log("member.authorization_failed", error_class=type(error).__name__)
            request_summary, scope_summary = self._fallback_summaries(request)
            return AuthorizationVerdict(
                decision="ask",
                basis="none",
                evidence="",
                selected_message_ref=request.message_ref,
                request_summary=request_summary,
                scope_summary=scope_summary,
            )

    def _grounded(
        self,
        verdict: AuthorizationVerdict,
        request: AuthorizationRequest,
        state: _AuthorizationState,
    ) -> bool:
        if verdict.selected_message_ref != request.message_ref:
            return False
        proposal = request.context.assistant_proposal
        if verdict.supporting_message_ref is not None and (
            proposal is None or verdict.supporting_message_ref != proposal.ref
        ):
            return False
        current_evidence = bool(verdict.evidence) and verdict.evidence in request.message
        match verdict.decision, verdict.basis:
            case "ask", "none":
                return verdict.evidence == ""
            case "allow", "standing":
                return state.permission_id is not None and verdict.evidence == ""
            case "allow", "selected_message":
                return current_evidence
            case "always", "selected_message":
                return request.scope is not None and current_evidence
            case "deny", "selected_message":
                return current_evidence
            case "revoke", "selected_message":
                return state.permission_id is not None and current_evidence
            case _:
                return False

    def _fallback_summaries(self, request: AuthorizationRequest) -> tuple[str, str | None]:
        if request.scope is None:
            return f"Run {request.effect.call}.", None
        return (
            f"Run {request.scope.operation} using {request.scope.account_id}.",
            f"Use {request.scope.account_id} for future {request.scope.operation} requests.",
        )

    async def _ensure_pending(
        self,
        request: AuthorizationRequest,
        pending_id: UUID | None,
        verdict: AuthorizationVerdict,
    ) -> None:
        if pending_id is not None:
            return
        await self._insert(request, verdict)

    async def _supersede_pending(self, request: AuthorizationRequest) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member_authorization)
                .values(
                    decision=SUPERSEDED_AUTHORIZATION_DECISION,
                    basis="selected_message",
                    evidence=request.message[:MEMBER_AUTHORIZATION_EVIDENCE_CHARS],
                    decision_key=tables.member_authorization.c.request_key,
                    decided_by=request.message_ref,
                    updated_at=now,
                )
                .where(
                    tables.member_authorization.c.workspace_id == request.workspace_id,
                    tables.member_authorization.c.member_id == request.member_id,
                    tables.member_authorization.c.agent_id == request.agent_id,
                    tables.member_authorization.c.conversation_id == request.conversation_id,
                    tables.member_authorization.c.decision.is_(None),
                )
            )

    async def _settle(
        self,
        request: AuthorizationRequest,
        state: _AuthorizationState,
        verdict: AuthorizationVerdict,
    ) -> bool:
        assert verdict.decision != "ask"
        decision = verdict.decision
        now = datetime.now(UTC)
        try:
            async with workspace_tx() as connection:
                member = await connection.execute(
                    sa.update(tables.member)
                    .values(updated_at=tables.member.c.updated_at)
                    .where(
                        tables.member.c.workspace_id == request.workspace_id,
                        tables.member.c.id == request.member_id,
                    )
                )
                if member.rowcount != 1:
                    raise _SettlementConflict
                if verdict.basis != "standing":
                    consumed = (
                        await connection.execute(
                            sa.select(tables.member_authorization.c.id)
                            .where(
                                tables.member_authorization.c.workspace_id == request.workspace_id,
                                tables.member_authorization.c.member_id == request.member_id,
                                tables.member_authorization.c.agent_id == request.agent_id,
                                tables.member_authorization.c.call == request.effect.call,
                                tables.member_authorization.c.effect_digest
                                == self._digest(request.effect),
                                sa.or_(
                                    tables.member_authorization.c.requested_by
                                    == request.message_ref,
                                    tables.member_authorization.c.decided_by == request.message_ref,
                                ),
                                tables.member_authorization.c.decision.is_not(None),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if consumed is not None:
                        raise _SettlementConflict
                if verdict.basis == "standing":
                    if state.permission_id is None:
                        raise _SettlementConflict
                    locked = await connection.execute(
                        sa.update(tables.member_permission)
                        .values(updated_at=tables.member_permission.c.updated_at)
                        .where(
                            tables.member_permission.c.id == state.permission_id,
                            tables.member_permission.c.revoked_at.is_(None),
                        )
                    )
                    if locked.rowcount != 1:
                        raise _SettlementConflict
                if decision == "revoke":
                    if state.permission_id is None:
                        raise _SettlementConflict
                    revoked = await connection.execute(
                        sa.update(tables.member_permission)
                        .values(revoked_at=now, updated_at=now)
                        .where(
                            tables.member_permission.c.id == state.permission_id,
                            tables.member_permission.c.revoked_at.is_(None),
                        )
                    )
                    if revoked.rowcount != 1:
                        raise _SettlementConflict
                if state.pending is not None:
                    settled = await connection.execute(
                        sa.update(tables.member_authorization)
                        .values(
                            decision=decision,
                            basis=verdict.basis,
                            evidence=verdict.evidence,
                            decision_key=request.dispatch_key,
                            decided_by=request.message_ref,
                            updated_at=now,
                        )
                        .where(
                            tables.member_authorization.c.id == state.pending.id,
                            tables.member_authorization.c.workspace_id == request.workspace_id,
                            tables.member_authorization.c.member_id == request.member_id,
                            tables.member_authorization.c.agent_id == request.agent_id,
                            tables.member_authorization.c.conversation_id
                            == request.conversation_id,
                            tables.member_authorization.c.call == request.effect.call,
                            tables.member_authorization.c.effect_digest
                            == self._digest(request.effect),
                            tables.member_authorization.c.binding_digest
                            == self._binding_digest(request),
                            tables.member_authorization.c.decision.is_(None),
                        )
                    )
                    if settled.rowcount != 1:
                        raise _SettlementConflict
                elif not await self._insert_authorization(connection, request, verdict):
                    raise _SettlementConflict
                if decision == "always":
                    await self._store_permission(connection, request, now)
        except _SettlementConflict:
            return False
        return True

    async def _store_permission(
        self, connection: AsyncConnection, request: AuthorizationRequest, now: datetime
    ) -> None:
        scope = request.scope
        assert scope is not None
        effect_digest = self._digest(request.effect)
        scope_digest = scope.digest(self.digest_key)
        identity = (
            tables.member_permission.c.workspace_id == request.workspace_id,
            tables.member_permission.c.member_id == request.member_id,
            tables.member_permission.c.agent_id == request.agent_id,
            tables.member_permission.c.call == request.effect.call,
        )
        existing = (
            await connection.execute(
                sa.select(tables.member_permission.c.id)
                .where(
                    *identity,
                    sa.or_(
                        tables.member_permission.c.scope_digest == scope_digest,
                        tables.member_permission.c.effect_digest == effect_digest,
                    ),
                )
                .order_by((tables.member_permission.c.scope_digest == scope_digest).desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        values = {
            "effect_digest": effect_digest,
            "effect": request.effect.stored(),
            "scope_digest": scope_digest,
            "scope": scope.model_dump(mode="json"),
            "revoked_at": None,
            "updated_at": now,
        }
        if existing is not None:
            await connection.execute(
                sa.update(tables.member_permission)
                .values(granted_by=request.message_ref, **values)
                .where(tables.member_permission.c.id == existing)
            )
            return
        await connection.execute(
            sa.insert(tables.member_permission).values(
                id=uuid7(),
                workspace_id=request.workspace_id,
                member_id=request.member_id,
                agent_id=request.agent_id,
                call=request.effect.call,
                granted_by=request.message_ref,
                created_at=now,
                **values,
            )
        )

    async def _insert(self, request: AuthorizationRequest, verdict: AuthorizationVerdict) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await self._insert_authorization(connection, request, verdict, uuid7(), now)

    async def _insert_authorization(
        self,
        connection: AsyncConnection,
        request: AuthorizationRequest,
        verdict: AuthorizationVerdict | None,
        authorization_id: UUID | None = None,
        now: datetime | None = None,
    ) -> bool:
        recorded_at = now or datetime.now(UTC)
        pending = verdict is not None and verdict.decision == "ask"
        decision = None if verdict is None or pending else verdict.decision
        insert = (
            postgres_insert(tables.member_authorization)
            if connection.dialect.name == "postgresql"
            else sqlite_insert(tables.member_authorization)
        )
        inserted = await connection.execute(
            insert.values(
                id=authorization_id or uuid7(),
                workspace_id=request.workspace_id,
                member_id=request.member_id,
                agent_id=request.agent_id,
                conversation_id=request.conversation_id,
                call=request.effect.call,
                effect_digest=self._digest(request.effect),
                effect=request.effect.stored(),
                scope_digest=(
                    None if request.scope is None else request.scope.digest(self.digest_key)
                ),
                scope=(None if request.scope is None else request.scope.model_dump(mode="json")),
                binding_digest=self._binding_digest(request),
                request_summary=None if verdict is None else verdict.request_summary,
                scope_summary=None if verdict is None else verdict.scope_summary,
                request_key=request.dispatch_key,
                decision_key=request.dispatch_key if decision is not None else None,
                requested_by=request.message_ref,
                decided_by=request.message_ref if decision is not None else None,
                decision=decision,
                basis=None if verdict is None or pending else verdict.basis,
                evidence=None if verdict is None or pending else verdict.evidence,
                created_at=recorded_at,
                updated_at=recorded_at,
            ).on_conflict_do_nothing()
        )
        return inserted.rowcount == 1

    def _question(self, request: AuthorizationRequest, state: _AuthorizationState) -> AskUserInput:
        """What the member reads: the classifier's one-line summary of the effect, the account
        facts the decision binds, and one labeled line per argument and target field read off the
        effect itself — a short value verbatim, a long one by its length — so the sentence a model
        wrote from untrusted text sits beside facts nothing but the bound bytes can move."""
        effect = request.effect.stored() if state.pending is None else state.pending.effect
        call = request.effect.call if state.pending is None else state.pending.call
        fallback_request, fallback_scope = self._fallback_summaries(request)
        request_summary = (
            fallback_request
            if state.pending is None or state.pending.request_summary is None
            else state.pending.request_summary
        )
        scope_summary = (
            fallback_scope
            if state.pending is None or state.pending.scope_summary is None
            else state.pending.scope_summary
        )
        scope = request.scope
        if scope is None:
            header = "Account request"
            lines = [f"Operation: {call}"]
        else:
            provider = scope.provider.replace("_", " ").title()
            header = f"{provider} account"
            lines = [
                f"Account: {scope.account_id}",
                f"Operation: {scope.operation}",
                f"Access: {scope.access}",
            ]
        arguments = effect["arguments"]
        target = effect["target"]
        assert isinstance(arguments, dict)
        fields = _fact_lines(arguments)
        if isinstance(target, dict):
            fields.extend(_fact_lines(target, "Target "))
        used = 0
        for index, field in enumerate(fields):
            if used + len(field) + 1 > MEMBER_AUTHORIZATION_FACT_CHARS:
                lines.append(f"And {len(fields) - index} more fields.")
                break
            lines.append(field)
            used += len(field) + 1
        facts = "\n".join(lines)
        options = [
            QuestionOption(
                label="Allow once",
                description="Allow this request once.",
                authorization_choice="allow",
            ),
            QuestionOption(
                label="Deny",
                description="Deny this occurrence.",
                authorization_choice="deny",
            ),
        ]
        if scope is not None:
            assert scope_summary is not None
            options.append(
                QuestionOption(
                    label="Always allow",
                    description=(
                        f"{scope_summary} Reuse approval only for the account, operation, "
                        "and access above."
                    ),
                    authorization_choice="always",
                )
            )
        return AskUserInput(
            title="Account approval",
            target_member_id=request.member_id,
            authorization_id=None if state.pending is None else state.pending.id,
            questions=(
                AskQuestion(
                    header=header,
                    question=f"{request_summary}\n\n{facts}",
                    options=tuple(options),
                ),
            ),
        )
