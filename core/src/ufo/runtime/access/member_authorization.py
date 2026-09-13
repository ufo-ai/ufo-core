"""Member authorization for an exact agent effect selected from a multi-speaker turn."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
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

MEMBER_AUTHORIZATION_MODEL = "gpt-5.6-luna"
MEMBER_AUTHORIZATION_JOB = "core:member_authorization"
MEMBER_AUTHORIZATION_TOOL = "decide_authorization"
MEMBER_AUTHORIZATION_MAX_TOKENS = 512
MEMBER_AUTHORIZATION_MESSAGE_CHARS = 2_000
MEMBER_AUTHORIZATION_EFFECT_CHARS = 8_000
MEMBER_AUTHORIZATION_QUESTION_CHARS = 1_200
MEMBER_AUTHORIZATION_EVIDENCE_CHARS = 500
MEMBER_AUTHORIZATION_TIMEOUT_SECONDS = 12
MEMBER_AUTHORIZATION_REASONING: ReasoningEffort = "low"
SUPERSEDED_AUTHORIZATION_DECISION = "superseded"
MEMBER_AUTHORIZATION_SECRET_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passcode",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessionid",
        "token",
    }
)
MEMBER_AUTHORIZATION_SYSTEM = (
    (Path(__file__).parent.parent / "prompts" / "member_authorization.md").read_text().strip()
)


def _disclose_value(value: JsonValue) -> tuple[JsonValue, bool]:
    match value:
        case dict():
            disclosed: dict[str, JsonValue] = {}
            complete = True
            for key, item in value.items():
                normalized = key.replace("_", "").replace("-", "").lower()
                if normalized in MEMBER_AUTHORIZATION_SECRET_KEYS:
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

    @property
    def digest(self) -> str:
        """The stable identity of these exact JSON semantics."""
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def stored(self) -> dict[str, JsonValue]:
        """Return the persistable projection with credential values redacted."""
        return self.disclosure()[0]

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


class AuthorizationVerdict(BaseModel):
    """One forced model decision and its grounded current-message evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: AuthorizationDecision
    basis: AuthorizationBasis
    evidence: str = Field(max_length=MEMBER_AUTHORIZATION_EVIDENCE_CHARS)


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
    effect: AuthorizationEffect
    selected_from_multiple: bool


@dataclass(frozen=True)
class AuthorizationResolution:
    """The dispatch gate's admitted, denied, or member-question outcome."""

    decision: Literal["allow", "deny", "ask"]
    question: AskUserInput | None = None


@dataclass(frozen=True)
class AuthorizationAttempt:
    """One side-model verdict bound to the exact request and durable state it classified."""

    request: AuthorizationRequest
    verdict: AuthorizationVerdict | None = None
    request_complete: bool = False
    pending_id: UUID | None = None
    permission_id: UUID | None = None
    replay: Literal["allow", "deny", "ask"] | None = None
    consumed: bool = False


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


@dataclass(frozen=True)
class _AuthorizationState:
    email: str
    pending: _PendingAuthorization | None
    permission_id: UUID | None
    replay: Literal["allow", "deny", "ask"] | None
    consumed: bool


class _SettlementConflict(Exception):
    pass


@dataclass(frozen=True)
class MemberAuthorization:
    """Adjudicate and persist member consent before member authority reaches a tool call."""

    model: AuthorizationModel

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
        state = await self._state(request)
        verdict, request_complete = await self._candidate(request, state)
        return AuthorizationAttempt(
            request=request,
            verdict=verdict,
            request_complete=request_complete,
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
        state = await self._state(request)
        if state.replay is not None:
            return AuthorizationResolution(
                state.replay,
                self._question(request, state) if state.replay == "ask" else None,
            )
        if state.pending is not None and state.pending.requested_by == request.message_ref:
            return AuthorizationResolution("ask", self._question(request, state))
        if state.pending is not None and not self._pending_matches(request, state):
            await self._supersede_pending(request)
            state = await self._state(request)
            if state.replay is not None:
                return AuthorizationResolution(
                    state.replay,
                    self._question(request, state) if state.replay == "ask" else None,
                )
            if state.pending is not None:
                return AuthorizationResolution("ask", self._question(request, state))
        if state.consumed and state.permission_id is None:
            return AuthorizationResolution("deny")
        verdict, request_complete = await self._verdict(request, prepared, state)
        if verdict is None:
            if not request.selected_from_multiple and state.pending is None:
                return AuthorizationResolution("allow")
        if (
            verdict is None
            or not self._grounded(verdict, request, state)
            or (
                not request_complete
                and verdict.decision in ("allow", "always")
                and verdict.basis != "pending_answer"
            )
        ):
            verdict = AuthorizationVerdict(decision="ask", basis="none", evidence="")
        if verdict.decision == "ask":
            await self._ensure_pending(request, None if state.pending is None else state.pending.id)
            return AuthorizationResolution(
                "ask", self._question(request, await self._state(request))
            )
        if not await self._settle(request, state, verdict):
            fresh = await self._state(request)
            await self._ensure_pending(
                request,
                None if fresh.pending is None else fresh.pending.id,
            )
            return AuthorizationResolution(
                "ask", self._question(request, await self._state(request))
            )
        if verdict.decision in ("deny", "revoke"):
            return AuthorizationResolution("deny")
        return AuthorizationResolution("allow")

    async def _verdict(
        self,
        request: AuthorizationRequest,
        prepared: AuthorizationAttempt,
        state: _AuthorizationState,
    ) -> tuple[AuthorizationVerdict | None, bool]:
        if self._attempt_matches(prepared, state):
            return prepared.verdict, prepared.request_complete
        if prepared.verdict is not None and prepared.verdict.basis == "standing":
            return None, False
        return await self._candidate(request, state)

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
            tables.member_authorization.c.effect_digest == request.effect.digest,
        )
        async with workspace_tx() as connection:
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
            permission_id = (
                await connection.execute(
                    sa.select(tables.member_permission.c.id)
                    .where(
                        tables.member_permission.c.workspace_id == request.workspace_id,
                        tables.member_permission.c.member_id == request.member_id,
                        tables.member_permission.c.agent_id == request.agent_id,
                        tables.member_permission.c.call == request.effect.call,
                        tables.member_permission.c.effect_digest == request.effect.digest,
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
        if replayed:
            [recorded] = replayed[:1]
            request_match = (
                recorded.member_id == request.member_id
                and recorded.agent_id == request.agent_id
                and recorded.conversation_id == request.conversation_id
                and recorded.call == request.effect.call
                and recorded.effect_digest == request.effect.digest
            )
            if len(replayed) != 1 or not request_match:
                replay = "deny"
            elif recorded.decision_key == request.dispatch_key:
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
                replay = "ask" if recorded.requested_by == request.message_ref else "deny"
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
            )
        return _AuthorizationState(
            email,
            pending_state,
            permission_id,
            replay,
            consumed is not None,
        )

    def _pending_matches(self, request: AuthorizationRequest, state: _AuthorizationState) -> bool:
        return bool(
            state.pending is not None
            and state.pending.call == request.effect.call
            and state.pending.effect_digest == request.effect.digest
        )

    async def _candidate(
        self, request: AuthorizationRequest, state: _AuthorizationState
    ) -> tuple[AuthorizationVerdict | None, bool]:
        if (
            state.replay is not None
            or (state.consumed and state.permission_id is None)
            or len(request.message) > MEMBER_AUTHORIZATION_MESSAGE_CHARS
            or (
                state.pending is not None
                and (
                    not self._pending_matches(request, state)
                    or state.pending.requested_by == request.message_ref
                )
            )
            or (not request.selected_from_multiple and state.pending is None)
        ):
            return None, False
        disclosed, disclosure_complete = request.effect.disclosure()
        effect_json = json.dumps(
            disclosed,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_complete = (
            disclosure_complete and len(effect_json) <= MEMBER_AUTHORIZATION_EFFECT_CHARS
        )
        if len(effect_json) > MEMBER_AUTHORIZATION_EFFECT_CHARS:
            effect_json = json.dumps(
                {
                    "call": request.effect.call,
                    "details": "Request exceeds the authorization disclosure limit.",
                },
                separators=(",", ":"),
            )
        return await self._classify(request, state, effect_json, request_complete), request_complete

    async def _classify(
        self,
        request: AuthorizationRequest,
        state: _AuthorizationState,
        effect_json: str,
        request_complete: bool,
    ) -> AuthorizationVerdict:
        payload = json.dumps(
            {
                "member": state.email,
                "agent": request.agent_name,
                "selected_message": request.message,
                "pending_request": (
                    self._pending_matches(request, state)
                    and state.pending is not None
                    and state.pending.requested_by != request.message_ref
                ),
                "standing_authorization": state.permission_id is not None,
                "request": effect_json,
                "request_complete": request_complete,
            },
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
            return AuthorizationVerdict.model_validate(recorded[0].input)
        except Exception as error:
            emit_metric("member_authorization_failed_total", error_class=type(error).__name__)
            log("member.authorization_failed", error_class=type(error).__name__)
            return AuthorizationVerdict(decision="ask", basis="none", evidence="")

    def _grounded(
        self,
        verdict: AuthorizationVerdict,
        request: AuthorizationRequest,
        state: _AuthorizationState,
    ) -> bool:
        current_evidence = bool(verdict.evidence) and verdict.evidence in request.message
        match verdict.decision, verdict.basis:
            case "ask", "none":
                return verdict.evidence == ""
            case "allow", "standing":
                return state.permission_id is not None and verdict.evidence == ""
            case "allow", "pending_answer":
                return (
                    self._pending_matches(request, state)
                    and state.pending is not None
                    and state.pending.requested_by != request.message_ref
                    and current_evidence
                )
            case "allow", "selected_message":
                return current_evidence
            case "always", "pending_answer":
                return (
                    self._pending_matches(request, state)
                    and state.pending is not None
                    and state.pending.requested_by != request.message_ref
                    and current_evidence
                )
            case "always", "selected_message":
                return current_evidence
            case "deny", "pending_answer":
                return (
                    self._pending_matches(request, state)
                    and state.pending is not None
                    and state.pending.requested_by != request.message_ref
                    and current_evidence
                )
            case "deny", "selected_message":
                return current_evidence
            case "revoke", "pending_answer":
                return (
                    self._pending_matches(request, state)
                    and state.pending is not None
                    and state.pending.requested_by != request.message_ref
                    and state.permission_id is not None
                    and current_evidence
                )
            case "revoke", "selected_message":
                return state.permission_id is not None and current_evidence
            case _:
                return False

    async def _ensure_pending(self, request: AuthorizationRequest, pending_id: UUID | None) -> None:
        if pending_id is not None:
            return
        await self._insert(request)

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
                    sa.or_(
                        tables.member_authorization.c.call != request.effect.call,
                        tables.member_authorization.c.effect_digest != request.effect.digest,
                    ),
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
                            tables.member_authorization.c.decision.is_(None),
                        )
                    )
                    if settled.rowcount != 1:
                        raise _SettlementConflict
                elif not await self._insert_authorization(connection, request, verdict):
                    raise _SettlementConflict
                if decision == "always":
                    insert = (
                        postgres_insert(tables.member_permission)
                        if connection.dialect.name == "postgresql"
                        else sqlite_insert(tables.member_permission)
                    )
                    await connection.execute(
                        insert.values(
                            id=uuid7(),
                            workspace_id=request.workspace_id,
                            member_id=request.member_id,
                            agent_id=request.agent_id,
                            call=request.effect.call,
                            effect_digest=request.effect.digest,
                            effect=request.effect.stored(),
                            granted_by=request.message_ref,
                            revoked_at=None,
                            created_at=now,
                            updated_at=now,
                        ).on_conflict_do_update(
                            index_elements=[
                                tables.member_permission.c.workspace_id,
                                tables.member_permission.c.member_id,
                                tables.member_permission.c.agent_id,
                                tables.member_permission.c.call,
                                tables.member_permission.c.effect_digest,
                            ],
                            set_={
                                "effect": request.effect.stored(),
                                "granted_by": request.message_ref,
                                "revoked_at": None,
                                "updated_at": now,
                            },
                        )
                    )
        except _SettlementConflict:
            return False
        return True

    async def _insert(self, request: AuthorizationRequest) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await self._insert_authorization(connection, request, None, uuid7(), now)

    async def _insert_authorization(
        self,
        connection: AsyncConnection,
        request: AuthorizationRequest,
        verdict: AuthorizationVerdict | None,
        authorization_id: UUID | None = None,
        now: datetime | None = None,
    ) -> bool:
        recorded_at = now or datetime.now(UTC)
        decision = None if verdict is None else verdict.decision
        if decision == "ask":
            raise ValueError("an ask is pending, not a settled authorization")
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
                effect_digest=request.effect.digest,
                effect=request.effect.stored(),
                request_key=request.dispatch_key,
                decision_key=request.dispatch_key if decision is not None else None,
                requested_by=request.message_ref,
                decided_by=request.message_ref if decision is not None else None,
                decision=decision,
                basis=None if verdict is None else verdict.basis,
                evidence=None if verdict is None else verdict.evidence,
                created_at=recorded_at,
                updated_at=recorded_at,
            ).on_conflict_do_nothing()
        )
        return inserted.rowcount == 1

    def _question(self, request: AuthorizationRequest, state: _AuthorizationState) -> AskUserInput:
        agent = request.agent_name or "this agent"
        effect = request.effect.stored() if state.pending is None else state.pending.effect
        call = request.effect.call if state.pending is None else state.pending.call
        digest = request.effect.digest if state.pending is None else state.pending.effect_digest
        details = json.dumps(
            {
                "arguments": effect["arguments"],
                "target": effect["target"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(details) > MEMBER_AUTHORIZATION_QUESTION_CHARS:
            details = json.dumps(
                {
                    "details": "Request exceeds the authorization disclosure limit.",
                    "request_digest": digest,
                },
                separators=(",", ":"),
            )
        return AskUserInput(
            title=f"Approval required from {state.email}",
            target_member_id=request.member_id,
            questions=(
                AskQuestion(
                    header="Member access",
                    question=(f"Allow {agent} to run {call} as {state.email} with {details}?"),
                    options=(
                        QuestionOption(label="Allow", description="Allow this request once."),
                        QuestionOption(label="Deny", description="Deny this occurrence."),
                        QuestionOption(
                            label="Always Allow",
                            description=(
                                "Allow this exact request for this agent without asking again."
                            ),
                        ),
                    ),
                ),
            ),
        )
