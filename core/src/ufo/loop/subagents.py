"""Typed spawn: one verb that runs a subagent profile or a workspace agent as a child turn.

A profile names a prompt, a tool subset, and an input/output schema; a workspace agent row carries
its own prompt, model, tool allowlist, and declared (or default) I/O contract. `spawn` resolves the
target across both namespaces, validates the payload against the target's input contract, admits a
child turn linked to its parent (`parent_turn_id`) on its own conversation, and enqueues it on the
turn queue — a distinct partition, so the parent may await it without the queue serializing them
into a deadlock. Foreground awaits the child's terminal and returns its contract-validated output;
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import REJECT, BalanceGate
from ufo.audience import Audience, audience_member
from ufo.balance import BalanceExhausted
from ufo.cancellation import cancel_one_turn
from ufo.contracts import Contract, input_contract, output_contract
from ufo.db import workspace_tx
from ufo.delivery_register import DELIVERY_REGISTER_BLOCK
from ufo.ext.context import TurnInvoker
from ufo.ext.manifest import SubagentProfile
from ufo.ext.surface import conversation_name
from ufo.loop.prompts.render import (
    CITATION_BLOCK,
    CITATION_SLOT,
    DELIVERY_REGISTER_SLOT,
    PROMPT_VAR_RE,
    SKILL_INDEX_SLOT,
    render_skill_index,
)
from ufo.o11y import current_traceparent, log
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    DELIVERY_DELIVERED,
    DELIVERY_PENDING,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    PARKED,
    SPAWN_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    Turn,
    turn_id_for,
)
from ufo.seats import member_is_admin
from ufo.skills.runtime import CORE_SKILLS, LoadedSkill, loaded_context
from ufo.tools.context import (
    AmbiguousSpawnTarget,
    SpawnResult,
    SubagentStatus,
    UnknownSpawnTarget,
    UnknownSubagentProfile,
    UntrustedContentError,
)
from ufo.untrusted import wall

SUBAGENT_POLL_SECONDS = 0.1
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

SUBAGENT_OUTPUT_DISCIPLINE = (
    (Path(__file__).parent / "prompts" / "subagent_shell.md")
    .read_text()
    .strip()
    .replace(CITATION_SLOT, CITATION_BLOCK)
    .replace(DELIVERY_REGISTER_SLOT, DELIVERY_REGISTER_BLOCK)
)
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
    owner, payload contract, and answer contract."""

    id: UUID
    name: str
    owner_member_id: UUID | None
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
    if unresolved := frozenset(PROMPT_VAR_RE.findall(f"{body}\n\n{SUBAGENT_OUTPUT_DISCIPLINE}")):
        raise ValueError(f"subagent prompt has unresolved slots: {', '.join(sorted(unresolved))}")
    if preload:
        bodies = loaded_context(preload)
        if len(bodies) > PRELOAD_PROMPT_CHAR_BOUND:
            raise ValueError(
                f"preloaded skill bodies are {len(bodies)} chars, "
                f"over the {PRELOAD_PROMPT_CHAR_BOUND} bound"
            )
        body = f"{body}\n\n---\n\nPreloaded skill(s):\n\n{bodies}"
    return f"{body}\n\n{SUBAGENT_OUTPUT_DISCIPLINE}\n\n{FINISH_CONTRACT}"


@dataclass(frozen=True)
class Subagents:
    """The spawn workflow, bound to the turn that spawns: resolve the target, admit and enqueue a
    child turn, then (foreground) await and validate its output."""

    client: DBOSClient
    registry: SubagentRegistry
    parent: Turn
    audience: Audience
    key_slot_for: Callable[[str], str | None] | None = None
    billing_url: str | None = None
    requester_member_id: UUID | None = None

    def authorize(self, requester_member_id: UUID | None) -> "Subagents":
        return replace(self, requester_member_id=requester_member_id)

    @property
    def acting_member_id(self) -> UUID | None:
        """The member whose authority a spawn carries: the bound requester, else the turn's
        founding speaker, else the initiator a speakerless turn acts on behalf of — the same fold
        `ToolContext.acting_member_id` applies, so the ownership gate, the catalog, and the
        child's own stamp all read one member."""
        return (
            self.requester_member_id
            or self.parent.speaker_member_id
            or self.parent.on_behalf_of_member_id
        )

    async def spawn(
        self,
        target: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
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
        on and delivers itself."""
        resolved = await self._resolve(target)
        match resolved:
            case SubagentProfile():
                agent_id = self.parent.agent_id
                profile_name: str | None = resolved.name
                inherits_sandbox = True
                input_model: Contract = resolved.input_model
                output_model: Contract = resolved.output_model
                untrusted = resolved.untrusted_output
            case AgentTarget():
                if not await self._may_spawn(resolved.owner_member_id):
                    raise ValueError(AGENT_SPAWN_REFUSAL.format(name=resolved.name))
                agent_id = resolved.id
                profile_name = None
                inherits_sandbox = False
                input_model = input_contract(resolved.input_schema)
                output_model = output_contract(resolved.output_schema)
                untrusted = True
                background = True
                delivers_result = True
        typed_input = input_model.model_validate(payload)
        conversation_id = (
            uuid5(NAMESPACE_URL, f"{self.parent.id}/{dedup_key}")
            if dedup_key is not None
            else uuid4()
        )
        turn_id = turn_id_for(self.parent.workspace_id, conversation_id, 1)
        if await self._admit(
            conversation_id,
            turn_id,
            agent_id=agent_id,
            profile=profile_name,
            inherits_sandbox=inherits_sandbox,
            inbound=typed_input.model_dump_json(),
            delivers_result=delivers_result,
            name=name,
            model=_target_model(resolved),
        ):
            await self._enqueue(turn_id, conversation_id)
        if background:
            return SpawnResult(turn_id=turn_id, conversation_id=conversation_id, output=None)
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
        conversation with `text` as its inbound. The child's partition serializes it after the turn
        in flight (create-or-attach hands it the same sandbox), and the engine loads the child's
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
                await self._require_balance(
                    connection, self._profile_model(child.subagent_profile), child.agent_id
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
                        on_behalf_of_member_id=self.acting_member_id,
                        terminal=None,
                        parent_turn_id=self.parent.id,
                        result_delivery=(
                            DELIVERY_PENDING
                            if delivers_result or child.result_delivery is not None
                            else None
                        ),
                        subagent_profile=child.subagent_profile,
                        traceparent=current_traceparent(),
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
            earlier_queued = (
                await connection.execute(
                    sa.select(
                        sa.exists(
                            sa.select(tables.turn.c.id).where(
                                tables.turn.c.conversation_id == child.conversation_id,
                                tables.turn.c.status == "queued",
                                tables.turn.c.seq < followup_seq,
                            )
                        )
                    )
                )
            ).scalar_one()
            dispatch = followup_status == "queued" and not earlier_queued
            if dispatch:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                    .where(tables.turn.c.id == followup_id)
                )
        if dispatch:
            await self._enqueue(followup_id, child.conversation_id)
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

    def _profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(profile.name for profile in self.registry.profiles))

    async def _agent_names(self) -> tuple[str, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.name)
                    .where(tables.agent.c.workspace_id == self.parent.workspace_id)
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
                        tables.agent.c.owner_member_id,
                        tables.agent.c.input_schema,
                        tables.agent.c.output_schema,
                    ).where(
                        tables.agent.c.workspace_id == self.parent.workspace_id,
                        tables.agent.c.name == name,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return AgentTarget(
            id=row.id,
            name=row.name,
            owner_member_id=row.owner_member_id,
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

    async def _may_spawn(self, owner_member_id: UUID | None) -> bool:
        """The spawn side of ownership: the owner runs their own agent, and a workspace admin runs
        any — including the ownerless rows (main, provisioned) that are the admins'. This mirrors
        the portal's reach, which gives a non-admin only the main agent, their grants, and their
        own rows, so chat opens no agent the portal would refuse."""
        if owner_member_id is not None and owner_member_id == self.acting_member_id:
            return True
        if self.acting_member_id is None:
            return False
        async with workspace_tx() as connection:
            return await member_is_admin(
                connection, self.parent.workspace_id, self.acting_member_id
            )

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
            or row.on_behalf_of_member_id != self.acting_member_id
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

    async def _require_balance(
        self, connection: AsyncConnection, model: str | None, agent_id: UUID | None = None
    ) -> None:
        """Refuse to start work the prepaid balance cannot cover. A turn passes this line once, at
        its own admission; a turn that fans out asks again per child, so an exhausted workspace
        stops at the first one instead of buying a free round per helper. Only genuinely new work
        is asked: a recovery re-run that reconnects to a child already past `queued` never reaches
        here, so a refusal can never strand a child that has already run.

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
        delivers_result: bool = False,
        name: str = "",
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
                    on_behalf_of_member_id=self.acting_member_id,
                    terminal=None,
                    parent_turn_id=self.parent.id,
                    result_delivery=DELIVERY_PENDING if delivers_result else None,
                    subagent_profile=profile,
                    subagent_name=name or None,
                    traceparent=current_traceparent(),
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
                    )
                    .where(tables.turn.c.id == turn_id)
                    .with_for_update()
                )
            ).one()
            if claimed.on_behalf_of_member_id != self.acting_member_id:
                raise ValueError("spawn dedup key belongs to another member request")
            if claimed.status != "queued":
                return False
            await self._require_balance(connection, model, agent_id)
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
            )
        return True

    async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "queue_partition_key": str(conversation_id),
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
        while True:
            terminal = await self._terminal_or_park(turn_id)
            if terminal is not None:
                return terminal
            await asyncio.sleep(SUBAGENT_POLL_SECONDS)

    async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None:
        """The same wait, ended early by a member message waiting on the parent's conversation:
        None says the child was moved to the background, where it keeps running and hands back its
        own result, so the parent can answer the member now.

        The signal is the engine's own — an admitted `inbound_message` row no turn has drained is
        exactly what the parent's next arrival drain would fold — so the wait ends on the message
        the parent is about to read rather than on a clock, and one poll of the child's terminal
        carries the question.

        The child's terminal is read first, and the move is refused for a child that already
        committed one, so a message landing in the same instant the child finishes resolves to the
        child's result: the caller gets the answer it waited for, and the conversation is not woken
        by a result the parent already holds."""
        detachable = not self.parent.spawned
        while True:
            terminal = await self._terminal_or_park(turn_id)
            if terminal is not None:
                return terminal
            if detachable and await self._member_waiting() and await self._detach(turn_id):
                log(
                    "subagent.detached_on_arrival",
                    turn_id=str(turn_id),
                    parent_turn_id=str(self.parent.id),
                )
                return None
            await asyncio.sleep(SUBAGENT_POLL_SECONDS)

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

    async def _member_waiting(self) -> bool:
        """Whether a member has spoken into the parent's conversation and nothing has read it yet.
        Only member rows count: a child's own delivered result and an extension's prompt are work
        the system posted to itself, and a wait ended by its own child's arrival would detach the
        very spawn it is waiting on. A subagent's conversation is its parent's private channel that
        no member speaks into, which is why a spawned parent never asks."""
        async with workspace_tx() as connection:
            waiting = (
                await connection.execute(
                    sa.select(tables.inbound_message.c.id)
                    .where(
                        tables.inbound_message.c.workspace_id == self.parent.workspace_id,
                        tables.inbound_message.c.conversation_id == self.parent.conversation_id,
                        tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .limit(1)
                )
            ).first()
        return waiting is not None

    async def _detach(self, turn_id: UUID) -> bool:
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
                    tables.turn.c.terminal.is_(None),
                    tables.turn.c.result_delivery.is_(None),
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
    crash retire a delivery no parent ever received."""

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
                    sa.select(tables.turn.c.conversation_id, tables.turn.c.agent_id).where(
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
            on_behalf_of_member_id=child.on_behalf_of_member_id,
            holds_work_already_done=True,
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
