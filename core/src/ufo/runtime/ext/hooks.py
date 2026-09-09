"""The turn's reactive hook chain: bound hooks grouped by event, fired and folded in pin order."""

import asyncio
from dataclasses import dataclass, field, replace
from uuid import UUID

from pydantic import BaseModel

from ufo.harness.o11y import log, log_error
from ufo.harness.sandbox.session import Sandbox
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import (
    Deny,
    HookContext,
    HookEvent,
    HookPayload,
    HookSpec,
    InjectContext,
    ModifyInput,
    ModifyOutput,
    PostToolUse,
    PostToolUseFailure,
    PreToolUse,
)
from ufo.runtime.turns.audience import SHARED_AUDIENCE, Audience
from ufo.schema.records import Agent, Turn

HOOK_TIMEOUT_SECONDS = 5.0
TURN_HOOK_EVENTS: tuple[HookEvent, ...] = (
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "user_prompt_submit",
    "stop",
    "pre_compact",
    "post_compact",
)
CONNECTION_RECORDED: HookEvent = "connection_recorded"
GATING_EVENTS: frozenset[HookEvent] = frozenset({"pre_tool_use", "user_prompt_submit"})
ALLOWED_OUTCOMES: dict[HookEvent, tuple[type, ...]] = {
    "pre_tool_use": (Deny, ModifyInput),
    "post_tool_use": (ModifyOutput, InjectContext),
    "post_tool_use_failure": (),
    "user_prompt_submit": (Deny, InjectContext),
    "stop": (),
    "pre_compact": (),
    "post_compact": (),
    "page_change": (),
    "connection_recorded": (),
}


class HookOutcomeNotAllowed(TypeError):
    """A hook returned an outcome its event does not permit (a Deny from post_tool_use, a
    ModifyInput from user_prompt_submit); treated as the hook malfunctioning under the failure
    policy."""


@dataclass(frozen=True)
class BoundHook:
    spec: HookSpec
    ext: ExtensionContext


@dataclass(frozen=True)
class HookResolution:
    """The folded outcome of firing an event's hooks. `denied` is set (short-circuit) the moment a
    hook Denies; otherwise `tool_input`/`output` carry the left-to-right ModifyInput/ModifyOutput
    fold (each hook saw the prior's) and `injected` concatenates every InjectContext in order.
    `failed_closed` names the class of the fault behind a denial a gating hook produced by failing
    rather than by deciding — the two are one refusal to the caller and two different events to an
    operator, so what reports the refusal can tell them apart."""

    denied: str | None = None
    failed_closed: str | None = None
    tool_input: BaseModel | None = None
    output: str | None = None
    injected: str = ""


@dataclass(frozen=True)
class HookChain:
    """The turn's reactive hooks, grouped by event in lockfile pin order. `fire` runs one event's
    hooks and folds their outcomes into a HookResolution the engine applies at the fire point. Only
    turn-lifecycle events live here; the data-plane page_change event is driven by the core
    page-change runner in the jobs role, never the turn chain."""

    hooks: dict[HookEvent, tuple[BoundHook, ...]] = field(default_factory=dict)
    audience: Audience = SHARED_AUDIENCE

    def __post_init__(self) -> None:
        bound = tuple(hook for hooks in self.hooks.values() for hook in hooks)
        if any(hook.ext.audience != self.audience for hook in bound):
            raise ValueError("hook and chain audiences differ")

    async def fire(
        self,
        event: HookEvent,
        payload: HookPayload,
        turn: Turn | None,
        agent: Agent | None,
        speaker_member_id: UUID | None,
        sandbox: Sandbox | None = None,
    ) -> HookResolution:
        """Run every hook bound to `event` in order and fold their outcomes. Any Deny denies and
        short-circuits (later hooks skip); ModifyInput/ModifyOutput fold left-to-right so each hook
        sees the prior's result; InjectContext concatenates in order. Composition trust is the pin
        alone — no hook can admit a tool grants withheld. A gating hook (pre_tool_use,
        user_prompt_submit) that raises or exceeds the timeout fails closed to a Deny (fail loud); a
        best-effort user_prompt_submit hook drops only its injection on a fault. A non-gating hook
        (post_tool_use and every observe event) that raises — or returns an outcome its event does
        not permit — is swallowed with a log, never failing the turn."""
        bound = self.hooks.get(event, ())
        gating = event in GATING_EVENTS
        tool_input = payload.tool_input if isinstance(payload, PreToolUse) else None
        output = payload.output if isinstance(payload, PostToolUse) else None
        injected: list[str] = []
        for hook in bound:
            assert self.audience is not None
            current: HookPayload
            match payload:
                case PreToolUse() | PostToolUse() | PostToolUseFailure() if hook.spec.tools and (
                    payload.call not in hook.spec.tools
                ):
                    continue
                case PreToolUse():
                    assert tool_input is not None
                    current = replace(payload, tool_input=tool_input)
                case PostToolUse():
                    assert output is not None
                    current = replace(payload, output=output)
                case _:
                    current = payload
            context = HookContext(
                ext=hook.ext,
                payload=current,
                turn=turn,
                agent=agent,
                audience=self.audience,
                speaker_member_id=speaker_member_id,
                sandbox=sandbox,
            )
            try:
                async with asyncio.timeout(HOOK_TIMEOUT_SECONDS):
                    outcome = await hook.spec.handler(context)
                if outcome is not None and not isinstance(outcome, ALLOWED_OUTCOMES[event]):
                    raise HookOutcomeNotAllowed(f"{event} hook returned {type(outcome).__name__}")
            except Exception as error:
                if gating and not hook.spec.best_effort:
                    return HookResolution(
                        denied=(
                            f"hook {hook.ext.store.extension!r} failed closed on {event}: "
                            f"{type(error).__name__}"
                        ),
                        failed_closed=type(error).__name__,
                    )
                emit = log_error if hook.spec.best_effort else log
                emit(
                    "hook.swallowed",
                    extension=hook.ext.store.extension,
                    hook_event=event,
                    error_class=type(error).__name__,
                )
                continue
            match outcome:
                case Deny(reason=reason):
                    return HookResolution(denied=reason)
                case ModifyInput(tool_input=new_input):
                    tool_input = new_input
                case ModifyOutput(output=new_output):
                    output = new_output
                case InjectContext(text=text):
                    injected.append(text)
                case None:
                    continue
        return HookResolution(tool_input=tool_input, output=output, injected="\n".join(injected))
