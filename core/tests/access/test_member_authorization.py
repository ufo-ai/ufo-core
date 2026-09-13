import asyncio
import json
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.models.interface import Message, ModelRequest, ToolUseBlock
from ufo.harness.o11y import REDACTED
from ufo.runtime.access.member_authorization import (
    MEMBER_AUTHORIZATION_EFFECT_CHARS,
    MEMBER_AUTHORIZATION_MESSAGE_CHARS,
    MEMBER_AUTHORIZATION_MODEL,
    MEMBER_AUTHORIZATION_TOOL,
    SUPERSEDED_AUTHORIZATION_DECISION,
    AuthorizationEffect,
    AuthorizationRequest,
    AuthorizationVerdict,
    MemberAuthorization,
)
from ufo.schema import tables


@dataclass
class StubModel:
    verdicts: list[AuthorizationVerdict]
    requests: list[ModelRequest] = field(default_factory=list)
    model: str = MEMBER_AUTHORIZATION_MODEL

    async def turn(self, request: ModelRequest) -> Message:
        self.requests.append(request)
        verdict = self.verdicts.pop(0)
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
        verdict = AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Send")
        block = ToolUseBlock(
            id="decision",
            name=MEMBER_AUTHORIZATION_TOOL,
            input=verdict.model_dump(mode="json"),
        )
        return Message(role="assistant", content=(block, block.model_copy(update={"id": "again"})))


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
) -> AuthorizationRequest:
    workspace_id, member_id, agent_id, conversation_id = ids
    return AuthorizationRequest(
        workspace_id=workspace_id,
        member_id=member_id,
        agent_id=agent_id,
        agent_name="Operator",
        dispatch_key=dispatch_key or uuid4().hex,
        conversation_id=conversation_id,
        message_ref=message_ref or uuid4(),
        message=message,
        effect=effect or AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"}),
        selected_from_multiple=selected_from_multiple,
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

    result = await MemberAuthorization(model).authorize(
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
        [
            AuthorizationVerdict(
                decision="allow", basis="selected_message", evidence="Send that email"
            )
        ]
    )
    gate = MemberAuthorization(model)

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
    model = StubModel([AuthorizationVerdict(decision="ask", basis="none", evidence="")])
    ids = await _seed()

    result = await MemberAuthorization(model).authorize(_request(ids, "Looks good"))

    assert result.decision == "ask"
    assert result.question is not None
    assert result.question.title == "Approval required from alice@example.com"
    assert result.question.target_member_id == ids[1]
    [question] = result.question.questions
    assert question.question == (
        "Allow Operator to run send_email as alice@example.com with "
        '{"arguments":{"to":"bob@x.test"},"target":null}?'
    )
    assert [option.label for option in question.options] == ["Allow", "Deny", "Always Allow"]
    [stored] = await _authorizations()
    assert stored["decision"] is None


async def test_the_selected_member_can_answer_the_pending_question_once(db: None) -> None:
    ids = await _seed()
    first_ref, answer_ref = uuid4(), uuid4()
    model = StubModel(
        [
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
            AuthorizationVerdict(decision="allow", basis="pending_answer", evidence="Allow"),
        ]
    )
    gate = MemberAuthorization(model)

    await gate.authorize(_request(ids, "Do it", message_ref=first_ref))
    allowed = await gate.authorize(_request(ids, "Allow", message_ref=answer_ref))
    repeated = await gate.authorize(_request(ids, "Allow", message_ref=answer_ref))

    assert allowed.decision == "allow"
    assert repeated.decision == "deny"
    [stored] = await _authorizations()
    assert stored["requested_by"] == first_ref
    assert stored["decided_by"] == answer_ref
    assert stored["decision"] == "allow"
    assert stored["basis"] == "pending_answer"
    assert stored["evidence"] == "Allow"


async def test_a_dispatch_replay_returns_its_recorded_result_without_another_model_call(
    db: None,
) -> None:
    ids = await _seed()
    first_ref, answer_ref = uuid4(), uuid4()
    request_key, answer_key = uuid4().hex, uuid4().hex
    model = StubModel(
        [
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
            AuthorizationVerdict(decision="allow", basis="pending_answer", evidence="Allow"),
        ]
    )
    gate = MemberAuthorization(model)

    asked = await gate.authorize(
        _request(ids, "Do it", message_ref=first_ref, dispatch_key=request_key)
    )
    asked_again = await gate.authorize(
        _request(ids, "Do it", message_ref=first_ref, dispatch_key=request_key)
    )
    allowed = await gate.authorize(
        _request(ids, "Allow", message_ref=answer_ref, dispatch_key=answer_key)
    )
    allowed_again = await gate.authorize(
        _request(ids, "Allow", message_ref=answer_ref, dispatch_key=answer_key)
    )

    assert [asked.decision, asked_again.decision, allowed.decision, allowed_again.decision] == [
        "ask",
        "ask",
        "allow",
        "allow",
    ]
    assert len(model.requests) == 2
    [stored] = await _authorizations()
    assert stored["request_key"] == request_key
    assert stored["decision_key"] == answer_key


async def test_a_dispatch_key_cannot_be_rebound_to_another_effect(db: None) -> None:
    ids = await _seed()
    message_ref = uuid4()
    model = StubModel(
        [AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Send it")]
    )
    gate = MemberAuthorization(model)

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
    model = StubModel(
        [AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Send it")]
    )
    gate = MemberAuthorization(model)

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
            AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Send it"),
            AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Send it"),
        ]
    )
    gate = MemberAuthorization(model)
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


async def test_same_effect_standing_dispatches_each_record_and_replay(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="always", basis="selected_message", evidence="Always allow this"
            ),
            AuthorizationVerdict(decision="allow", basis="standing", evidence=""),
            AuthorizationVerdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model)
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
        effect=effect,
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
    model = StubModel([AuthorizationVerdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model)

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
    model = StubModel([AuthorizationVerdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model)
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
    model = StubModel([AuthorizationVerdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model)
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
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model)

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
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
            AuthorizationVerdict(
                decision="allow", basis="selected_message", evidence="Send second"
            ),
        ]
    )
    gate = MemberAuthorization(model)
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
            AuthorizationVerdict(
                decision="allow", basis="selected_message", evidence="Send second"
            ),
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model)
    second = _request(ids, "Send second", dispatch_key="second")
    attempt = await gate.preflight(second)
    first = _request(ids, "Maybe send first", dispatch_key="first")
    await gate.authorize(first)

    result = await gate.authorize(second, attempt)

    assert result.decision == "ask"
    assert result.question is not None
    assert len(model.requests) == 3
    [pending] = await _authorizations()
    assert pending["request_key"] == "first"


async def test_always_allow_is_reused_only_for_the_exact_effect(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="always", basis="selected_message", evidence="Always allow this"
            ),
            AuthorizationVerdict(decision="allow", basis="standing", evidence=""),
            AuthorizationVerdict(decision="ask", basis="none", evidence=""),
        ]
    )
    gate = MemberAuthorization(model)

    granted = await gate.authorize(_request(ids, "Always allow this", effect=effect))
    reused = await gate.authorize(_request(ids, "Send it again", effect=effect))
    different = await gate.authorize(
        _request(
            ids,
            "Send another",
            effect=AuthorizationEffect(call="send_email", arguments={"to": "eve@x.test"}),
        )
    )

    assert granted.decision == "allow"
    assert reused.decision == "allow"
    assert different.decision == "ask"
    second_payload = json.loads(str(model.requests[1].messages[0].content))
    assert second_payload["standing_authorization"] is True
    assert [row["decision"] for row in await _authorizations()] == ["always", "allow", None]
    [permission] = await _permissions()
    assert permission["effect_digest"] == effect.digest
    assert permission["revoked_at"] is None


async def test_a_member_can_revoke_the_exact_standing_authorization(db: None) -> None:
    ids = await _seed()
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="always", basis="selected_message", evidence="Always allow this"
            ),
            AuthorizationVerdict(
                decision="deny",
                basis="selected_message",
                evidence="Do not send this time",
            ),
            AuthorizationVerdict(
                decision="revoke",
                basis="selected_message",
                evidence="Stop allowing this",
            ),
        ]
    )
    gate = MemberAuthorization(model)

    await gate.authorize(_request(ids, "Always allow this"))
    denied = await gate.authorize(_request(ids, "Do not send this time"))

    [permission] = await _permissions()
    assert permission["revoked_at"] is None

    revoked = await gate.authorize(_request(ids, "Stop allowing this"))

    assert denied.decision == "deny"
    assert revoked.decision == "deny"
    rows = await _authorizations()
    assert [row["decision"] for row in rows] == ["always", "deny", "revoke"]
    [permission] = await _permissions()
    assert permission["revoked_at"] is not None


async def test_persisted_permissions_redact_secret_fields_but_keep_exact_identity(db: None) -> None:
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
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="always",
                basis="selected_message",
                evidence="Always publish this report",
            ),
            AuthorizationVerdict(
                decision="always",
                basis="pending_answer",
                evidence="Always Allow",
            ),
        ]
    )
    gate = MemberAuthorization(model)

    asked = await gate.authorize(_request(ids, "Always publish this report", effect=effect))
    allowed = await gate.authorize(_request(ids, "Always Allow", effect=effect))

    assert asked.decision == "ask"
    assert allowed.decision == "allow"
    [permission] = await _permissions()
    assert permission["effect"] == {
        "call": "publish",
        "arguments": {
            "path": "/workspace/report",
            "content": "Quarterly report",
            "prompt": "Publish verbatim",
            "token": REDACTED,
        },
        "target": None,
    }
    assert permission["effect_digest"] == effect.digest


async def test_preflight_preserves_safe_effect_details_for_the_model_and_member(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={"content": "Exact report", "prompt": "Publish verbatim"},
        target={"channel": "finance"},
    )
    model = StubModel([AuthorizationVerdict(decision="ask", basis="none", evidence="")])
    gate = MemberAuthorization(model)
    request = _request(await _seed(), "Maybe publish", effect=effect)

    attempt = await gate.preflight(request)
    result = await gate.authorize(request, attempt)

    payload = json.loads(str(model.requests[0].messages[0].content))
    assert json.loads(payload["request"]) == effect.model_dump(mode="json")
    assert payload["request_complete"] is True
    assert result.question is not None
    question = result.question.questions[0].question
    assert '"content":"Exact report"' in question
    assert '"prompt":"Publish verbatim"' in question
    assert '"channel":"finance"' in question


async def test_a_secret_bearing_effect_requires_member_confirmation(db: None) -> None:
    effect = AuthorizationEffect(
        call="publish",
        arguments={"path": "/report", "token": "secret-value"},
    )
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="allow", basis="selected_message", evidence="Publish the report"
            )
        ]
    )

    result = await MemberAuthorization(model).authorize(
        _request(await _seed(), "Publish the report", effect=effect)
    )

    payload = json.loads(str(model.requests[0].messages[0].content))
    assert payload["request_complete"] is False
    assert "secret-value" not in payload["request"]
    assert REDACTED in payload["request"]
    assert result.decision == "ask"
    assert result.question is not None
    assert "secret-value" not in result.question.questions[0].question
    assert REDACTED in result.question.questions[0].question


async def test_an_overbound_effect_requires_bounded_member_confirmation(db: None) -> None:
    content = "x" * (MEMBER_AUTHORIZATION_EFFECT_CHARS + 1)
    effect = AuthorizationEffect(call="publish", arguments={"content": content})
    model = StubModel(
        [AuthorizationVerdict(decision="allow", basis="selected_message", evidence="Publish")]
    )

    result = await MemberAuthorization(model).authorize(
        _request(await _seed(), "Publish", effect=effect)
    )

    payload = json.loads(str(model.requests[0].messages[0].content))
    assert payload["request_complete"] is False
    assert content not in payload["request"]
    assert len(payload["request"]) < 500
    assert result.decision == "ask"
    assert result.question is not None
    assert content not in result.question.questions[0].question
    assert effect.digest in result.question.questions[0].question


async def test_a_duplicate_forced_decision_fails_closed(db: None) -> None:
    result = await MemberAuthorization(DuplicateToolModel()).authorize(
        _request(await _seed(), "Send")
    )

    assert result.decision == "ask"
    assert result.question is not None


async def test_a_preflight_standing_decision_is_revalidated_before_settlement(db: None) -> None:
    ids = await _seed()
    effect = AuthorizationEffect(call="send_email", arguments={"to": "bob@x.test"})
    model = StubModel(
        [
            AuthorizationVerdict(
                decision="always", basis="selected_message", evidence="Always allow this"
            ),
            AuthorizationVerdict(decision="allow", basis="standing", evidence=""),
        ]
    )
    gate = MemberAuthorization(model)
    await gate.authorize(_request(ids, "Always allow this", effect=effect))
    request = _request(ids, "Send it again", effect=effect)
    attempt = await gate.preflight(request)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member_permission)
            .values(revoked_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member_permission.c.effect_digest == effect.digest)
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
            AuthorizationVerdict(decision="allow", basis="standing", evidence=""),
            AuthorizationVerdict(
                decision="allow", basis="selected_message", evidence="permission granted"
            ),
        ]
    )

    result = await MemberAuthorization(ungrounded).authorize(_request(ids, "Maybe"))
    hallucinated = await MemberAuthorization(ungrounded).authorize(
        _request(ids, "Still maybe", effect=AuthorizationEffect(call="other", arguments={}))
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert hallucinated.decision == "ask"
    assert hallucinated.question is not None


async def test_a_model_failure_asks_instead_of_escaping_the_gate(db: None) -> None:
    result = await MemberAuthorization(StubModel([])).authorize(_request(await _seed(), "Send it"))

    assert result.decision == "ask"
    assert result.question is not None


async def test_an_overlong_member_message_asks_without_sending_a_partial_message(
    db: None,
) -> None:
    model = StubModel([])

    result = await MemberAuthorization(model).authorize(
        _request(await _seed(), "x" * (MEMBER_AUTHORIZATION_MESSAGE_CHARS + 1))
    )

    assert result.decision == "ask"
    assert result.question is not None
    assert model.requests == []


def test_effect_identity_is_canonical_json() -> None:
    left = AuthorizationEffect(call="tool", arguments={"a": 1, "b": {"x": 2}})
    right = AuthorizationEffect(call="tool", arguments={"b": {"x": 2}, "a": 1})

    assert left.digest == right.digest
