import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

import ufo.runtime.access.member_authorization as member_authorization_module
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.interface import Message, ModelRequest, ToolUseBlock
from ufo.runtime.access.member_authorization import (
    MEMBER_AUTHORIZATION_EFFECT_CHARS,
    MEMBER_AUTHORIZATION_FACT_CHARS,
    MEMBER_AUTHORIZATION_MESSAGE_CHARS,
    MEMBER_AUTHORIZATION_MODEL,
    MEMBER_AUTHORIZATION_TOOL,
    MEMBER_AUTHORIZATION_UNDISCLOSABLE,
    SUPERSEDED_AUTHORIZATION_DECISION,
    AuthorizationAnswer,
    AuthorizationBinding,
    AuthorizationContext,
    AuthorizationContextMessage,
    AuthorizationEffect,
    AuthorizationRequest,
    AuthorizationResolution,
    AuthorizationScope,
    AuthorizationVerdict,
    MemberAuthorization,
)
from ufo.schema import tables
from ufo.schema.records import AskQuestion, AskUserInput, QuestionOption

TEST_DIGEST_KEY = b"member-authorization-test-key"
TEST_CONNECTION_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_GRANT_ID = UUID("00000000-0000-0000-0000-000000000002")
TEST_SCOPE = AuthorizationScope(
    provider="gmail",
    account_id="alice@example.com",
    operation="GMAIL_SEND_EMAIL",
    access="write",
)
TEST_BINDING = AuthorizationBinding(
    connection_id=TEST_CONNECTION_ID,
    grant_id=TEST_GRANT_ID,
    provider=TEST_SCOPE.provider,
    account_id=TEST_SCOPE.account_id,
    operation=TEST_SCOPE.operation,
    access=TEST_SCOPE.access,
)
_DEFAULT_BINDING = object()


def _verdict(
    *,
    decision: str,
    basis: str,
    evidence: str,
    selected_message_ref: UUID | None = None,
    supporting_message_ref: str | None = None,
    request_summary: str = "Run GMAIL_SEND_EMAIL using alice@example.com.",
    scope_summary: str | None = "Use alice@example.com for future GMAIL_SEND_EMAIL requests.",
) -> AuthorizationVerdict:
    return AuthorizationVerdict.model_validate(
        {
            "decision": decision,
            "basis": basis,
            "evidence": evidence,
            "selected_message_ref": selected_message_ref or UUID(int=0),
            "supporting_message_ref": supporting_message_ref,
            "request_summary": request_summary,
            "scope_summary": scope_summary,
        }
    )


@dataclass
class StubModel:
    verdicts: list[AuthorizationVerdict]
    requests: list[ModelRequest] = field(default_factory=list)
    model: str = MEMBER_AUTHORIZATION_MODEL
    bind_selected_ref: bool = True

    async def turn(self, request: ModelRequest) -> Message:
        self.requests.append(request)
        payload = json.loads(str(request.messages[0].content))
        verdict = self.verdicts.pop(0)
        verdict = verdict.model_copy(
            update={
                **(
                    {"selected_message_ref": UUID(payload["context"]["selected"]["ref"])}
                    if self.bind_selected_ref
                    else {}
                ),
                "scope_summary": (
                    verdict.scope_summary if payload["reusable_scope"] is not None else None
                ),
            }
        )
        return Message(
            role="assistant",
            content=(
                ToolUseBlock(
                    id="decision",
                    name=MEMBER_AUTHORIZATION_TOOL,
                    input=verdict.model_dump(mode="json"),
                ),
            ),
        )


@dataclass
class DuplicateToolModel:
    model: str = MEMBER_AUTHORIZATION_MODEL

    async def turn(self, request: ModelRequest) -> Message:
        verdict = _verdict(decision="allow", basis="selected_message", evidence="Send")
        block = ToolUseBlock(
            id="decision",
            name=MEMBER_AUTHORIZATION_TOOL,
            input=verdict.model_dump(mode="json"),
        )
        return Message(role="assistant", content=(block, block.model_copy(update={"id": "again"})))


class SecretArguments(BaseModel):
    value: SecretStr


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
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
                email="alice@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="Operator",
                prompt="p",
                model="gpt-5.6-terra",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                queue_key="channel:C1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


def _request(
    ids: tuple[UUID, UUID, UUID, UUID],
    message: str,
    *,
    dispatch_key: str | None = None,
    message_ref: UUID | None = None,
    selected_from_multiple: bool = True,
    effect: AuthorizationEffect | None = None,
    scope: AuthorizationScope | None = TEST_SCOPE,
    binding: AuthorizationBinding | None | object = _DEFAULT_BINDING,
    context: AuthorizationContext | None = None,
    message_complete: bool = True,
    answer: AuthorizationAnswer | None = None,
) -> AuthorizationRequest:
    workspace_id, member_id, agent_id, conversation_id = ids
    resolved_message_ref = message_ref or uuid4()
    resolved_context = context or AuthorizationContext(
        selected=AuthorizationContextMessage(
            ref=str(resolved_message_ref),
            role="user",
            member_id=member_id,
            text=message,
        )
    )
    resolved_binding = (
        TEST_BINDING.model_copy(
            update={
                "provider": scope.provider,
                "account_id": scope.account_id,
                "operation": scope.operation,
                "access": scope.access,
            }
        )
        if binding is _DEFAULT_BINDING and scope is not None
        else binding
    )
    if resolved_binding is _DEFAULT_BINDING:
        resolved_binding = None
    assert resolved_binding is None or isinstance(resolved_binding, AuthorizationBinding)
    return AuthorizationRequest(
        workspace_id=workspace_id,
        member_id=member_id,
        agent_id=agent_id,
        agent_name="Operator",
        dispatch_key=dispatch_key or uuid4().hex,
        conversation_id=conversation_id,
        message_ref=resolved_message_ref,
        message=message,
        context=resolved_context,
        effect=effect or AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"}),
        scope=scope,
        binding=resolved_binding,
        selected_from_multiple=selected_from_multiple,
        message_complete=message_complete,
        answer=answer,
    )


def _answer(
    result: AuthorizationResolution,
    choice: str,
) -> AuthorizationAnswer:
    question = result.question
    assert question is not None
    assert question.authorization_id is not None
    return AuthorizationAnswer.model_validate(
        {"authorization_id": question.authorization_id, "choice": choice}
    )


async def _authorizations() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (await connection.execute(sa.select(tables.member_authorization))).mappings().all()
        )


async def _permissions() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (await connection.execute(sa.select(tables.member_permission))).mappings().all()
        )


async def test_one_active_member_is_automatic(db: None) -> None:
    model = StubModel([])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "send that", selected_from_multiple=False)
    )

    assert result.decision == "allow"
    assert result.question is None
    assert model.requests == []
    assert await _authorizations() == []


async def test_a_selected_member_can_authorize_one_exact_effect(db: None) -> None:
    ids = await _seed()
    ref = uuid4()
    model = StubModel(
        [_verdict(decision="allow", basis="selected_message", evidence="Send that email")]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    first = await gate.authorize(_request(ids, "Send that email", message_ref=ref))
    repeated = await gate.authorize(_request(ids, "Send that email", message_ref=ref))

    assert first.decision == "allow"
    assert repeated.decision == "deny"
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["decision"] == "allow"
    assert stored["basis"] == "selected_message"
    assert stored["evidence"] == "Send that email"
    assert stored["requested_by"] == ref
    assert stored["decided_by"] == ref


async def test_ambiguity_becomes_an_exact_pending_question(db: None) -> None:
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    ids = await _seed()

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(ids, "Looks good")
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert result.question.title == "Account approval"
    assert result.question.target_member_id == ids[1]
    [question] = result.question.questions
    assert question.question == (
        "Run GMAIL_SEND_EMAIL using alice@example.com.\n\n"
        "Account: alice@example.com\n"
        "Operation: GMAIL_SEND_EMAIL\n"
        "Access: write\n"
        "To: bob@x.test"
    )
    assert question.header == "Gmail account"
    assert [option.label for option in question.options] == [
        "Allow once",
        "Deny",
        "Always allow",
    ]
    assert [option.authorization_choice for option in question.options] == [
        "allow",
        "deny",
        "always",
    ]
    [stored] = await _authorizations()
    assert stored["decision"] is None
    assert result.question.authorization_id == stored["id"]


async def test_pending_question_replays_its_persisted_summaries(db: None) -> None:
    ids = await _seed()
    request = _request(ids, "Looks good", dispatch_key="summary-request")
    gate = MemberAuthorization(
        StubModel(
            [
                _verdict(
                    decision="ask",
                    basis="none",
                    evidence="",
                    request_summary="Send the finance report.",
                    scope_summary="Future finance report sends.",
                )
            ]
        ),
        TEST_DIGEST_KEY,
    )

    first = await gate.authorize(request)
    replayed = await gate.authorize(request)

    assert first.question == replayed.question
    assert first.question is not None
    assert "Send the finance report." in first.question.questions[0].question
    assert "Operation: GMAIL_SEND_EMAIL" in first.question.questions[0].question
    always_description = (first.question.questions[0].options or ())[-1].description
    assert always_description is not None
    assert "Future finance report sends." in always_description
    assert "only for the account, operation, and access above" in always_description
    [stored] = await _authorizations()
    assert stored["request_summary"] == "Send the finance report."
    assert stored["scope_summary"] == "Future finance report sends."


async def test_a_call_without_a_reusable_scope_offers_only_allow_or_deny(db: None) -> None:
    model = StubModel(
        [_verdict(decision="always", basis="selected_message", evidence="Looks good")]
    )
    ids = await _seed()
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    result = await gate.authorize(_request(ids, "Looks good", scope=None))

    assert result.question is not None
    [question] = result.question.questions
    assert [option.authorization_choice for option in question.options or ()] == [
        "allow",
        "deny",
    ]
    refused = await gate.authorize(
        _request(
            ids,
            "Always Allow",
            scope=None,
            answer=_answer(result, "always"),
        )
    )
    assert refused.decision == "deny"
    assert refused.refusal is not None


async def test_the_selected_member_can_answer_the_pending_question_once(db: None) -> None:
    ids = await _seed()
    first_ref, answer_ref = uuid4(), uuid4()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    asked = await gate.authorize(_request(ids, "Do it", message_ref=first_ref))
    answer = _answer(asked, "allow")
    allowed = await gate.authorize(_request(ids, "Allow", message_ref=answer_ref, answer=answer))
    repeated = await gate.authorize(_request(ids, "Allow", message_ref=answer_ref, answer=answer))

    assert allowed.decision == "allow"
    assert allowed.requesting_message_ref == first_ref
    assert repeated.decision == "deny"
    [stored] = await _authorizations()
    assert stored["requested_by"] == first_ref
    assert stored["decided_by"] == answer_ref
    assert stored["decision"] == "allow"
    assert stored["basis"] == "pending_answer"
    assert stored["evidence"] == "allow"


async def test_structured_always_answer_grants_scope_for_changed_payloads(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="ask", basis="none", evidence=""),
            _verdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    asked = await gate.authorize(_request(ids, "Maybe send it"))
    granted = await gate.authorize(_request(ids, "Always allow", answer=_answer(asked, "always")))
    reused = await gate.authorize(
        _request(
            ids,
            "Send the revised report",
            effect=AuthorizationEffect(
                call="send_email",
                arguments={"to": "board@example.com"},
            ),
        )
    )

    assert granted.decision == "allow"
    assert reused.decision == "allow"
    [permission] = await _permissions()
    assert permission["scope"] == TEST_SCOPE.model_dump(mode="json")
    assert permission["granted_by"] is not None
    assert len(model.requests) == 2


async def test_a_structured_deny_cannot_be_reinterpreted_as_allow(db: None) -> None:
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    asked = await gate.authorize(_request(ids, "Maybe send it"))
    answer_request = _request(ids, "Deny", answer=_answer(asked, "deny"))
    attempt = await gate.preflight(answer_request)
    forged = replace(
        attempt,
        verdict=_verdict(
            decision="allow",
            basis="pending_answer",
            evidence="Deny",
        ),
    )

    result = await gate.authorize(answer_request, forged)

    assert result.decision == "deny"
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["decision"] == "deny"
    assert stored["evidence"] == "deny"


async def test_a_wrong_pending_identity_cannot_settle_the_question(db: None) -> None:
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    asked = await gate.authorize(_request(ids, "Maybe send it"))
    wrong = await gate.authorize(
        _request(
            ids,
            "Allow",
            answer=AuthorizationAnswer(authorization_id=uuid4(), choice="allow"),
        )
    )

    assert wrong.decision == "deny"
    assert wrong.refusal is not None
    [pending] = await _authorizations()
    assert pending["decision"] is None

    correct = await gate.authorize(_request(ids, "Allow", answer=_answer(asked, "allow")))

    assert correct.decision == "allow"
    assert len(model.requests) == 1


async def test_concurrent_duplicate_answer_replays_one_settlement(db: None) -> None:
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    asked = await gate.authorize(_request(ids, "Maybe send it"))
    answer_request = _request(
        ids,
        "Allow",
        dispatch_key="answer",
        message_ref=uuid4(),
        answer=_answer(asked, "allow"),
    )

    first, second = await asyncio.gather(
        gate.authorize(answer_request),
        gate.authorize(answer_request),
    )

    assert first.decision == "allow"
    assert second.decision == "allow"
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["decision"] == "allow"
    assert stored["decision_key"] == "answer"


async def test_a_dispatch_replay_returns_its_recorded_result_without_another_model_call(
    db: None,
) -> None:
    ids = await _seed()
    first_ref, answer_ref = uuid4(), uuid4()
    request_key, answer_key = uuid4().hex, uuid4().hex
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    asked = await gate.authorize(
        _request(ids, "Do it", message_ref=first_ref, dispatch_key=request_key)
    )
    asked_again = await gate.authorize(
        _request(ids, "Do it", message_ref=first_ref, dispatch_key=request_key)
    )
    answer = _answer(asked, "allow")
    allowed = await gate.authorize(
        _request(
            ids,
            "Allow",
            message_ref=answer_ref,
            dispatch_key=answer_key,
            answer=answer,
        )
    )
    allowed_again = await gate.authorize(
        _request(
            ids,
            "Allow",
            message_ref=answer_ref,
            dispatch_key=answer_key,
            answer=answer,
        )
    )

    assert [asked.decision, asked_again.decision, allowed.decision, allowed_again.decision] == [
        "ask",
        "ask",
        "allow",
        "allow",
    ]
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["request_key"] == request_key
    assert stored["decision_key"] == answer_key


async def test_a_dispatch_key_cannot_be_rebound_to_another_effect(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel([_verdict(decision="allow", basis="selected_message", evidence="Send it")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    allowed = await gate.authorize(
        _request(ids, "Send it", message_ref=message_ref, dispatch_key="same")
    )
    rebound = await gate.authorize(
        _request(
            ids,
            "Publish it",
            message_ref=message_ref,
            dispatch_key="same",
            effect=AuthorizationEffect(call="publish", arguments={"path": "/report"}),
        )
    )

    assert allowed.decision == "allow"
    assert rebound.decision == "deny"
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["call"] == "send_email"


async def test_a_second_dispatch_cannot_reuse_a_one_shot_decision(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel([_verdict(decision="allow", basis="selected_message", evidence="Send it")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    first = await gate.authorize(
        _request(ids, "Send it", message_ref=message_ref, dispatch_key="first")
    )
    second = await gate.authorize(
        _request(ids, "Send it", message_ref=message_ref, dispatch_key="second")
    )

    assert first.decision == "allow"
    assert second.decision == "deny"
    assert len(model.requests) == 1


async def test_parallel_preflights_cannot_reuse_a_one_shot_decision(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel(
        [
            _verdict(decision="allow", basis="selected_message", evidence="Send it"),
            _verdict(decision="allow", basis="selected_message", evidence="Send it"),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    first_request = _request(ids, "Send it", message_ref=message_ref, dispatch_key="first")
    second_request = _request(ids, "Send it", message_ref=message_ref, dispatch_key="second")

    first_attempt, second_attempt = await asyncio.gather(
        gate.preflight(first_request),
        gate.preflight(second_request),
    )
    first = await gate.authorize(first_request, first_attempt)
    second = await gate.authorize(second_request, second_attempt)

    assert first.decision == "allow"
    assert second.decision == "deny"
    assert len(model.requests) == 2
    [stored] = await _authorizations()
    assert stored["request_key"] == "first"


async def test_one_time_allow_does_not_cover_a_changed_payload(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel(
        [
            _verdict(decision="allow", basis="selected_message", evidence="Send"),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    allowed = await gate.authorize(
        _request(
            ids,
            "Send the messages",
            message_ref=message_ref,
            effect=AuthorizationEffect(call="send_email", arguments={"to": "first@x.test"}),
        )
    )
    changed = await gate.authorize(
        _request(
            ids,
            "Send the messages",
            message_ref=message_ref,
            effect=AuthorizationEffect(call="send_email", arguments={"to": "second@x.test"}),
        )
    )

    assert allowed.decision == "allow"
    assert changed.decision == "ask"
    assert len(model.requests) == 2


async def test_dispatch_replay_refuses_a_different_execution_binding(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    request = _request(
        ids,
        "Send it",
        dispatch_key="connector-call",
        message_ref=message_ref,
    )
    gate = MemberAuthorization(
        StubModel([_verdict(decision="allow", basis="selected_message", evidence="Send")]),
        TEST_DIGEST_KEY,
    )
    allowed = await gate.authorize(request)

    rebound = await gate.authorize(
        _request(
            ids,
            "Send it",
            dispatch_key="connector-call",
            message_ref=message_ref,
            binding=TEST_BINDING.model_copy(update={"connection_id": uuid4(), "grant_id": uuid4()}),
        )
    )

    assert allowed.decision == "allow"
    assert rebound.decision == "deny"


async def test_pending_answer_refuses_a_different_execution_binding(db: None) -> None:
    ids = await _seed()
    gate = MemberAuthorization(
        StubModel([_verdict(decision="ask", basis="none", evidence="")]),
        TEST_DIGEST_KEY,
    )
    asked = await gate.authorize(_request(ids, "Maybe send it"))

    answered = await gate.authorize(
        _request(
            ids,
            "Always Allow",
            binding=TEST_BINDING.model_copy(update={"connection_id": uuid4(), "grant_id": uuid4()}),
            answer=_answer(asked, "always"),
        )
    )

    assert answered.decision == "deny"
    assert answered.refusal is not None
    assert await _permissions() == []
    [pending] = await _authorizations()
    assert pending["decision"] is None
    assert pending["binding_digest"] == TEST_BINDING.digest(TEST_DIGEST_KEY)


async def test_linked_assistant_proposal_is_the_only_supporting_message(db: None) -> None:
    ids = await _seed()
    selected_ref = uuid4()
    proposal = AuthorizationContextMessage(
        ref="assistant-proposal",
        role="assistant",
        member_id=None,
        text="I can send the report using the connected account.",
    )
    linked = AuthorizationContext(
        selected=AuthorizationContextMessage(
            ref=str(selected_ref),
            role="user",
            member_id=ids[1],
            reply_to=proposal.ref,
            text="Yes, do that.",
        ),
        direct_reply_parent=proposal,
        assistant_proposal=proposal,
    )
    gate = MemberAuthorization(
        StubModel(
            [
                _verdict(
                    decision="allow",
                    basis="selected_message",
                    evidence="Yes",
                    supporting_message_ref=proposal.ref,
                ),
                _verdict(
                    decision="allow",
                    basis="selected_message",
                    evidence="Yes",
                    supporting_message_ref="unlinked-assistant",
                ),
            ]
        ),
        TEST_DIGEST_KEY,
    )

    allowed = await gate.authorize(
        _request(
            ids,
            "Yes, do that.",
            message_ref=selected_ref,
            context=linked,
        )
    )
    unlinked_ref = uuid4()
    unlinked = await gate.authorize(
        _request(
            ids,
            "Yes, do that.",
            message_ref=unlinked_ref,
            context=AuthorizationContext(
                selected=AuthorizationContextMessage(
                    ref=str(unlinked_ref),
                    role="user",
                    member_id=ids[1],
                    text="Yes, do that.",
                ),
                recent_messages=(proposal,),
            ),
        )
    )

    assert allowed.decision == "allow"
    assert unlinked.decision == "ask"


async def test_verdict_must_cite_the_selected_message(db: None) -> None:
    model = StubModel(
        [
            _verdict(
                decision="allow",
                basis="selected_message",
                evidence="Send",
                selected_message_ref=uuid4(),
            )
        ],
        bind_selected_ref=False,
    )

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Send it")
    )

    assert result.decision == "ask"


async def test_incomplete_selected_message_fails_closed_without_model_use(db: None) -> None:
    model = StubModel([_verdict(decision="allow", basis="selected_message", evidence="Send")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Send", message_complete=False)
    )

    assert result.decision == "ask"
    assert model.requests == []


async def test_concurrent_authorize_consumes_selected_consent_once(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel(
        [
            _verdict(decision="allow", basis="selected_message", evidence="Send it"),
            _verdict(decision="allow", basis="selected_message", evidence="Send it"),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    first, second = await asyncio.gather(
        gate.authorize(_request(ids, "Send it", message_ref=message_ref, dispatch_key="first")),
        gate.authorize(_request(ids, "Send it", message_ref=message_ref, dispatch_key="second")),
    )

    assert {first.decision, second.decision} == {"allow", "deny"}
    [stored] = await _authorizations()
    assert stored["decision"] == "allow"


async def test_same_scope_standing_dispatches_each_record_and_replay(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always allow this"),
            _verdict(decision="allow", basis="standing", evidence=""),
            _verdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Always allow this", effect=effect))
    message_ref = uuid4()
    first_request = _request(
        ids,
        "Send it twice",
        message_ref=message_ref,
        dispatch_key="first",
        effect=effect,
    )
    second_request = _request(
        ids,
        "Send it twice",
        message_ref=message_ref,
        dispatch_key="second",
        effect=AuthorizationEffect(call="send_email", arguments={"to": "eve@x.test"}),
    )

    first_attempt, second_attempt = await asyncio.gather(
        gate.preflight(first_request),
        gate.preflight(second_request),
    )
    first = await gate.authorize(first_request, first_attempt)
    second = await gate.authorize(second_request, second_attempt)
    replayed_first = await gate.authorize(first_request)
    replayed_second = await gate.authorize(second_request)

    assert first.decision == "allow"
    assert second.decision == "allow"
    assert replayed_first.decision == "allow"
    assert replayed_second.decision == "allow"
    assert len(model.requests) == 3
    rows = await _authorizations()
    assert len(rows) == 3
    dispatch_rows = [row for row in rows if row["requested_by"] == message_ref]
    assert {row["request_key"] for row in dispatch_rows} == {"first", "second"}
    assert {row["decision_key"] for row in dispatch_rows} == {"first", "second"}
    assert all(row["decision"] == "allow" for row in dispatch_rows)


async def test_a_pending_request_cannot_treat_its_own_message_as_an_answer(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    first = await gate.authorize(
        _request(ids, "Allow me to send it", message_ref=message_ref, dispatch_key="first")
    )
    duplicate = await gate.authorize(
        _request(ids, "Allow me to send it", message_ref=message_ref, dispatch_key="second")
    )

    assert first.decision == "ask"
    assert duplicate.decision == "ask"
    assert len(model.requests) == 1


async def test_a_new_effect_supersedes_a_pending_request_before_the_single_speaker_fast_path(
    db: None,
) -> None:
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    first_ref, second_ref = uuid4(), uuid4()

    first_request = _request(ids, "Maybe send it", message_ref=first_ref)
    first = await gate.authorize(first_request)
    second = await gate.authorize(
        _request(
            ids,
            "Publish this instead",
            message_ref=second_ref,
            selected_from_multiple=False,
            effect=AuthorizationEffect(call="publish", arguments={"path": "/report"}),
        )
    )

    assert first.decision == "ask"
    assert second.decision == "allow"
    assert second.question is None
    assert len(model.requests) == 1
    [superseded] = await _authorizations()
    assert superseded["call"] == "send_email"
    assert superseded["decision"] == "superseded"
    assert superseded["basis"] == "selected_message"
    assert superseded["evidence"] == "Publish this instead"
    assert superseded["requested_by"] == first_ref
    assert superseded["decided_by"] == second_ref
    assert not await gate.has_pending(ids[0], ids[3], frozenset({ids[1]}))
    assert (await gate.authorize(first_request)).decision == "deny"


async def test_pending_requests_are_scoped_to_their_conversation(db: None) -> None:
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Maybe send it"))
    second_conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=second_conversation_id,
                workspace_id=ids[0],
                agent_id=ids[2],
                surface="slack",
                queue_key="channel:C2",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    second_request = replace(
        _request(ids, "Send it", selected_from_multiple=False),
        conversation_id=second_conversation_id,
    )

    result = await gate.authorize(second_request)

    assert result.decision == "allow"
    assert result.question is None
    assert len(model.requests) == 1
    [pending] = await _authorizations()
    assert pending["conversation_id"] == ids[3]


async def test_concurrent_ambiguity_records_one_pending_request(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="ask", basis="none", evidence=""),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    first, second = await asyncio.gather(
        gate.authorize(_request(ids, "Maybe send it", dispatch_key="first")),
        gate.authorize(
            _request(
                ids,
                "Maybe publish it",
                dispatch_key="second",
                effect=AuthorizationEffect(call="publish", arguments={"path": "/report"}),
            )
        ),
    )

    assert first.decision == "ask"
    assert second.decision == "ask"
    recorded = await _authorizations()
    [pending] = [row for row in recorded if row["decision"] is None]
    assert pending["decision"] is None
    assert all(row["decision"] in (None, SUPERSEDED_AUTHORIZATION_DECISION) for row in recorded)


async def test_a_mismatched_preflight_is_recomputed(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="ask", basis="none", evidence=""),
            _verdict(decision="allow", basis="selected_message", evidence="Send second"),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    first = _request(ids, "Maybe send first")
    second = _request(
        ids,
        "Send second",
        effect=AuthorizationEffect(call="send_email", arguments={"to": "second@x.test"}),
    )

    attempt = await gate.preflight(first)
    result = await gate.authorize(second, attempt)

    assert result.decision == "allow"
    assert len(model.requests) == 2


async def test_a_preflight_is_recomputed_when_pending_state_changes(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="allow", basis="selected_message", evidence="Send second"),
            _verdict(decision="ask", basis="none", evidence=""),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    second = _request(ids, "Send second", dispatch_key="second")
    attempt = await gate.preflight(second)
    first = _request(ids, "Maybe send first", dispatch_key="first")
    await gate.authorize(first)

    result = await gate.authorize(second, attempt)

    assert result.decision == "ask"
    assert result.question is not None
    assert len(model.requests) == 2
    [pending] = await _authorizations()
    assert pending["request_key"] == "first"


async def test_always_allow_reuses_the_same_account_operation_across_payloads(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always allow this"),
            _verdict(decision="allow", basis="standing", evidence=""),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    granted = await gate.authorize(_request(ids, "Always allow this", effect=effect))
    reused = await gate.authorize(
        _request(
            ids,
            "Send another",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "eve@x.test"}),
        )
    )
    different = await gate.authorize(
        _request(
            ids,
            "Read the inbox",
            effect=AuthorizationEffect(call="send_email", arguments={"query": "unread"}),
            scope=TEST_SCOPE.model_copy(
                update={"operation": "GMAIL_LIST_EMAILS", "access": "read"}
            ),
        )
    )

    assert granted.decision == "allow"
    assert reused.decision == "allow"
    assert different.decision == "ask"
    second_payload = json.loads(str(model.requests[1].messages[0].content))
    assert second_payload["standing_authorization"] is True
    assert [row["decision"] for row in await _authorizations()] == ["always", "allow", None]
    [permission] = await _permissions()
    assert permission["effect_digest"] == effect.digest(TEST_DIGEST_KEY)
    assert permission["effect"] == effect.model_dump(mode="json")
    assert permission["scope_digest"] == TEST_SCOPE.digest(TEST_DIGEST_KEY)
    assert permission["scope"] == TEST_SCOPE.model_dump(mode="json")
    assert permission["revoked_at"] is None


async def test_standing_scope_survives_a_fresh_execution_binding(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always"),
            _verdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Always use this account"))

    result = await gate.authorize(
        _request(
            ids,
            "Send another",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "new@x.test"}),
            binding=TEST_BINDING.model_copy(update={"connection_id": uuid4(), "grant_id": uuid4()}),
        )
    )

    assert result.decision == "allow"
    assert json.loads(str(model.requests[1].messages[0].content))["standing_authorization"] is True


async def test_standing_scope_is_isolated_by_member_and_agent(db: None) -> None:
    ids = await _seed()
    workspace_id, member_id, agent_id, _ = ids
    other_member, other_agent, member_conversation, agent_conversation = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_member,
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=other_agent,
                workspace_id=workspace_id,
                name="Other",
                prompt="p",
                model="gpt-5.6-terra",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation),
            [
                {
                    "id": member_conversation,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "surface": "slack",
                    "queue_key": "member-peer",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": agent_conversation,
                    "workspace_id": workspace_id,
                    "agent_id": other_agent,
                    "surface": "slack",
                    "queue_key": "agent-peer",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always"),
            _verdict(decision="ask", basis="none", evidence=""),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Always use this account"))

    other_member_result = await gate.authorize(
        _request((workspace_id, other_member, agent_id, member_conversation), "Use it")
    )
    other_agent_result = await gate.authorize(
        _request((workspace_id, member_id, other_agent, agent_conversation), "Use it")
    )

    assert other_member_result.decision == "ask"
    assert other_agent_result.decision == "ask"
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "scope",
    (
        TEST_SCOPE.model_copy(update={"provider": "outlook"}),
        TEST_SCOPE.model_copy(update={"account_id": "other@example.com"}),
        TEST_SCOPE.model_copy(update={"operation": "GMAIL_DELETE_EMAIL"}),
        TEST_SCOPE.model_copy(update={"access": "read"}),
    ),
)
async def test_always_allow_does_not_cross_account_operation_scope(
    db: None, scope: AuthorizationScope
) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always allow this"),
            _verdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Always allow this"))

    result = await gate.authorize(
        _request(
            ids,
            "Use the account",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "eve@x.test"}),
            scope=scope,
        )
    )

    assert result.decision == "ask"
    second_payload = json.loads(str(model.requests[1].messages[0].content))
    assert second_payload["standing_authorization"] is False
    [permission] = await _permissions()
    assert permission["scope_digest"] == TEST_SCOPE.digest(TEST_DIGEST_KEY)


async def test_a_member_can_revoke_a_standing_account_authorization(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always allow this"),
            _verdict(
                decision="deny",
                basis="selected_message",
                evidence="Do not send this time",
            ),
            _verdict(
                decision="revoke",
                basis="selected_message",
                evidence="Stop allowing this",
            ),
            _verdict(
                decision="always",
                basis="selected_message",
                evidence="Always allow again",
            ),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    await gate.authorize(_request(ids, "Always allow this"))
    denied = await gate.authorize(
        _request(
            ids,
            "Do not send this time",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "eve@x.test"}),
        )
    )

    [permission] = await _permissions()
    assert permission["revoked_at"] is None

    revoked = await gate.authorize(
        _request(
            ids,
            "Stop allowing this",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "carol@x.test"}),
        )
    )

    assert denied.decision == "deny"
    assert revoked.decision == "deny"
    rows = await _authorizations()
    assert [row["decision"] for row in rows] == ["always", "deny", "revoke"]
    [permission] = await _permissions()
    assert permission["revoked_at"] is not None

    regranted = await gate.authorize(
        _request(
            ids,
            "Always allow again",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "new@x.test"}),
        )
    )

    assert regranted.decision == "allow"
    [permission] = await _permissions()
    assert permission["revoked_at"] is None
    assert permission["effect"] == {
        "call": "send_email",
        "arguments": {"to": "new@x.test"},
        "target": None,
    }


async def test_undisclosable_effects_leave_no_durable_secret_verifier(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(
        call="publish",
        arguments={
            "path": "/workspace/report",
            "content": "Quarterly report",
            "prompt": "Publish verbatim",
            "token": "secret-value",
        },
    )
    model = StubModel([])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)

    denied = await gate.authorize(_request(ids, "Always publish this report", effect=effect))

    assert denied.decision == "deny"
    assert denied.question is None
    assert denied.refusal == MEMBER_AUTHORIZATION_UNDISCLOSABLE
    assert model.requests == []
    assert await _authorizations() == []
    assert await _permissions() == []


async def test_an_undisclosable_refusal_is_counted(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    ids = await _seed()
    effect = AuthorizationEffect(call="publish", arguments={"token": "secret-value"})

    denied = await MemberAuthorization(StubModel([]), TEST_DIGEST_KEY).authorize(
        _request(ids, "Publish it", effect=effect)
    )

    assert denied.refusal == MEMBER_AUTHORIZATION_UNDISCLOSABLE
    points = [
        (metric.name, dict(point.attributes), point.value)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    ]
    assert points == [("ufo.member_authorization_refused_total", {"call": "publish"}, 1)]


async def test_undisclosable_effect_cannot_settle_with_a_forged_choice(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(
        call="publish",
        arguments={"connection_token": "secret-value"},
    )
    request = _request(
        ids,
        "Always Allow",
        effect=effect,
        answer=AuthorizationAnswer(authorization_id=uuid4(), choice="always"),
    )

    result = await MemberAuthorization(StubModel([]), TEST_DIGEST_KEY).authorize(request)

    assert result.decision == "deny"
    assert result.refusal == MEMBER_AUTHORIZATION_UNDISCLOSABLE
    assert await _authorizations() == []
    assert await _permissions() == []


async def test_preflight_hands_the_model_the_effect_and_the_member_its_facts(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={"content": "Exact report", "prompt": "Publish verbatim"},
        target={"channel": "finance"},
    )
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    request = _request(await _seed(), "Maybe publish", effect=effect)

    attempt = await gate.preflight(request)
    result = await gate.authorize(request, attempt)

    payload = json.loads(str(model.requests[0].messages[0].content))
    assert json.loads(payload["request"]) == effect.model_dump(mode="json")
    assert payload["context"]["selected"]["text"] == "Maybe publish"
    assert "connection_id" not in json.dumps(payload)
    assert "grant_id" not in json.dumps(payload)
    assert payload["request_complete"] is True
    assert result.question is not None
    question = result.question.questions[0].question
    assert question == (
        "Run GMAIL_SEND_EMAIL using alice@example.com.\n\n"
        "Account: alice@example.com\n"
        "Operation: GMAIL_SEND_EMAIL\n"
        "Access: write\n"
        "Content: Exact report\n"
        "Prompt: Publish verbatim\n"
        "Target channel: finance"
    )
    assert "{" not in question
    assert '"' not in question


async def test_the_question_states_each_argument_as_a_labeled_fact(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={
            "to": "bob@x.test",
            "urgent": True,
            "retries": 0,
            "attachments": [1, 2],
            "options": {"draft": False},
            "body": "x" * 500,
            "note": "line one\nline two",
            "reply_to": None,
        },
    )
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Maybe publish", effect=effect, scope=None, binding=None)
    )

    assert result.question is not None
    [question] = result.question.questions
    assert question.header == "Account request"
    assert question.question.split("\n\n", 1)[1] == (
        "Operation: publish\n"
        "To: bob@x.test\n"
        "Urgent: true\n"
        "Retries: 0\n"
        "Attachments: 1, 2\n"
        "Options draft: false\n"
        "Body: 500 characters\n"
        "Note: 17 characters\n"
        "Reply to: none"
    )


async def test_a_connector_write_shows_its_recipient_and_content_as_facts(db: None) -> None:
    effect = AuthorizationEffect(
        call="call_external_tool",
        arguments={
            "tool_name": "GMAIL_SEND_EMAIL",
            "source_id": "gmail",
            "account_id": "alice@example.com",
            "arguments": {
                "to": "bob@x.test",
                "cc": ["carol@x.test", "dan@x.test"],
                "subject": "Quarterly numbers",
                "body": "x" * 900,
                "attachments": [{"name": "q3.pdf", "bytes": 2048}],
                "headers": {},
            },
            "attribution_bot_user_id": None,
        },
    )
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Maybe send it", effect=effect)
    )

    assert result.question is not None
    assert result.question.questions[0].question.split("\n\n", 1)[1] == (
        "Account: alice@example.com\n"
        "Operation: GMAIL_SEND_EMAIL\n"
        "Access: write\n"
        "Tool name: GMAIL_SEND_EMAIL\n"
        "Source id: gmail\n"
        "Account id: alice@example.com\n"
        "Arguments to: bob@x.test\n"
        "Arguments cc: carol@x.test, dan@x.test\n"
        "Arguments subject: Quarterly numbers\n"
        "Arguments body: 900 characters\n"
        "Arguments attachments 1 name: q3.pdf\n"
        "Arguments attachments 1 bytes: 2048\n"
        "Arguments headers: none\n"
        "Attribution bot user id: none"
    )


async def test_the_question_bounds_its_fact_characters_under_every_surface(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={f"field_{index:02d}": f"{index:02d}" + "v" * 78 for index in range(40)},
    )
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Maybe publish", effect=effect, scope=None, binding=None)
    )

    assert result.question is not None
    question = result.question.questions[0].question
    lines = question.split("\n\n", 1)[1].split("\n")
    shown = lines[1:-1]
    assert lines[0] == "Operation: publish"
    assert shown[0] == "Field 00: 00" + "v" * 78
    assert len("\n".join(shown)) <= MEMBER_AUTHORIZATION_FACT_CHARS
    assert lines[-1] == f"And {40 - len(shown)} more fields."
    assert len(question) < 2_000


async def test_a_forged_key_or_value_cannot_add_a_fact_line(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={
            "to": "attacker@evil.test",
            "x\nTo": "bob@x.test",
            "y\u2028To": "bob@x.test",
            "note": "line one\nTo: bob@x.test",
            "memo": "line one\u2029To: bob@x.test",
            "tags": ["a", "b\nTo: bob@x.test"],
            "marks": ["a", "b\u2028To: bob@x.test"],
        },
    )
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Maybe publish", effect=effect, scope=None, binding=None)
    )

    assert result.question is not None
    question = result.question.questions[0].question
    assert len(question.splitlines()) == len(question.split("\n"))
    assert question.split("\n\n", 1)[1].split("\n") == [
        "Operation: publish",
        "To: attacker@evil.test",
        '"x\\nTo": bob@x.test',
        '"y\\u2028To": bob@x.test',
        "Note: 23 characters",
        "Memo: 23 characters",
        "Tags: a, 16 characters",
        "Marks: a, 16 characters",
    ]


async def test_model_packet_redacts_context_without_losing_message_identity(db: None) -> None:
    message_ref = uuid4()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(
            await _seed(),
            "Authorization: Bearer private-value",
            message_ref=message_ref,
        )
    )

    packet = json.loads(str(model.requests[0].messages[0].content))
    assert packet["context"]["selected"] == {
        "ref": str(message_ref),
        "role": "user",
        "member_id": packet["context"]["selected"]["member_id"],
        "reply_to": None,
        "text": "Authorization: [redacted]",
    }


async def test_nested_credential_markers_fail_closed_without_disclosure(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={
            "path": "/report",
            "provider": {"unknown_refresh_token_value": "secret-value"},
        },
    )
    model = StubModel([])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Publish the report", effect=effect)
    )

    assert result.decision == "deny"
    assert result.refusal == MEMBER_AUTHORIZATION_UNDISCLOSABLE
    assert model.requests == []
    assert await _authorizations() == []


async def test_a_declared_secret_value_is_never_disclosed(db: None) -> None:
    secret = SecretArguments(value="secret-value").model_dump(mode="json")
    model = StubModel([])
    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(
            await _seed(),
            "Publish the report",
            effect=AuthorizationEffect(call="publish", arguments=secret),
        )
    )

    assert secret == {"value": "**********"}
    assert result.decision == "deny"
    assert result.refusal == MEMBER_AUTHORIZATION_UNDISCLOSABLE
    assert model.requests == []


async def test_a_long_effect_reaches_the_model_clipped_and_the_member_by_its_size(
    db: None,
) -> None:
    content = "x" * 20_000
    effect = AuthorizationEffect(call="publish", arguments={"content": content})
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Publish", effect=effect)
    )

    assert result.decision == "ask"
    assert result.refusal is None
    assert result.question is not None
    question = result.question.questions[0].question
    assert content not in question
    assert question.endswith("Content: 20,000 characters")
    payload = json.loads(str(model.requests[0].messages[0].content))
    assert len(payload["request"]) == MEMBER_AUTHORIZATION_EFFECT_CHARS
    assert payload["request_complete"] is False
    [stored] = await _authorizations()
    assert stored["decision"] is None


async def test_a_duplicate_forced_decision_fails_closed(db: None) -> None:
    result = await MemberAuthorization(DuplicateToolModel(), TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Send")
    )

    assert result.decision == "ask"
    assert result.question is not None


async def test_a_preflight_standing_decision_is_revalidated_before_settlement(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            _verdict(decision="always", basis="selected_message", evidence="Always allow this"),
            _verdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    await gate.authorize(_request(ids, "Always allow this", effect=effect))
    request = _request(ids, "Send it again", effect=effect)
    attempt = await gate.preflight(request)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member_permission)
            .values(revoked_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member_permission.c.scope_digest == TEST_SCOPE.digest(TEST_DIGEST_KEY))
        )

    result = await gate.authorize(request, attempt)

    assert result.decision == "ask"
    assert result.question is not None
    assert len(model.requests) == 2
    assert [row["decision"] for row in await _authorizations()] == ["always", None]


async def test_an_ungrounded_or_failed_model_decision_asks(db: None) -> None:
    ids = await _seed()
    ungrounded = StubModel(
        [
            _verdict(decision="allow", basis="standing", evidence=""),
            _verdict(decision="allow", basis="selected_message", evidence="permission granted"),
        ]
    )

    result = await MemberAuthorization(ungrounded, TEST_DIGEST_KEY).authorize(
        _request(ids, "Maybe")
    )
    hallucinated = await MemberAuthorization(ungrounded, TEST_DIGEST_KEY).authorize(
        _request(ids, "Still maybe", effect=AuthorizationEffect(call="other", arguments={}))
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert hallucinated.decision == "ask"
    assert hallucinated.question is not None


async def test_a_model_failure_asks_instead_of_escaping_the_gate(db: None) -> None:
    result = await MemberAuthorization(StubModel([]), TEST_DIGEST_KEY).authorize(
        _request(await _seed(), "Send it")
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert "Run GMAIL_SEND_EMAIL using alice@example.com." in result.question.questions[0].question
    [stored] = await _authorizations()
    assert stored["request_summary"] == "Run GMAIL_SEND_EMAIL using alice@example.com."
    assert stored["scope_summary"] == (
        "Use alice@example.com for future GMAIL_SEND_EMAIL requests."
    )


async def test_an_overlong_member_message_asks_without_sending_a_partial_message(
    db: None,
) -> None:
    model = StubModel([])

    result = await MemberAuthorization(model, TEST_DIGEST_KEY).authorize(
        _request(
            await _seed(),
            "x" * MEMBER_AUTHORIZATION_MESSAGE_CHARS,
            message_complete=False,
        )
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert model.requests == []


def test_effect_identity_is_keyed_canonical_json() -> None:
    left = AuthorizationEffect(call="tool", arguments={"a": 1, "b": {"x": 2}})
    right = AuthorizationEffect(call="tool", arguments={"b": {"x": 2}, "a": 1})

    assert left.digest(TEST_DIGEST_KEY) == right.digest(TEST_DIGEST_KEY)
    assert left.digest(TEST_DIGEST_KEY) != left.digest(b"another-key")


def test_authorization_summaries_are_single_plain_lines() -> None:
    verdict = _verdict(
        decision="ask",
        basis="none",
        evidence="",
        request_summary="  Send the report.\nNow.  ",
        scope_summary=" Future\t report sends. ",
    )

    assert verdict.request_summary == "Send the report. Now."
    assert verdict.scope_summary == "Future report sends."
    with pytest.raises(ValueError, match="one line of plain text"):
        _verdict(
            decision="ask",
            basis="none",
            evidence="",
            request_summary="<b>Send the report.</b>",
        )
    with pytest.raises(ValueError, match="one line of plain text"):
        _verdict(
            decision="ask",
            basis="none",
            evidence="",
            request_summary="Send\u200b the report.",
        )


def test_authorization_question_metadata_is_runtime_only() -> None:
    schema = AskUserInput.model_json_schema()

    assert "authorization_id" not in schema["properties"]
    assert "authorization_choice" not in schema["$defs"]["QuestionOption"]["properties"]
    with pytest.raises(ValueError, match="ordinary question"):
        AskUserInput(
            title="Confirm",
            questions=(
                AskQuestion(
                    question="Proceed?",
                    options=(QuestionOption(label="Proceed", authorization_choice="allow"),),
                ),
            ),
        )
    AskUserInput(
        title="Confirm",
        target_member_id=uuid4(),
        authorization_id=uuid4(),
        questions=(
            AskQuestion(
                question="Proceed?",
                options=(
                    QuestionOption(label="Allow", authorization_choice="allow"),
                    QuestionOption(label="Deny", authorization_choice="deny"),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="exact choices"):
        AskUserInput(
            title="Confirm",
            target_member_id=uuid4(),
            authorization_id=uuid4(),
            questions=(
                AskQuestion(
                    question="Proceed?",
                    options=(
                        QuestionOption(label="Allow", authorization_choice="allow"),
                        QuestionOption(label="Deny", authorization_choice="deny"),
                        QuestionOption(label="Always", authorization_choice="always"),
                        QuestionOption(label="Something else"),
                    ),
                ),
            ),
        )


@dataclass
class _PausedBeforeStatement:
    connection: AsyncConnection
    before: int
    paused: asyncio.Event
    resume: asyncio.Event
    issued: int = 0

    @property
    def dialect(self) -> sa.Dialect:
        return self.connection.dialect

    async def execute(
        self, statement: sa.Executable, *args: object, **kwargs: object
    ) -> sa.CursorResult:
        if self.issued == self.before and not self.paused.is_set():
            self.paused.set()
            await self.resume.wait()
        self.issued += 1
        return await self.connection.execute(statement, *args, **kwargs)


@pytest.mark.parametrize("gap", range(1, 5))
async def test_a_settlement_landing_inside_the_duplicate_read_replays_allow(
    db: None, database_url: str, monkeypatch: pytest.MonkeyPatch, gap: int
) -> None:
    if database_url.startswith("sqlite"):
        pytest.skip("sqlite runs each transaction alone, so no read can be torn")
    ids = await _seed()
    model = StubModel([_verdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model, TEST_DIGEST_KEY)
    asked = await gate.authorize(_request(ids, "Maybe send it"))
    answer_request = _request(
        ids, "Allow", dispatch_key="answer", message_ref=uuid4(), answer=_answer(asked, "allow")
    )
    attempt = await gate.preflight(answer_request)
    paused, resume = asyncio.Event(), asyncio.Event()
    real_tx = member_authorization_module.workspace_tx

    @asynccontextmanager
    async def paused_tx(**options: bool) -> AsyncIterator[_PausedBeforeStatement]:
        async with real_tx(**options) as connection:
            yield _PausedBeforeStatement(connection, gap, paused, resume)

    monkeypatch.setattr(member_authorization_module, "workspace_tx", paused_tx)
    loser = asyncio.create_task(gate.authorize(answer_request, attempt))
    await paused.wait()
    winner = await gate.authorize(answer_request, attempt)
    resume.set()
    second = await loser

    assert winner.decision == "allow"
    assert second.decision == "allow", f"a settlement before read {gap} denied the duplicate"
    assert len(model.requests) == 1
    [stored] = await _authorizations()
    assert stored["decision"] == "allow"
    assert stored["decision_key"] == "answer"
