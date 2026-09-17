import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import make_dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from dbos import DBOS, SetWorkflowID
from pydantic import BaseModel, ConfigDict

import ufo.runtime.engine as engine_module
from core.tests.loop.test_engine import (
    EchoModel,
    RecordingMemberAuthorization,
    _dispatch_context,
    _engine,
    _seat_member,
    _seed_turn,
)
from ufo.config import Config
from ufo.harness.models.interface import ToolUseBlock
from ufo.harness.sandbox.session import SandboxHandle, SandboxSession
from ufo.host.ext.loader import BoundHook, HookChain
from ufo.runtime.access.member_authorization import (
    AuthorizationBinding,
    AuthorizationResolution,
    AuthorizationScope,
)
from ufo.runtime.engine import ActiveMessage, DispatchResult, _BoundToolCall
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import HookContext, HookOutcome, HookSpec, ModifyInput, PreToolUse
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import StandingAuthorization, ToolDef, ToolRegistry
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema.records import MEMBER_ADMISSION

PREFLIGHT_STEP = "TurnEngine._preflight_member_authorizations"
DISPATCH_STEP = "TurnEngine._dispatch_step"


class _WorkerCrash(BaseException):
    pass


class _RecordedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str = ""
    payload: str


@pytest.mark.serial
@pytest.mark.parametrize("flat_state", [False, True])
async def test_authorization_checkpoint_recovers_with_fresh_context(
    db: None,
    dbos_launched: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flat_state: bool,
) -> None:
    class Input(_RecordedInput):
        pass

    turn = await _seed_turn("queued", None, admission_source=MEMBER_ADMISSION)
    first = await _seat_member(turn.workspace_id, "first@example.com")
    second = await _seat_member(turn.workspace_id, "second@example.com")
    second_ref = uuid4()
    scope = AuthorizationScope(
        provider="gmail",
        account_id="alice@example.com",
        operation="GMAIL_SEND_EMAIL",
        access="write",
    )
    binding = AuthorizationBinding(connection_id=uuid4(), grant_id=uuid4(), **scope.model_dump())
    ext = context_for("authorization_probe", frozenset())

    async def shape(ctx: HookContext) -> HookOutcome:
        count = await ctx.ext.store.get("hooks")
        assert count is None or isinstance(count, int)
        await ctx.ext.store.put("hooks", (count or 0) + 1)
        match ctx.payload:
            case PreToolUse(tool_input=Input() as args):
                return ModifyInput(tool_input=args.model_copy(update={"payload": "shaped"}))
        raise AssertionError("unexpected hook input")

    async def authorize(ctx: ToolContext, args: Input) -> StandingAuthorization[Input]:
        return StandingAuthorization(
            context=replace(ctx, connector_read_only=True),
            input=args.model_copy(update={"account": scope.account_id}),
            scope=scope,
            binding=binding,
        )

    async def handler(ctx: ToolContext, args: Input) -> ToolResult:
        assert ctx.ext is not None
        assert isinstance(ctx.sandbox, SandboxSession)
        count = await ctx.ext.store.get("dispatches")
        assert count is None or isinstance(count, int)
        await ctx.ext.store.put("dispatches", (count or 0) + 1)
        await ctx.ext.store.put(
            "result",
            {
                "container": ctx.sandbox.handle.container_id,
                "standing_context": ctx.connector_read_only,
                "input": args.model_dump(mode="json"),
            },
        )
        return ToolResult(content=(TextContent(text="sent"),))

    gate = RecordingMemberAuthorization(AuthorizationResolution("allow"))
    engine = replace(
        _engine(turn, EchoModel(), tmp_path),
        member_authorization=gate,
        tools=ToolRegistry(
            (
                ToolDef(
                    name="member_probe",
                    description="d",
                    input_model=Input,
                    handler=handler,
                    standing_authorization=authorize,
                ),
            )
        ),
        tool_ext={"member_probe": ext},
        hooks=HookChain(
            hooks={
                "pre_tool_use": (
                    BoundHook(
                        spec=HookSpec(event="pre_tool_use", handler=shape, tools=("member_probe",)),
                        ext=ext,
                    ),
                ),
            },
            audience=conversation_audience(None),
        ),
    )
    call = ToolUseBlock(
        id="probe",
        name="member_probe",
        input={"payload": "original", "requested_by": str(second_ref)},
    )
    requesters = {
        turn.id: ActiveMessage(member_id=first, rendered="Review it"),
        second_ref: ActiveMessage(member_id=second, rendered="Send from my account"),
    }
    resolved = await engine._bind_or_error(
        _dispatch_context(engine), engine._resolve_call(call), requesters
    )
    assert isinstance(resolved, _BoundToolCall)
    bound = resolved
    phase = 0
    workflow_id = str(uuid4())

    @DBOS.workflow(name=f"authorization_recovery_{workflow_id}")
    async def workflow() -> DispatchResult:
        with ws(turn.workspace_id):
            if flat_state and phase == 0:
                preflights = await recorded_preflight()
            else:
                preflights = await engine._preflight_member_authorizations((bound,))
            if phase == 0:
                raise _WorkerCrash
            return await engine._dispatch_step_recovering(bound, [], preflights[0])

    try:
        with ws(turn.workspace_id):
            if flat_state:
                prepared = await engine._preflight_member_authorization(bound)
                assert prepared is not None
                assert prepared.attempt is not None
                fields = {
                    "args": _RecordedInput(
                        account=scope.account_id,
                        payload="shaped",
                    ),
                    "request_target": prepared.request_target,
                    "context": {"container": "discarded"},
                    "scope": scope,
                    "binding": binding,
                    "attempt": prepared.attempt,
                    "denied": prepared.denied,
                    "failed_closed": prepared.failed_closed,
                    "authority_error": prepared.authority_error,
                    "authority_error_class": prepared.authority_error_class,
                }
                record = make_dataclass(
                    "_AuthorizationPreflight",
                    [(name, object) for name in fields],
                    frozen=True,
                    module=engine_module.__name__,
                )

                @DBOS.step(name=PREFLIGHT_STEP, preemptible=True)
                async def recorded_preflight() -> tuple[object, ...]:
                    return (record(**fields),)

                with monkeypatch.context() as patch:
                    patch.setattr(engine_module, "_AuthorizationPreflight", record)
                    with SetWorkflowID(workflow_id), pytest.raises(_WorkerCrash):
                        await workflow()
            else:
                with SetWorkflowID(workflow_id), pytest.raises(_WorkerCrash):
                    await workflow()

            steps = await DBOS.list_workflow_steps_async(workflow_id, load_output=False)
            assert [(step["function_id"], step["function_name"]) for step in steps] == [
                (1, PREFLIGHT_STEP)
            ]
            assert await ext.store.get("hooks") == 1
            assert await ext.store.get("dispatches") is None

            phase = 1
            assert isinstance(engine.sandbox, SandboxSession)
            engine = replace(
                engine,
                sandbox=replace(
                    engine.sandbox,
                    handle=SandboxHandle(
                        conversation_id=turn.conversation_id, container_id="recovered"
                    ),
                ),
            )
            resolved = await engine._bind_or_error(
                _dispatch_context(engine), engine._resolve_call(call), requesters
            )
            assert isinstance(resolved, _BoundToolCall)
            bound = resolved
            DBOS._recover_pending_workflows(["local"])
            result = await DBOS.get_result_async(workflow_id)
            assert isinstance(result, DispatchResult)
            with SetWorkflowID(workflow_id):
                assert await workflow() == result

            assert result.text == "sent"
            assert result.is_error is False
            assert await ext.store.get("hooks") == 1
            assert await ext.store.get("dispatches") == 1
            assert await ext.store.get("result") == {
                "container": "recovered",
                "standing_context": True,
                "input": {"account": "alice@example.com", "payload": "shaped"},
            }
            assert len(gate.preflight_requests) == 1
            assert gate.requests == gate.preflight_requests
            steps = await DBOS.list_workflow_steps_async(workflow_id, load_output=False)
            assert [(step["function_id"], step["function_name"]) for step in steps] == [
                (1, PREFLIGHT_STEP),
                (2, DISPATCH_STEP),
            ]
    finally:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
