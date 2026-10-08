"""Typed spawn: one verb that runs a subagent profile or a workspace agent as a child turn.

A profile names a prompt, a tool subset, and an input/output schema; a workspace agent row carries
its own prompt, model, tool allowlist, and declared (or default) I/O contract. `spawn` resolves the
target across both namespaces, validates the payload against the target's input contract, admits a
child turn linked to its parent (`parent_turn_id`) on its own conversation, and enqueues it on
the express queue — uncapped, so a fleet whose turn capacity is claimed whole by waiting parents
still starts their children. Foreground awaits the child's terminal and returns its
contract-validated output;
background returns the child turn id at once and the child delivers its own result through
`SubagentResult` when it finishes. A foreground wait is interruptible: a member message arriving on
the parent's conversation moves the child to the background — it keeps running and delivers its own
result — exactly as a bash command still running at its foreground budget keeps running detached,
so the parent is never held away from its own conversation by work it can read later. An agent child
is a fully async peer: it runs as the target agent — its identity, its sandbox, its whole tool
set — the spawn returns its identity at once whatever the caller asked, and the spawning
conversation is where its messages arrive; a profile child runs under the spawning turn's agent, as
it always has."""

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions, WorkflowHandleAsync
from dbos import error as dbos_error
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.o11y import current_traceparent, log
from ufo.harness.untrusted import wall
from ufo.runtime.access.turn_sessions import DeploySessions
from ufo.runtime.billing.spend import NO_SPEND_GATES, PARK, REJECT, SPAWN_MOMENT, SpendGates
from ufo.runtime.engine import TurnParked
from ufo.runtime.ext.context import TurnInvoker
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.ext.surface import conversation_name
from ufo.runtime.hub import ArrivalQueued, Hub, Parked, Terminal
from ufo.runtime.prompts.render import (
    CONTEXT_WINDOW_SLOT,
    PROMPT_VAR_RE,
    SKILL_INDEX_SLOT,
    SUBAGENT_OUTPUT_DISCIPLINE,
    render_skill_index,
)
from ufo.runtime.seats import member_is_admin
from ufo.runtime.skills.runtime import CORE_SKILLS, LoadedSkill, loaded_context
from ufo.runtime.tools.context import (
    AmbiguousSpawnTarget,
    SpawnModelRejected,
    SpawnPayloadRejected,
    SpawnResult,
    SubagentStatus,
    UnknownSpawnTarget,
    UnknownSubagentProfile,
    UntrustedContentError,
    model_route_guidance,
)
from ufo.runtime.turns.audience import Audience, audience_member
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.turns.contracts import (
    Contract,
    input_contract,
    output_contract,
    payload_keys,
)
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    DELIVERY_DELIVERED,
    DELIVERY_PENDING,
    EXPRESS_QUEUE_NAME,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    PARKED,
    SPAWN_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    TURN_WORKFLOW_NAME,
    ModelAccountCapability,
    TerminalFrame,
    Turn,
    TurnContext,
    TurnRuntimeConfig,
    turn_id_for,
)

SUBAGENT_WORKFLOW_POLL_SECONDS = 1.0
PROFILE_TARGET_KIND = "profile"
AGENT_TARGET_KIND = "agent"
STATUS_QUESTION = "question"
RESULT_OPEN = '<spawn_result target="{target}" spawn_id="{spawn_id}" status="{status}">'
RESULT_CLOSE = "</spawn_result>"
RESULT_CLOSE_ESCAPE = "&lt;/spawn_result&gt;"
RESULT_REPLY_GUIDANCE = (
    (Path(__file__).parent / "prompts" / "background_results.md").read_text().strip()
)
RESULT_UNKNOWN_PROFILE = (
    "The spawn's profile is no longer registered, so its answer could not be checked "
    "against a schema and is withheld."
)
RESULT_INVALID = (
    "The spawn's final answer does not match its output schema, so it was dropped rather than "
    "delivered. Failures: {faults}"
)
AGENT_SPAWN_REFUSAL = (
    "agent {name!r} is not yours to spawn — spawn an agent you own, or have a workspace admin "
    "run it"
)
PRELOAD_PROMPT_CHAR_BOUND = 200_000
FINISH_CONTRACT = (
    "End the turn by calling the `finish` tool with your final answer — its input schema is the "
    "output contract. Do not write a final prose message before it, and do not emit text alongside "
    "it. The spawn returns only the finish payload to the parent; it does not return preceding "
    "messages or tool output."
)

PARENT_HANDOFF_END = "</parent_handoff>"
PARENT_HANDOFF_START_INDEX = SUBAGENT_OUTPUT_DISCIPLINE.index("<parent_handoff>")
PARENT_HANDOFF_END_INDEX = SUBAGENT_OUTPUT_DISCIPLINE.index(PARENT_HANDOFF_END) + len(
    PARENT_HANDOFF_END
)
UNCAPPED_SUBAGENT_OUTPUT_DISCIPLINE = (
    SUBAGENT_OUTPUT_DISCIPLINE[:PARENT_HANDOFF_START_INDEX]
    + SUBAGENT_OUTPUT_DISCIPLINE[PARENT_HANDOFF_END_INDEX:]
).strip()
CORE_SKILL_INDEX = tuple((skill.name, skill.description) for skill in CORE_SKILLS)


class SubagentParked(RuntimeError):
    """An awaited child stopped on a spend limit. The parent stops waiting, the child is cancelled
    rather than left for the dispatcher to re-run into nobody, and the tool that spawned reports
    this to the model."""


class SpendRefused(RuntimeError):
    """A spend gate refused a spawn. The tool that asked answers the model with the gate's words,
    so a fan-out that cannot be paid for stops at the first child rather than starting every one
    of them."""


@dataclass(frozen=True)
class SubagentRegistry:
    """The frozen set of profiles a spawn dispatches against — extensions contribute profiles the
    same way they contribute tools. Rejects a duplicate name at construction, fails loud on an
    unknown lookup."""

    profiles: tuple[SubagentProfile, ...]

    def __post_init__(self) -> None:
        names = [profile.name for profile in self.profiles]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate subagent profiles: {', '.join(duplicates)}")

    def get(self, name: str) -> SubagentProfile:
        profile = self.find(name)
        if profile is None:
            raise UnknownSubagentProfile(
                name, tuple(sorted(profile.name for profile in self.profiles))
            )
        return profile

    def find(self, name: str) -> SubagentProfile | None:
        return next((profile for profile in self.profiles if profile.name == name), None)


@dataclass(frozen=True)
class AgentTarget:
    """A workspace agent resolved as a spawn target: the row facts the spawn needs — identity,
    payload contract, and answer contract."""

    id: UUID
    name: str
    input_schema: dict[str, object] | None
    output_schema: dict[str, object] | None


def subagent_system_prompt(
    profile: SubagentProfile,
    *,
    skills: Sequence[tuple[str, str]] = CORE_SKILL_INDEX,
    preload: tuple[LoadedSkill, ...] = (),
    context_window: str = "",
) -> str:
    """The child's system prompt: the profile's own instructions with its `{{skill_index}}` slot
    filled from the loadable-skill index, then any preloaded skills' instructions, then the shared
    output discipline (the shared delivery register, citation, and formatting rules), then the
    output contract — the child ends its turn by calling the engine's finish tool, whose input
    schema is the profile's output model, and a preloaded skill's own answer-formatting instructions
    can never displace that contract from the prompt's last word. A slot the profile leaves unfilled
    fails loud rather than reaching the model as a literal brace; preloaded bodies over the char
    bound fail loud rather than blowing the model call. Skill bodies are injected after slot
    validation — a literal brace inside a skill is content, never an unfilled slot.
    `context_window` is the active context strategy's own note on the boundary: the child is told
    what its window does under the strategy this deploy runs, and a deploy that hands none tells it
    nothing rather than the words of another strategy."""
    if "load_skill" in profile.tool_names and SKILL_INDEX_SLOT not in profile.prompt:
        raise ValueError(
            f"subagent profile {profile.name!r} grants load_skill but has no "
            f"{SKILL_INDEX_SLOT} slot"
        )
    body = profile.prompt.replace(SKILL_INDEX_SLOT, render_skill_index(skills))
    discipline = (
        SUBAGENT_OUTPUT_DISCIPLINE
        if profile.concise_parent_handoff
        else UNCAPPED_SUBAGENT_OUTPUT_DISCIPLINE
    ).replace(CONTEXT_WINDOW_SLOT, context_window)
    if unresolved := frozenset(PROMPT_VAR_RE.findall(f"{body}\n\n{discipline}")):
        raise ValueError(f"subagent prompt has unresolved slots: {', '.join(sorted(unresolved))}")
    if preload:
        bodies = loaded_context(preload)
        if len(bodies) > PRELOAD_PROMPT_CHAR_BOUND:
            raise ValueError(
                f"preloaded skill bodies are {len(bodies)} chars, "
                f"over the {PRELOAD_PROMPT_CHAR_BOUND} bound"
            )
        body = f"{body}\n\n---\n\nPreloaded skill(s):\n\n{bodies}"
    return f"{body}\n\n{discipline}\n\n{FINISH_CONTRACT}"


@dataclass(frozen=True)
class Subagents:
    """The spawn workflow, bound to the turn that spawns: resolve the target, admit and enqueue a
    child turn, then (foreground) await and validate its output."""

    client: DBOSClient
    registry: SubagentRegistry
    parent: Turn
    audience: Audience
    sessions: DeploySessions | None
    hub: Hub | None = None
    invoker: TurnInvoker | None = None
    spend: SpendGates = NO_SPEND_GATES
    """The model ids this deploy's registry serves, the closed set a spawn's own model pin holds
    to. The registry is fixed at boot, so the ids ride here as data rather than as a handle
    `subagents` would have to import the registry to hold."""
    models: tuple[str, ...] = ()
    model_providers: Mapping[str, str] = field(default_factory=dict)

    async def spawn(
        self,
        target: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
        model: str | None = None,
        *,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SpawnResult:
        """Admit and enqueue a child turn. With `dedup_key`, the child's conversation (and so its
        turn id, the DBOS workflow id) is derived from the parent turn and the key, so a re-run of
        the spawning tool step reconnects: `_admit` is idempotent, DBOS dedups the re-enqueue on the
        existing workflow id, and `_await_terminal` returns a child that already finished at once —
        completed branches are memoized by their own durable terminal, never respawned or rebilled.
        Without a key, each call mints a fresh random child. An agent target always runs
        background and always delivers: it is an independent peer nobody blocks on, and its
        answers reach this conversation as arrivals.

        `detach_on_arrival` gives the foreground wait the shape a bash command's foreground budget
        has: a member message arriving on the parent's conversation ends the wait, the child is
        moved to the background rather than cancelled, and the result says so instead of carrying
        an output — so the parent can answer the message while the work it already paid for runs
        on and delivers itself.

        `model` pins this child onto one model id over the target's own and the deploy's default:
        it becomes the child turn's runtime-config pin, which is what every later setup of that
        turn resolves through, and the model the spend gates weigh the child under. An id the
        registry does not serve is refused here rather than at the child's first round, and so is a
        pin asked for under a turn tree that already carries one — that pin is the member's own
        admitted selection, which the tree keeps."""
        conversation_id = (
            uuid5(NAMESPACE_URL, f"{self.parent.id}/{dedup_key}")
            if dedup_key is not None
            else uuid4()
        )
        turn_id = turn_id_for(self.parent.workspace_id, conversation_id, 1)
        replay_exists, replayed_runtime, replayed_model_accounts = (
            (False, None, ()) if dedup_key is None else await self._existing_runtime_config(turn_id)
        )
        replay = None if dedup_key is None else await self._existing_agent_spawn(turn_id, target)
        resolved = replay[0] if replay is not None else await self._resolve(target)
        runtime_config = replayed_runtime if replay_exists else self._child_runtime_config(model)
        available_model_accounts = (
            replayed_model_accounts if replay_exists else self.parent.model_accounts
        )
        pinned_model = (
            None if runtime_config is None or runtime_config.model is None else runtime_config.model
        )
        match resolved:
            case SubagentProfile():
                routed_models = (
                    (pinned_model,) if pinned_model is not None else resolved.models[:-1]
                )
                routed_providers = {
                    provider
                    for candidate in routed_models
                    if (provider := self.model_providers.get(candidate)) is not None
                }
                model_accounts = tuple(
                    account
                    for account in available_model_accounts
                    if not routed_models or account.provider in routed_providers
                )
                if (
                    routed_models
                    and not model_accounts
                    and not replay_exists
                    and requester_member_id is not None
                ):
                    model_accounts = tuple(
                        ModelAccountCapability(provider=provider, slot=slot)
                        for provider, slot in await ws_current().member_model_accounts(
                            requester_member_id
                        )
                        if provider in routed_providers
                    )
                agent_id = self.parent.agent_id
                profile_name: str | None = resolved.name
                inherits_sandbox = True
                input_model: Contract = resolved.input_model
                output_model: Contract = resolved.output_model
                untrusted = resolved.untrusted_output
                agent_target: AgentTarget | None = None
            case AgentTarget():
                routed_provider = (
                    None if pinned_model is None else self.model_providers.get(pinned_model)
                )
                model_accounts = tuple(
                    account
                    for account in available_model_accounts
                    if routed_provider is None or account.provider == routed_provider
                )
                if (
                    pinned_model is not None
                    and routed_provider is not None
                    and not model_accounts
                    and not replay_exists
                    and requester_member_id is not None
                ):
                    model_accounts = tuple(
                        ModelAccountCapability(provider=provider, slot=slot)
                        for provider, slot in await ws_current().member_model_accounts(
                            requester_member_id
                        )
                        if provider == routed_provider
                    )
                agent_id = resolved.id
                profile_name = None
                inherits_sandbox = False
                input_model = input_contract(resolved.input_schema)
                output_model = output_contract(resolved.output_schema)
                untrusted = True
                agent_target = resolved
                background = True
                delivers_result = True
        request_fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "agent_id": str(agent_id),
                        "profile": profile_name,
                        "payload": payload,
                        "delivers_result": delivers_result,
                        "name": name,
                        "requesting_message_ref": (
                            None if requesting_message_ref is None else str(requesting_message_ref)
                        ),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        inbound = replay[1] if replay is not None else self._validated(target, input_model, payload)
        if await self._admit(
            conversation_id,
            turn_id,
            agent_id=agent_id,
            profile=profile_name,
            inherits_sandbox=inherits_sandbox,
            inbound=inbound,
            request_fingerprint=request_fingerprint,
            delivers_result=delivers_result,
            name=name,
            agent_target=agent_target,
            requester_member_id=requester_member_id,
            requesting_message_ref=requesting_message_ref,
            runtime_config=runtime_config,
            model_accounts=model_accounts,
            model=(
                runtime_config.model
                if runtime_config is not None and runtime_config.model is not None
                else self._profile_model(profile_name, model_accounts)
            ),
        ):
            await self._enqueue(turn_id, conversation_id)
        if background:
            return SpawnResult(turn_id=turn_id, conversation_id=conversation_id, output=None)
        try:
            if detach_on_arrival:
                awaited = await self._await_terminal_or_detach(turn_id)
                if awaited is None:
                    return SpawnResult(
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        output=None,
                        detached_on_arrival=True,
                    )
                terminal = awaited
            else:
                terminal = await self._await_terminal(turn_id)
        except Exception:
            await cancel_one_turn(self.client, self.sessions, turn_id)
            raise
        if terminal.status != "done":
            diagnostic = ": ".join(
                part
                for part in (terminal.error_class, terminal.error_message or terminal.text)
                if part
            )
            raise RuntimeError(
                f"spawn {target!r} turn ended {terminal.status}"
                + (f" ({diagnostic})" if diagnostic else "")
            )
        if terminal.question is not None:
            return SpawnResult(
                turn_id=turn_id,
                conversation_id=conversation_id,
                output=None,
                terminal=terminal,
                untrusted=untrusted,
            )
        try:
            output = output_model.model_validate_json(terminal.text)
        except ValidationError as error:
            if untrusted:
                raise UntrustedContentError(
                    f"spawn {target!r} returned output that failed validation: {error}"
                ) from error
            raise
        return SpawnResult(
            turn_id=turn_id,
            conversation_id=conversation_id,
            output=output,
            terminal=terminal,
            untrusted=untrusted,
        )

    async def result(self, turn_id: UUID) -> SpawnResult:
        """Read the exact terminal and validated output of a finished child this conversation
        spawned. A failed child, a child that ended asking, or invalid output returns no output, so
        a host-side consumer can represent that terminal without trusting the model to relay its
        result."""
        profile = await self._require_child(turn_id)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.terminal,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one()
        if row.terminal is None:
            raise ValueError(f"spawn {turn_id} has not finished")
        terminal = TerminalFrame.model_validate(row.terminal)
        contract: Contract | None
        if profile is None:
            contract = output_contract(await self._agent_output_schema(row.agent_id))
        else:
            resolved = self.registry.find(profile)
            contract = None if resolved is None else resolved.output_model
        output = None
        if contract is not None and terminal.status == "done" and terminal.question is None:
            try:
                output = contract.model_validate_json(terminal.text)
            except ValidationError:
                output = None
        return SpawnResult(
            turn_id=turn_id,
            conversation_id=row.conversation_id,
            output=output,
            terminal=terminal,
            untrusted=self._untrusted_output(profile),
        )

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
        """Hold this turn until each named child has committed a terminal, and report status and
        final text. This is for a tool that must answer with its child's result inside its own
        call and bounds the hold itself — the browser task, which times out and cancels. A parent
        with nothing to do but wait ends its turn instead: the child delivers its own output
        through `SubagentResult`, so waiting is never how a result is collected."""
        profiles: dict[UUID, str | None] = {}
        for turn_id in turn_ids:
            profiles[turn_id] = await self._require_child(turn_id)
        statuses: list[SubagentStatus] = []
        for turn_id in turn_ids:
            terminal = await self._await_terminal(turn_id)
            statuses.append(
                SubagentStatus(
                    turn_id=turn_id,
                    status=terminal.status,
                    text=terminal.text,
                    untrusted=self._untrusted_output(profiles[turn_id]),
                )
            )
        return tuple(statuses)

    async def cancel(self, turn_id: UUID) -> SubagentStatus:
        """Cancel a running child through the shared `cancel_one_turn` primitive — cancel its
        durable workflow, then commit its cancelled terminal — and report the turn's status. A child
        that already finished keeps its own terminal. Any turns the child itself spawned are
        cancelled by the reconciler once this child's cancelled terminal lands. Refuses a turn id
        not a child of this parent."""
        await self._require_child(turn_id)
        await cancel_one_turn(self.client, self.sessions, turn_id)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one()
        text = "" if row.terminal is None else TerminalFrame.model_validate(row.terminal).text
        return SubagentStatus(turn_id=turn_id, status=row.status, text=text)

    async def message(
        self,
        turn_id: UUID,
        text: str,
        dedup_key: str,
        *,
        requesting_message_ref: UUID | None = None,
    ) -> SubagentStatus:
        """Send a child a follow-up through the same internal admission its own children's results
        ride. A child whose turn is in flight takes `text` as an arrival on its conversation,
        drained before its next model round — the tool-call boundary — so a correction reaches a
        multi-round run mid-flight rather than after it has merged; an idle child takes it as its
        next turn, with its accumulated transcript as prior context. Either way the follow-up
        continues the child under its own contract and delivers its result to this conversation.
        The returned status is the admitted turn's: `running` says the turn in flight holds the
        message, `queued` that it runs next. Admission is idempotent under `dedup_key` on both
        paths, so a re-run of the messaging tool step (crash recovery) reconnects to the arrival
        or turn it already admitted. Refuses a turn id that is not a child of this parent,
        mirroring cancel, and a profile this registry no longer holds. That last check is the
        admission's, not the child's: the follow-up carries the profile of the child it continues,
        so admitting one whose profile nothing resolves queues a turn that can only die in its own
        setup, where the caller that asked for it is no longer there to be told. An agent child
        carries no profile and nothing to resolve — its agent row cannot vanish."""
        profile = await self._require_child(turn_id)
        if profile is not None:
            self.registry.get(profile)
        if self.invoker is None:
            raise RuntimeError("messaging a spawn requires the turn invoker")
        async with workspace_tx() as connection:
            child = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.runtime_config,
                        tables.turn.c.model_accounts,
                        tables.turn.c.member_id,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one()
            runtime_config = (
                None
                if child.runtime_config is None
                else TurnRuntimeConfig.model_validate(child.runtime_config)
            )
            await self._require_spend(
                connection,
                (
                    runtime_config.model
                    if runtime_config is not None and runtime_config.model is not None
                    else self._profile_model(
                        child.subagent_profile,
                        tuple(
                            ModelAccountCapability.model_validate(account)
                            for account in child.model_accounts
                        ),
                    )
                ),
                child.agent_id,
            )
        admitted = await self.invoker.invoke(
            child.conversation_id,
            child.agent_id,
            text,
            dedup_key,
            context=(
                None
                if requesting_message_ref is None
                else TurnContext(requesting_message_ref=requesting_message_ref)
            ),
            runtime_config=runtime_config,
            model_accounts=tuple(
                ModelAccountCapability.model_validate(account) for account in child.model_accounts
            ),
            acting_member_id=child.member_id,
        )
        if admitted is None:
            raise RuntimeError("follow-up admission answered no turn")
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == admitted)
                )
            ).scalar_one()
        return SubagentStatus(turn_id=admitted, status=status, text="")

    async def _resolve(self, target: str) -> SubagentProfile | AgentTarget:
        kind, qualified, bare = target.partition(":")
        if qualified and kind == PROFILE_TARGET_KIND:
            profile = self.registry.find(bare)
            if profile is None:
                raise UnknownSpawnTarget(target, self._profile_names(), await self._agent_names())
            return profile
        if qualified and kind == AGENT_TARGET_KIND:
            agent = await self._agent_target(bare)
            if agent is None:
                raise UnknownSpawnTarget(target, self._profile_names(), await self._agent_names())
            return agent
        profile = self.registry.find(target)
        agent = await self._agent_target(target)
        if profile is not None and agent is not None:
            raise AmbiguousSpawnTarget(target)
        if profile is not None:
            return profile
        if agent is not None:
            return agent
        raise UnknownSpawnTarget(target, self._profile_names(), await self._agent_names())

    def _validated(self, target: str, contract: Contract, payload: dict[str, Any]) -> str:
        """The contract's own error names a type the caller never sees, so a caller read its own
        mistake as a platform fault and repeated the call."""
        try:
            return contract.model_validate(payload).model_dump_json()
        except ValidationError as error:
            faults = "; ".join(
                f"{'.'.join(str(part) for part in fault['loc']) or 'payload'}: {fault['msg']}"
                for fault in error.errors()
            )
            raise SpawnPayloadRejected(target, payload_keys(contract), faults) from error

    async def _existing_agent_spawn(
        self, turn_id: UUID, target: str
    ) -> tuple[AgentTarget, str] | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.inbound,
                        tables.agent.c.id,
                        tables.agent.c.name.label("agent_name"),
                        tables.agent.c.archived_name,
                        tables.agent.c.input_schema,
                        tables.agent.c.output_schema,
                    )
                    .join(tables.agent, tables.agent.c.id == tables.turn.c.agent_id)
                    .where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None or row.subagent_profile is not None:
            return None
        original_name = row.archived_name or row.agent_name
        if row.parent_turn_id != self.parent.id or target not in (
            original_name,
            f"{AGENT_TARGET_KIND}:{original_name}",
        ):
            raise ValueError("spawn dedup key belongs to another request")
        return (
            AgentTarget(
                id=row.id,
                name=original_name,
                input_schema=row.input_schema,
                output_schema=row.output_schema,
            ),
            row.inbound,
        )

    async def _existing_runtime_config(
        self, turn_id: UUID
    ) -> tuple[bool, TurnRuntimeConfig | None, tuple[ModelAccountCapability, ...]]:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.runtime_config,
                        tables.turn.c.model_accounts,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None:
            return False, None, ()
        if row.parent_turn_id != self.parent.id:
            raise ValueError("spawn dedup key belongs to another request")
        return (
            True,
            None
            if row.runtime_config is None
            else TurnRuntimeConfig.model_validate(row.runtime_config),
            tuple(ModelAccountCapability.model_validate(account) for account in row.model_accounts),
        )

    def _profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(profile.name for profile in self.registry.profiles))

    async def _agent_names(self) -> tuple[str, ...]:
        member_id = audience_member(self.audience)
        visible = (
            tables.agent.c.visibility == "workspace"
            if member_id is None
            else sa.or_(
                tables.agent.c.visibility == "workspace",
                tables.agent.c.owner_member_id == member_id,
            )
        )
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.name)
                    .where(
                        tables.agent.c.workspace_id == self.parent.workspace_id,
                        tables.agent.c.archived_at.is_(None),
                        visible,
                    )
                    .order_by(tables.agent.c.name)
                )
            ).all()
        return tuple(row.name for row in rows)

    async def _agent_target(self, name: str) -> AgentTarget | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.input_schema,
                        tables.agent.c.output_schema,
                    ).where(
                        tables.agent.c.workspace_id == self.parent.workspace_id,
                        tables.agent.c.name == name,
                        tables.agent.c.archived_at.is_(None),
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return AgentTarget(
            id=row.id,
            name=row.name,
            input_schema=row.input_schema,
            output_schema=row.output_schema,
        )

    async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.agent.c.output_schema).where(tables.agent.c.id == agent_id)
                )
            ).scalar_one()

    def _untrusted_output(self, profile: str | None) -> bool:
        """An agent child holds the member tool set, open-web readers included."""
        if profile is None:
            return True
        resolved = self.registry.find(profile)
        return True if resolved is None else resolved.untrusted_output

    async def _require_child(self, turn_id: UUID) -> str | None:
        """A handed-back result wakes a later turn, so a sibling turn qualifies; gated on the
        spawning turn alone it could never reach the child that woke it."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
            spawned_here = row is not None and row.parent_turn_id == self.parent.id
            if row is not None and not spawned_here:
                spawned_here = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(tables.turn)
                        .where(
                            tables.turn.c.id == row.parent_turn_id,
                            tables.turn.c.conversation_id == self.parent.conversation_id,
                        )
                    )
                ).scalar_one() == 1
        if row is None or row.parent_turn_id is None or not spawned_here:
            raise ValueError(f"{turn_id} is not a spawn of this conversation")
        return row.subagent_profile

    def _profile_model(
        self,
        profile: str | None,
        accounts: tuple[ModelAccountCapability, ...] = (),
    ) -> str | None:
        named = next((one for one in self.registry.profiles if one.name == profile), None)
        if named is None or not named.models:
            return None
        connected = {account.provider for account in accounts}
        return next(
            (model for model in named.models[:-1] if self.model_providers.get(model) in connected),
            named.models[-1],
        )

    def _child_runtime_config(self, model: str | None) -> TurnRuntimeConfig | None:
        """An unregistered model id would fail every attempt of the child's turn with nothing for
        the caller to repair."""
        inherited = self.parent.runtime_config
        if model is None:
            return inherited
        if inherited is not None and inherited.model is not None:
            raise SpawnModelRejected.pinned_tree(model, inherited.model)
        if self.models and model not in self.models:
            raise SpawnModelRejected.unknown(model, self.models)
        base = {} if inherited is None else inherited.model_dump(mode="json")
        base["model"] = model
        return TurnRuntimeConfig.model_validate(base)

    async def _require_spend(
        self, connection: AsyncConnection, model: str | None, agent_id: UUID | None = None
    ) -> None:
        decision = await self.spend.admit(
            connection,
            SPAWN_MOMENT,
            self.parent.workspace_id,
            agent_id=agent_id or self.parent.agent_id,
            model=model,
        )
        if decision.outcome == REJECT:
            raise SpendRefused(decision.message)
        if decision.outcome == PARK:
            raise TurnParked(decision.message)

    async def _admit(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        agent_id: UUID,
        profile: str | None,
        inherits_sandbox: bool,
        inbound: str,
        request_fingerprint: str,
        delivers_result: bool = False,
        name: str = "",
        agent_target: AgentTarget | None = None,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
        model_accounts: tuple[ModelAccountCapability, ...] = (),
        model: str | None = None,
    ) -> bool:
        async with workspace_tx() as connection:
            admitted = (
                await connection.execute(
                    sa.select(tables.turn.c.id).where(tables.turn.c.id == turn_id).with_for_update()
                )
            ).one_or_none()
            if admitted is None and agent_target is not None:
                await self._require_agent_spawn(
                    connection, agent_target, inbound, requester_member_id
                )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation)
                .values(
                    id=conversation_id,
                    workspace_id=self.parent.workspace_id,
                    agent_id=agent_id,
                    surface=SUBAGENT_SURFACE,
                    sandbox_conversation_id=(
                        (self.parent.sandbox_conversation_id or self.parent.conversation_id)
                        if inherits_sandbox
                        else None
                    ),
                    queue_key=str(turn_id),
                    member_id=audience_member(self.audience),
                    audience=str(self.audience),
                    title=name or conversation_name(inbound),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            await connection.execute(
                insert(tables.turn)
                .values(
                    id=turn_id,
                    workspace_id=self.parent.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="queued",
                    inbound=inbound,
                    admission_source=INTERNAL_ADMISSION,
                    speaker_member_id=None,
                    member_id=(
                        requester_member_id
                        if requester_member_id is not None
                        else self.parent.member_id
                    ),
                    context=(
                        None
                        if requesting_message_ref is None
                        else TurnContext(requesting_message_ref=requesting_message_ref).model_dump(
                            mode="json"
                        )
                    ),
                    terminal=None,
                    parent_turn_id=self.parent.id,
                    result_delivery=DELIVERY_PENDING if delivers_result else None,
                    spawn_delivers_result=delivers_result,
                    spawn_request_fingerprint=request_fingerprint,
                    subagent_profile=profile,
                    subagent_name=name or None,
                    traceparent=current_traceparent(),
                    runtime_config=(
                        None if runtime_config is None else runtime_config.model_dump(mode="json")
                    ),
                    model_accounts=[account.model_dump(mode="json") for account in model_accounts],
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            claimed = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.inbound,
                        tables.turn.c.spawn_delivers_result,
                        tables.turn.c.spawn_request_fingerprint,
                        tables.turn.c.subagent_name,
                        tables.turn.c.model_accounts,
                        tables.turn.c.context,
                    )
                    .where(tables.turn.c.id == turn_id)
                    .with_for_update()
                )
            ).one()
            if (
                claimed.parent_turn_id != self.parent.id
                or claimed.agent_id != agent_id
                or claimed.subagent_profile != profile
                or claimed.inbound != inbound
                or claimed.subagent_name != (name or None)
                or tuple(
                    ModelAccountCapability.model_validate(account)
                    for account in claimed.model_accounts
                )
                != model_accounts
                or (
                    None if claimed.context is None else TurnContext.model_validate(claimed.context)
                )
                != (
                    None
                    if requesting_message_ref is None
                    else TurnContext(requesting_message_ref=requesting_message_ref)
                )
                or (
                    claimed.spawn_delivers_result is not None
                    and claimed.spawn_delivers_result != delivers_result
                )
                or (
                    claimed.spawn_request_fingerprint is not None
                    and claimed.spawn_request_fingerprint != request_fingerprint
                )
            ):
                raise ValueError("spawn dedup key belongs to another request")
            if claimed.status != "queued":
                return False
            if admitted is None:
                await self._require_spend(connection, model, agent_id)
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
            )
        return True

    async def _require_agent_spawn(
        self,
        connection: AsyncConnection,
        target: AgentTarget,
        inbound: str,
        requester_member_id: UUID | None,
    ) -> None:
        row = (
            await connection.execute(
                sa.select(
                    tables.agent.c.owner_member_id,
                    tables.agent.c.visibility,
                    tables.agent.c.input_schema,
                    tables.agent.c.archived_at,
                )
                .where(
                    tables.agent.c.workspace_id == self.parent.workspace_id,
                    tables.agent.c.id == target.id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None or row.archived_at is not None:
            audience_id = audience_member(self.audience)
            visible = (
                tables.agent.c.visibility == "workspace"
                if audience_id is None
                else sa.or_(
                    tables.agent.c.visibility == "workspace",
                    tables.agent.c.owner_member_id == audience_id,
                )
            )
            names = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.name)
                        .where(
                            tables.agent.c.workspace_id == self.parent.workspace_id,
                            tables.agent.c.archived_at.is_(None),
                            visible,
                        )
                        .order_by(tables.agent.c.name)
                    )
                )
                .scalars()
                .all()
            )
            raise UnknownSpawnTarget(target.name, self._profile_names(), tuple(names))
        if row.visibility != "workspace":
            member_id = requester_member_id
            owned = row.owner_member_id is not None and row.owner_member_id == member_id
            if not owned and member_id is None:
                raise ValueError(AGENT_SPAWN_REFUSAL.format(name=target.name))
            if not owned:
                assert member_id is not None
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == self.parent.workspace_id,
                        tables.member.c.id == member_id,
                    )
                    .with_for_update()
                )
                if not await member_is_admin(connection, self.parent.workspace_id, member_id):
                    raise ValueError(AGENT_SPAWN_REFUSAL.format(name=target.name))
        input_contract(row.input_schema).model_validate_json(inbound)

    async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": EXPRESS_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "app_version": DBOS_APP_VERSION,
        }
        try:
            await self.client.enqueue_async(options, str(self.parent.workspace_id), str(turn_id))
        except asyncio.CancelledError:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
                )
            raise
        except Exception as error:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
                )
            log(
                "turn.enqueue_deferred",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )

    async def _await_terminal(self, turn_id: UUID) -> TerminalFrame:
        """The hub is lossy, so the durable poll still answers when the child's Terminal or Parked
        frame never arrives."""
        if self.hub is None:
            return await self._poll_terminal(turn_id)
        polled = asyncio.ensure_future(self._poll_terminal(turn_id))
        ended = asyncio.ensure_future(self._hub_end(self.hub, turn_id))
        try:
            await asyncio.wait((polled, ended), return_when=asyncio.FIRST_COMPLETED)
            if not polled.done():
                terminal = await self._terminal_or_park(turn_id)
                if terminal is not None:
                    return terminal
            return await polled
        finally:
            for task in (polled, ended):
                task.cancel()
            await asyncio.gather(polled, ended, return_exceptions=True)

    async def _hub_end(self, hub: Hub, turn_id: UUID) -> None:
        try:
            async for _cursor, frame in hub.subscribe(turn_id):
                if isinstance(frame, Terminal | Parked):
                    return
        except Exception as error:
            log("subagent.hub_wait_failed", turn_id=str(turn_id), error=repr(error))

    async def _poll_terminal(self, turn_id: UUID) -> TerminalFrame:
        workflow_id = str(turn_id)
        handle: WorkflowHandleAsync[str]
        while True:
            try:
                handle = await self.client.retrieve_workflow_async(workflow_id)
            except dbos_error.DBOSNonExistentWorkflowError:
                terminal = await self._terminal_or_park(turn_id)
                if terminal is not None:
                    return terminal
                running_attempt = await self._running_attempt(turn_id)
                if running_attempt is not None:
                    workflow_id = running_attempt
                await asyncio.sleep(SUBAGENT_WORKFLOW_POLL_SECONDS)
                continue
            try:
                outcome = await handle.get_result(
                    polling_interval_sec=SUBAGENT_WORKFLOW_POLL_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                terminal = await self._terminal_or_park(turn_id)
                if terminal is not None:
                    return terminal
                raise
            terminal = await self._terminal_or_park(turn_id)
            if terminal is not None:
                return terminal
            running_attempt = await self._running_attempt(turn_id)
            if running_attempt is None or running_attempt == workflow_id:
                raise RuntimeError(f"subagent workflow ended {outcome!r} without a terminal")
            workflow_id = running_attempt

    async def _running_attempt(self, turn_id: UUID) -> str | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.turn.c.running_attempt).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()

    async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None:
        if self.parent.spawned:
            return await self._await_terminal(turn_id)
        if self.hub is None:
            raise RuntimeError("an interruptible spawn requires the turn hub")
        terminal_wait = asyncio.create_task(self._await_terminal(turn_id))
        subscription = self.hub.subscribe(self.parent.id)
        arrival_wait = asyncio.ensure_future(anext(subscription))
        try:
            while True:
                await asyncio.wait(
                    (terminal_wait, arrival_wait), return_when=asyncio.FIRST_COMPLETED
                )
                if not arrival_wait.done():
                    return await terminal_wait
                try:
                    _, frame = await arrival_wait
                except StopAsyncIteration:
                    return await terminal_wait
                except Exception as error:
                    log(
                        "subagent.arrival_wait_failed",
                        turn_id=str(turn_id),
                        parent_turn_id=str(self.parent.id),
                        error=repr(error),
                    )
                    return await terminal_wait
                if isinstance(frame, ArrivalQueued) and await self._detach(
                    turn_id, frame.arrival_id
                ):
                    log(
                        "subagent.detached_on_arrival",
                        turn_id=str(turn_id),
                        parent_turn_id=str(self.parent.id),
                    )
                    return None
                if terminal_wait.done():
                    return await terminal_wait
                arrival_wait = asyncio.ensure_future(anext(subscription))
        finally:
            for task in (terminal_wait, arrival_wait):
                if not task.done():
                    task.cancel()
            await asyncio.gather(terminal_wait, arrival_wait, return_exceptions=True)

    async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal, tables.turn.c.status).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one()
        if row.terminal is not None:
            return TerminalFrame.model_validate(row.terminal)
        if row.status == PARKED:
            await cancel_one_turn(self.client, self.sessions, turn_id)
            raise SubagentParked(
                "subagent stopped before it finished and was cancelled; it held on a spend limit"
            )
        return None

    async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool:
        """`_deliver_to_parent` loads the child's row after its terminal commits, so exactly one
        path carries the result."""
        async with workspace_tx() as connection:
            moved = await connection.execute(
                sa.update(tables.turn)
                .values(result_delivery=DELIVERY_PENDING, updated_at=sa.func.now())
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.workspace_id == self.parent.workspace_id,
                    tables.turn.c.terminal.is_(None),
                    tables.turn.c.result_delivery.is_(None),
                    sa.exists(
                        sa.select(tables.inbound_message.c.id).where(
                            tables.inbound_message.c.id == arrival_id,
                            tables.inbound_message.c.workspace_id == self.parent.workspace_id,
                            tables.inbound_message.c.conversation_id == self.parent.conversation_id,
                            tables.inbound_message.c.admitted_turn_id == self.parent.id,
                            tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                            tables.inbound_message.c.consumed_turn_id.is_(None),
                        )
                    ),
                )
            )
        return moved.rowcount == 1


@dataclass(frozen=True)
class SubagentResult:
    """A finished child's output delivered to the conversation that spawned it. The parent takes it
    as an ordinary arrival — folded into its live turn at the next round boundary, or admitted as
    its next turn once that turn has ended — so a parent never holds a turn open waiting on a
    child. Delivery is keyed on the child turn, so a recovery re-run of the delivering execution
    settles on the arrival already posted rather than a second one, and it carries the same
    contract-validated output a foreground spawn returns: a child that ended any way but `done`,
    or whose final answer does not match its contract, arrives as that failure rather than as
    prose the parent would read as an answer. A child that ended asking arrives as its structured
    question — the need bubbles to the spawning conversation, which answers through `message_spawn`
    or re-raises with its own ask.

    The arrival is posted first and the child stamped `delivered` after, both here and in the sweep
    that finds what this path missed: a crash between the two leaves the child `pending` and the
    next pass re-posts under the same key, which admits nothing. Stamping first would let that same
    crash retire a delivery no parent ever received.

    The arrival carries the spawning turn's own runtime settings and acts for the member that turn
    acted for. A parent holding no live turn takes this arrival as a whole new turn, so the
    child-only model and model-account pins stay behind."""

    invoker: TurnInvoker
    registry: SubagentRegistry

    async def deliver(self, child: Turn) -> None:
        if child.result_delivery != DELIVERY_PENDING or child.parent_turn_id is None:
            return
        if child.terminal is None:
            raise RuntimeError("a spawn result is delivered only from a committed terminal")
        async with workspace_tx() as connection:
            parent = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.runtime_config,
                        tables.turn.c.model_accounts,
                        tables.turn.c.member_id,
                    ).where(
                        tables.turn.c.id == child.parent_turn_id,
                        tables.turn.c.workspace_id == child.workspace_id,
                    )
                )
            ).one()
            child_agent = (
                None
                if child.subagent_profile is not None
                else (
                    await connection.execute(
                        sa.select(tables.agent.c.name, tables.agent.c.output_schema).where(
                            tables.agent.c.id == child.agent_id
                        )
                    )
                ).one()
            )
        parent_runtime = (
            None
            if parent.runtime_config is None
            else TurnRuntimeConfig.model_validate(parent.runtime_config)
        )
        await self.invoker.invoke(
            parent.conversation_id,
            parent.agent_id,
            self._body(child, child_agent),
            f"{SPAWN_RESULT_KEY_PREFIX}{child.id}",
            holds_work_already_done=True,
            runtime_config=parent_runtime,
            acting_member_id=parent.member_id,
            context=(
                None
                if child.context is None or child.context.requesting_message_ref is None
                else TurnContext(requesting_message_ref=child.context.requesting_message_ref)
            ),
            model_accounts=tuple(
                ModelAccountCapability.model_validate(account) for account in parent.model_accounts
            ),
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(result_delivery=DELIVERY_DELIVERED, updated_at=sa.func.now())
                .where(
                    tables.turn.c.id == child.id,
                    tables.turn.c.result_delivery == DELIVERY_PENDING,
                )
            )

    def _body(self, child: Turn, child_agent: sa.Row | None) -> str:
        if child.terminal is None:
            raise RuntimeError("a spawn result is delivered only from a committed terminal")
        if child.subagent_profile is not None:
            target = f"{PROFILE_TARGET_KIND}:{child.subagent_profile}"
            resolved = self.registry.find(child.subagent_profile)
            contract: Contract | None = None if resolved is None else resolved.output_model
            walled = resolved is None or resolved.untrusted_output
            wall_label = child.subagent_profile
        else:
            if child_agent is None:
                raise RuntimeError("an agent child delivery carries no agent row")
            target = f"{AGENT_TARGET_KIND}:{child_agent.name}"
            contract = output_contract(child_agent.output_schema)
            walled = True
            wall_label = child_agent.name
        payload, status = self._payload(contract, child.terminal)
        if walled:
            payload = wall(wall_label, payload)
        guidance = model_route_guidance(child.terminal.model_route_changes)
        return (
            RESULT_OPEN.format(target=target, spawn_id=child.id, status=status)
            + f"\n{payload.replace(RESULT_CLOSE, RESULT_CLOSE_ESCAPE)}\n"
            + ("" if not guidance else f"{guidance}\n")
            + RESULT_CLOSE
            + f"\n\n{RESULT_REPLY_GUIDANCE}"
        )

    def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]:
        if terminal.status != "done":
            diagnostic = ": ".join(
                part
                for part in (terminal.error_class, terminal.error_message or terminal.text)
                if part
            )
            return diagnostic, terminal.status
        if terminal.question is not None:
            return terminal.question.model_dump_json(), STATUS_QUESTION
        if contract is None:
            return RESULT_UNKNOWN_PROFILE, "invalid"
        try:
            return contract.model_validate_json(terminal.text).model_dump_json(), "done"
        except ValidationError as error:
            faults = "; ".join(
                f"{'.'.join(str(part) for part in fault['loc']) or 'output'}: {fault['msg']}"
                for fault in error.errors(include_url=False, include_input=False)
            )
            return RESULT_INVALID.format(faults=faults), "invalid"
