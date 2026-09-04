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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
from ufo.runtime.authority import ExecutionAuthority, MemberAuthority, authority_member_id
from ufo.runtime.billing.accounting import REJECT, BalanceGate
from ufo.runtime.billing.balance import BalanceExhausted
from ufo.runtime.ext.context import TurnInvoker
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.ext.surface import conversation_name
from ufo.runtime.hub import ArrivalQueued, Hub
from ufo.runtime.prompts.render import (
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
    SpawnNeedsOwnModelKey,
    SpawnPayloadRejected,
    SpawnResult,
    SubagentStatus,
    UnknownSpawnTarget,
    UnknownSubagentProfile,
    UntrustedContentError,
)
from ufo.runtime.turns.audience import Audience, audience_member
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.turns.contracts import (
    Contract,
    input_contract,
    output_contract,
    payload_keys,
)
from ufo.runtime.turns.dispatch import dispatch_next_turn
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
    TerminalFrame,
    Turn,
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


def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None:
    """The model a spawn target pins, or None to weigh the child under the agent that will run it.
    A profile may pin one — possibly another provider's — while a workspace agent carries its model
    on its own row, which the gate reads from the agent id the child is admitted under."""
    match resolved:
        case SubagentProfile():
            return resolved.model
        case _:
            return None


def subagent_system_prompt(
    profile: SubagentProfile,
    *,
    skills: Sequence[tuple[str, str]] = CORE_SKILL_INDEX,
    preload: tuple[LoadedSkill, ...] = (),
) -> str:
    """The child's system prompt: the profile's own instructions with its `{{skill_index}}` slot
    filled from the loadable-skill index, then any preloaded skills' instructions, then the shared
    output discipline (the shared delivery register, citation, and formatting rules), then the
    output contract — the child ends its turn by calling the engine's finish tool, whose input
    schema is the profile's output model, and a preloaded skill's own answer-formatting instructions
    can never displace that contract from the prompt's last word. A slot the profile leaves unfilled
    fails loud rather than reaching the model as a literal brace; preloaded bodies over the char
    bound fail loud rather than blowing the model call. Skill bodies are injected after slot
    validation — a literal brace inside a skill is content, never an unfilled slot."""
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
    )
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
    authority: ExecutionAuthority
    hub: Hub | None = None
    key_slot_for: Callable[[str], str | None] | None = None
    billing_url: str | None = None
    connect_url: str | None = None
    """The model ids this deploy's registry serves, the closed set a spawn's own model pin holds
    to. The registry is fixed at boot, so the ids ride here as data rather than as a handle
    `subagents` would have to import the registry to hold."""
    models: tuple[str, ...] = ()
    """Whether this deploy can hold a member's own provider account at all. A deploy carrying no
    extension that connects one has no member to refuse: the profile runs on the deploy's own key
    there, so refusing work nobody could ever enable would take the capability away entirely."""
    member_accounts_connectable: bool = True

    def authorize(self, authority: ExecutionAuthority) -> "Subagents":
        """Bind a tool call's authority to the spawns it makes: the call's own bound requester, else
        the authority the spawning turn already holds. A call that names no requester carries
        workspace authority even on a member-spoken turn, and a spawn there is still that speaker's
        — the ownership gate, the catalog, and the child's own stamp read one authority."""
        match authority:
            case MemberAuthority():
                return replace(self, authority=authority)
            case _:
                return replace(self, authority=self.parent.authority)

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
        turn resolves through, and the model the balance gate weighs the child under. An id the
        registry does not serve is refused here rather than at the child's first round, and so is a
        pin asked for under a turn tree that already carries one — that pin is the member's own
        admitted selection, which the tree keeps."""
        runtime_config = self._child_runtime_config(model)
        conversation_id = (
            uuid5(NAMESPACE_URL, f"{self.parent.id}/{dedup_key}")
            if dedup_key is not None
            else uuid4()
        )
        turn_id = turn_id_for(self.parent.workspace_id, conversation_id, 1)
        replay = None if dedup_key is None else await self._existing_agent_spawn(turn_id, target)
        resolved = replay[0] if replay is not None else await self._resolve(target)
        match resolved:
            case SubagentProfile():
                if resolved.needs_own_model_key and model is not None:
                    raise SpawnModelRejected.own_account(model, target)
                if (
                    resolved.needs_own_model_key
                    and self.member_accounts_connectable
                    and not await ws_current().member_holds_own_model_key(self.authority)
                ):
                    raise SpawnNeedsOwnModelKey(target, self.connect_url)
                agent_id = self.parent.agent_id
                profile_name: str | None = resolved.name
                inherits_sandbox = True
                input_model: Contract = resolved.input_model
                output_model: Contract = resolved.output_model
                untrusted = resolved.untrusted_output
                agent_target: AgentTarget | None = None
            case AgentTarget():
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
            runtime_config=runtime_config,
            model=(
                runtime_config.model
                if runtime_config is not None and runtime_config.model is not None
                else _target_model(resolved)
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
            await cancel_one_turn(self.client, turn_id)
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
        await cancel_one_turn(self.client, turn_id)
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
        self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool = False
    ) -> SubagentStatus:
        """Queue a follow-up for a background child by admitting the next turn on the child's own
        conversation with `text` as its inbound. The exit handoff runs it after the turn in
        flight (create-or-attach hands it the same sandbox), and the engine loads the child's
        accumulated transcript as prior context — so the follow-up continues the child under its
        own contract rather than starting fresh. Admission is idempotent through
        `turn.idempotency_key`: a re-run of the messaging tool step (crash recovery) finds the turn
        it already admitted under `dedup_key` instead of admitting a second one at the next seq.
        `delivers_result` marks the follow-up delivering even when the child was awaited foreground
        — the caller that answers a bubbled question ends its own turn, so the continuation's
        answer must arrive as a delivery or not at all. Returns the follow-up's status; refuses a
        turn id that is not a child of this parent, mirroring cancel, and a profile this registry
        no longer holds. That last check is the admission's, not the child's: the follow-up
        carries the profile of the child it continues, so admitting one whose profile nothing
        resolves queues a turn that can only die in its own setup, where the caller that asked for
        it is no longer there to be told. An agent child carries no profile and nothing to
        resolve — its agent row cannot vanish."""
        profile = await self._require_child(turn_id)
        if profile is not None:
            self.registry.get(profile)
        async with workspace_tx() as connection:
            child = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.result_delivery,
                        tables.turn.c.runtime_config,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one()
            await connection.execute(
                sa.select(tables.conversation.c.id)
                .where(tables.conversation.c.id == child.conversation_id)
                .with_for_update()
            )
            followup = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.seq,
                        tables.turn.c.status,
                    ).where(
                        tables.turn.c.workspace_id == self.parent.workspace_id,
                        tables.turn.c.idempotency_key == dedup_key,
                    )
                )
            ).one_or_none()
            if followup is not None and followup.conversation_id != child.conversation_id:
                raise ValueError("dedup key belongs to another follow-up")
            if followup is None:
                runtime_config = (
                    None
                    if child.runtime_config is None
                    else TurnRuntimeConfig.model_validate(child.runtime_config)
                )
                await self._require_balance(
                    connection,
                    (
                        runtime_config.model
                        if runtime_config is not None and runtime_config.model is not None
                        else self._profile_model(child.subagent_profile)
                    ),
                    child.agent_id,
                )
                followup_seq = (
                    await connection.execute(
                        sa.select(sa.func.max(tables.turn.c.seq)).where(
                            tables.turn.c.conversation_id == child.conversation_id
                        )
                    )
                ).scalar_one() + 1
                followup_id = turn_id_for(
                    self.parent.workspace_id, child.conversation_id, followup_seq
                )
                followup_status = "queued"
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=followup_id,
                        workspace_id=self.parent.workspace_id,
                        conversation_id=child.conversation_id,
                        agent_id=child.agent_id,
                        seq=followup_seq,
                        status=followup_status,
                        inbound=text,
                        admission_source=INTERNAL_ADMISSION,
                        idempotency_key=dedup_key,
                        speaker_member_id=None,
                        on_behalf_of_member_id=authority_member_id(self.authority),
                        terminal=None,
                        parent_turn_id=self.parent.id,
                        result_delivery=(
                            DELIVERY_PENDING
                            if delivers_result or child.result_delivery is not None
                            else None
                        ),
                        subagent_profile=child.subagent_profile,
                        traceparent=current_traceparent(),
                        runtime_config=child.runtime_config,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                followup_id, followup_seq, followup_status = (
                    followup.id,
                    followup.seq,
                    followup.status,
                )
        if followup_status == "queued":
            await dispatch_next_turn(self.client, child.conversation_id)
        return SubagentStatus(turn_id=followup_id, status=followup_status, text="")

    async def _resolve(self, target: str) -> SubagentProfile | AgentTarget:
        """The target a spawn names, across both namespaces. A `profile:`/`agent:` prefix is exact;
        a bare name both kinds hold is refused naming the qualified forms, and a bare name neither
        holds is refused naming what is spawnable."""
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
        """The child's inbound, or a refusal the caller can repair. The contract's own error names
        a type the caller never sees and a value the tool may have defaulted for it, so a caller
        that sent the wrong shape — or no payload at all — reads its own mistake as a platform
        fault and repeats the call. `SpawnPayloadRejected` answers with the target and the keys
        that target takes, the way an unknown target answers with what is spawnable."""
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
                        tables.turn.c.on_behalf_of_member_id,
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
        if row.on_behalf_of_member_id != authority_member_id(self.authority):
            raise ValueError("spawn dedup key belongs to another member request")
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

    def _profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(profile.name for profile in self.registry.profiles))

    async def _agent_names(self) -> tuple[str, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.name)
                    .where(
                        tables.agent.c.workspace_id == self.parent.workspace_id,
                        tables.agent.c.archived_at.is_(None),
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
        """Trust fails closed for profiles: a child whose profile is no longer registered walls as
        untrusted rather than passing its output through as instructions. An agent child (no
        profile) always walls: it holds the whole member-facing tool set, open-web readers
        included, so its answer derives from whatever it read — the vouched prompt does not vouch
        the content."""
        if profile is None:
            return True
        resolved = self.registry.find(profile)
        return True if resolved is None else resolved.untrusted_output

    async def _require_child(self, turn_id: UUID) -> str | None:
        """The profile of a child this conversation delegated (None for an agent child), or a
        refusal. A child that hands its result back wakes a *later* turn, and that turn is the one
        holding the id the result named — gated on the spawning turn alone it could never message
        or cancel the very child that woke it. So a sibling turn of the same conversation
        qualifies, and the authority check, which is what actually walls one member's child off
        from another's, is unchanged."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.on_behalf_of_member_id,
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
        if (
            row is None
            or row.parent_turn_id is None
            or not spawned_here
            or row.on_behalf_of_member_id != authority_member_id(self.authority)
        ):
            raise ValueError(f"{turn_id} is not a spawn of this conversation")
        return row.subagent_profile

    def _profile_model(self, profile: str | None) -> str | None:
        """The model the named profile pins, or None to weigh the child under its agent's. A profile
        the registry no longer declares answers None rather than raising: this is the billing model
        for a child that already exists, and a follow-up to a live child is not the place to
        discover a manifest changed under it."""
        named = next((one for one in self.registry.profiles if one.name == profile), None)
        return named.model if named is not None else None

    def _child_runtime_config(self, model: str | None) -> TurnRuntimeConfig | None:
        """The runtime config the child turn is admitted with: the parent's, with a requested model
        taking the pin an unpinned tree leaves empty. A spawn that asks for no model inherits the
        parent's config whole, as it always has — the pin is the one choice a caller makes per
        child, and the internet and environment pins around it stay the turn tree's.

        A tree that already carries a model pin keeps it. That pin is the member's own selection
        (`ufo --model`, `x-ufo-model`), which replaces every agent and subagent profile model in the
        turn tree, so a spawn under it is refused rather than run on a model the member excluded —
        and refused here, where the caller can drop its argument and retry.

        The id is checked against the deploy's registry here, before any row is written: an
        unregistered id would reach the child's setup, fail every attempt of that turn, and leave
        the caller nothing to repair."""
        inherited = self.parent.runtime_config
        if model is None:
            return inherited
        if inherited is not None and inherited.model is not None:
            raise SpawnModelRejected.pinned_tree(model, inherited.model)
        if self.models and model not in self.models:
            raise SpawnModelRejected.unknown(model, self.models)
        base = {} if inherited is None else inherited.model_dump(mode="json")
        return TurnRuntimeConfig.model_validate({**base, "model": model})

    async def _require_balance(
        self, connection: AsyncConnection, model: str | None, agent_id: UUID | None = None
    ) -> None:
        """Refuse to start work the prepaid balance cannot cover. A turn passes this line once, at
        its own admission; a turn that fans out asks again per child, so an exhausted workspace
        stops at the first one instead of buying a free round per helper. Only genuinely new work
        is asked: an exact recovery re-run reconnects to the admitted row without re-deciding its
        mutable balance or authority, so a later change cannot strand work admission already
        accepted.

        The child is weighed under the model that will answer it — its profile's where the profile
        pins one, otherwise the model of the agent the child runs as, which is the child's own agent
        and not this parent's. A profile may pin a different provider, and a spawned agent may carry
        a different model; weighing either under the parent's would exempt a call the platform pays
        for in full."""
        admits = await BalanceGate(self.parent.workspace_id, self.billing_url).admits(
            connection, agent_id or self.parent.agent_id, self.key_slot_for, model=model
        )
        if admits.outcome == REJECT:
            raise BalanceExhausted(admits.message)

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
        runtime_config: TurnRuntimeConfig | None = None,
        model: str | None = None,
    ) -> bool:
        """Insert the child conversation and its first turn, stamped with the spawning turn's
        traceparent so the child's span joins the parent's trace. A profile child runs in the
        spawning turn's own sandbox conversation — it runs where the turn that spawned it runs, so
        the files it writes are the ones the parent reads, and a grandchild carries the same one.
        An agent child gets its own: the target's sandbox size and internet policy are its own
        settings, and a shared filesystem under a different egress policy would bypass them. The
        inserts do nothing on conflict, so a deterministic (`dedup_key`) child re-admitted by a
        recovery re-run of the spawning step settles on the rows already there — the first run's
        child stands, never a duplicate."""
        async with workspace_tx() as connection:
            admitted = (
                await connection.execute(
                    sa.select(tables.turn.c.id).where(tables.turn.c.id == turn_id).with_for_update()
                )
            ).one_or_none()
            if admitted is None and agent_target is not None:
                await self._require_agent_spawn(connection, agent_target, inbound)
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
                    on_behalf_of_member_id=authority_member_id(self.authority),
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
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            claimed = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.on_behalf_of_member_id,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.inbound,
                        tables.turn.c.spawn_delivers_result,
                        tables.turn.c.spawn_request_fingerprint,
                        tables.turn.c.subagent_name,
                    )
                    .where(tables.turn.c.id == turn_id)
                    .with_for_update()
                )
            ).one()
            if claimed.on_behalf_of_member_id != authority_member_id(self.authority):
                raise ValueError("spawn dedup key belongs to another member request")
            if (
                claimed.parent_turn_id != self.parent.id
                or claimed.agent_id != agent_id
                or claimed.subagent_profile != profile
                or claimed.inbound != inbound
                or claimed.subagent_name != (name or None)
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
                await self._require_balance(connection, model, agent_id)
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
    ) -> None:
        row = (
            await connection.execute(
                sa.select(
                    tables.agent.c.owner_member_id,
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
            names = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.name)
                        .where(
                            tables.agent.c.workspace_id == self.parent.workspace_id,
                            tables.agent.c.archived_at.is_(None),
                        )
                        .order_by(tables.agent.c.name)
                    )
                )
                .scalars()
                .all()
            )
            raise UnknownSpawnTarget(target.name, self._profile_names(), tuple(names))
        member_id = authority_member_id(self.authority)
        owned = row.owner_member_id is not None and row.owner_member_id == member_id
        if not owned:
            if member_id is None:
                raise ValueError(AGENT_SPAWN_REFUSAL.format(name=target.name))
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
        """Wait for the child's terminal, and end the wait if the child parks.

        A park writes no terminal and clears on nothing this parent can do, so waiting through one
        holds the member's turn open for as long as the process lives — the request never answers
        and the conversation admits nothing else.

        The child is cancelled rather than left parked. A parked child the parent has stopped
        waiting on would otherwise be resumed by the dispatcher, re-run at full cost, and finish
        into a caller that is long gone. Cancelling is also what makes the raise honest: a park has
        no stored reason, so the parent reports that the child stopped without finishing rather
        than guessing which line stopped it."""
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
        """The same wait, ended early by a member message waiting on the parent's conversation:
        None says the child was moved to the background, where it keeps running and hands back its
        own result, so the parent can answer the member now.

        Member admission publishes the durable arrival row's id through the turn hub. The wait
        validates that exact row while stamping the child for delivery in one transaction, so a
        stale replay after the parent absorbed the message does nothing and no database poll runs
        while nobody speaks.

        The guarded move is the race resolution. It refuses after the child's terminal commits, so
        that terminal answers inline; this waiter owns the subscription and settles each move
        before reading the next arrival, without cancelling a transaction."""
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
            await cancel_one_turn(self.client, turn_id)
            raise SubagentParked(
                "subagent stopped before it finished and was cancelled; it held on a spend limit"
            )
        return None

    async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool:
        """Hand the child the delivery its awaiting parent will no longer perform, so a child nobody
        blocks on still reaches the conversation — the same `DELIVERY_PENDING` stamp a spawn asked
        for in the background carries from admission.

        False says the child committed its terminal first: the guard is the whole race resolution,
        since a stamp landing on a finished child would post an arrival for a result this call is
        about to return inline. A stamp that does land is read by `_deliver_to_parent`, which loads
        the child's row after the terminal commits, so exactly one of the two paths carries the
        result."""
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

    The arrival carries the spawning turn's own runtime config, never the child's. A parent holding
    no live turn takes this arrival as a whole new turn admitted under that config, so delivering
    the child's would run the parent — and any member message folding into that turn — on a model
    the caller pinned for the child alone."""

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
        await self.invoker.invoke(
            parent.conversation_id,
            parent.agent_id,
            self._body(child, child_agent),
            f"{SPAWN_RESULT_KEY_PREFIX}{child.id}",
            authority=child.authority,
            holds_work_already_done=True,
            runtime_config=(
                None
                if parent.runtime_config is None
                else TurnRuntimeConfig.model_validate(parent.runtime_config)
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
        """The child's answer inside the envelope naming which child answered. A profile no longer
        registered still delivers: the parent learns its child ended and with what, which is the
        whole reason it ended its own turn, and an unresolvable profile walls the payload rather
        than reaching the parent as prose. `RESULT_CLOSE` is escaped inside the payload for the
        same reason the wall escapes its own: content that closes the element holding it continues
        as instructions to the parent."""
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
        return (
            RESULT_OPEN.format(target=target, spawn_id=child.id, status=status)
            + f"\n{payload.replace(RESULT_CLOSE, RESULT_CLOSE_ESCAPE)}\n"
            + RESULT_CLOSE
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
