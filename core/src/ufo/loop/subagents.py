"""Typed subagents: a registry of profiles and the spawn that runs one as a child turn.

A profile names a prompt, a tool subset, and an input/output schema. `spawn` validates the payload
against the input schema, admits a child turn linked to its parent (`parent_turn_id`) on its own
conversation, and enqueues it on the turn queue — a distinct partition, so the parent may await it
without the queue serializing them into a deadlock. Foreground awaits the child's terminal and
returns its schema-validated output; background returns the child turn id at once and the child
delivers its own result through `SubagentResult` when it finishes."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.audience import Audience, audience_member
from ufo.cancellation import cancel_one_turn
from ufo.db import workspace_tx
from ufo.ext.context import TurnInvoker
from ufo.ext.manifest import SubagentProfile
from ufo.ext.surface import conversation_name
from ufo.loop.prompts.render import (
    CITATION_BLOCK,
    CITATION_SLOT,
    DELIVERY_REGISTER_BLOCK,
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
    SUBAGENT_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    Turn,
    turn_id_for,
)
from ufo.skills.runtime import CORE_SKILLS, LoadedSkill, loaded_context
from ufo.tools.context import (
    SpawnResult,
    SubagentStatus,
    UnknownSubagentProfile,
    UntrustedContentError,
)
from ufo.untrusted import wall

SUBAGENT_POLL_SECONDS = 0.1
RESULT_OPEN = '<subagent_result profile="{profile}" subagent_id="{subagent_id}" status="{status}">'
RESULT_CLOSE = "</subagent_result>"
RESULT_CLOSE_ESCAPE = "&lt;/subagent_result&gt;"
RESULT_UNKNOWN_PROFILE = (
    "The subagent's profile is no longer registered, so its answer could not be checked "
    "against a schema and is withheld."
)
RESULT_INVALID = (
    "The subagent's final answer does not match its output schema, so it was dropped rather than "
    "delivered. Failures: {faults}"
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
        for profile in self.profiles:
            if profile.name == name:
                return profile
        valid = ", ".join(sorted(profile.name for profile in self.profiles))
        raise UnknownSubagentProfile(
            f"unknown subagent profile {name!r}; valid profiles are: {valid}"
        )


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
    """The spawn workflow, bound to the turn that spawns: resolve the profile, admit and enqueue a
    child turn, then (foreground) await and validate its output."""

    client: DBOSClient
    registry: SubagentRegistry
    parent: Turn
    audience: Audience
    requester_member_id: UUID | None = None

    def authorize(self, requester_member_id: UUID | None) -> "Subagents":
        return replace(self, requester_member_id=requester_member_id)

    @property
    def acting_member_id(self) -> UUID | None:
        return self.requester_member_id or self.parent.on_behalf_of_member_id

    async def spawn(
        self,
        profile: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
    ) -> SpawnResult:
        """Admit and enqueue a child turn. With `dedup_key`, the child's conversation (and so its
        turn id, the DBOS workflow id) is derived from the parent turn and the key, so a re-run of
        the spawning tool step reconnects: `_admit` is idempotent, DBOS dedups the re-enqueue on the
        existing workflow id, and `_await_terminal` returns a child that already finished at once —
        completed branches are memoized by their own durable terminal, never respawned or rebilled.
        Without a key, each call mints a fresh random child."""
        resolved = self.registry.get(profile)
        typed_input = resolved.input_model.model_validate(payload)
        conversation_id = (
            uuid5(NAMESPACE_URL, f"{self.parent.id}/{dedup_key}")
            if dedup_key is not None
            else uuid4()
        )
        turn_id = turn_id_for(self.parent.workspace_id, conversation_id, 1)
        if await self._admit(
            conversation_id, turn_id, profile, typed_input.model_dump_json(), delivers_result
        ):
            await self._enqueue(turn_id, conversation_id)
        if background:
            return SpawnResult(turn_id=turn_id, conversation_id=conversation_id, output=None)
        terminal = await self._await_terminal(turn_id)
        if terminal.status != "done":
            diagnostic = ": ".join(
                part
                for part in (terminal.error_class, terminal.error_message or terminal.text)
                if part
            )
            raise RuntimeError(
                f"subagent {profile!r} turn ended {terminal.status}"
                + (f" ({diagnostic})" if diagnostic else "")
            )
        try:
            output = resolved.output_model.model_validate_json(terminal.text)
        except ValidationError as error:
            if resolved.untrusted_output:
                raise UntrustedContentError(
                    f"subagent {profile!r} returned output that failed validation: {error}"
                ) from error
            raise
        return SpawnResult(
            turn_id=turn_id,
            conversation_id=conversation_id,
            output=output,
            terminal=terminal,
            untrusted=resolved.untrusted_output,
        )

    async def result(self, turn_id: UUID) -> SpawnResult:
        """Read the exact terminal and validated output of a finished child this conversation
        spawned. A failed child or invalid output returns no output, so a host-side consumer can
        represent that terminal without trusting the model to relay its result."""
        profile = await self._require_child(turn_id)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.conversation_id, tables.turn.c.terminal).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one()
        if row.terminal is None:
            raise ValueError(f"subagent {turn_id} has not finished")
        terminal = TerminalFrame.model_validate(row.terminal)
        try:
            resolved = self.registry.get(profile)
        except UnknownSubagentProfile:
            output = None
        else:
            try:
                output = (
                    resolved.output_model.model_validate_json(terminal.text)
                    if terminal.status == "done"
                    else None
                )
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
        profiles: dict[UUID, str] = {}
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

    async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus:
        """Queue a follow-up for a background child by admitting the next turn on the child's own
        conversation with `text` as its inbound. The child's partition serializes it after the turn
        in flight (create-or-attach hands it the same sandbox), and the engine loads the child's
        accumulated transcript as prior context — so the follow-up continues the subagent under its
        own profile rather than starting fresh. Admission is idempotent through
        `turn.idempotency_key`: a re-run of the messaging tool step (crash recovery) finds the turn
        it already admitted under `dedup_key` instead of admitting a second one at the next seq.
        Returns the follow-up's status; refuses a turn id that is not a child of this parent,
        mirroring cancel."""
        await self._require_child(turn_id)
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
                            None if child.result_delivery is None else DELIVERY_PENDING
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

    def _untrusted_output(self, profile: str) -> bool:
        """Trust fails closed: a child whose profile is no longer registered walls as
        untrusted rather than passing its output through as instructions."""
        try:
            return self.registry.get(profile).untrusted_output
        except UnknownSubagentProfile:
            return True

    async def _require_child(self, turn_id: UUID) -> str:
        """The profile of a child this conversation delegated, or a refusal. A child that hands its
        result back wakes a *later* turn, and that turn is the one holding the id the result named —
        gated on the spawning turn alone it could never message or cancel the very child that woke
        it. So a sibling turn of the same conversation qualifies, and the authority check, which is
        what actually walls one member's child off from another's, is unchanged."""
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
        if row is None or not spawned_here or row.on_behalf_of_member_id != self.acting_member_id:
            raise ValueError(f"{turn_id} is not a subagent this conversation spawned")
        return row.subagent_profile

    async def _admit(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        profile: str,
        inbound: str,
        delivers_result: bool = False,
    ) -> bool:
        """Insert the child conversation and its first turn, stamped with the spawning turn's
        traceparent so the child's span joins the parent's trace, and with the spawning turn's own
        sandbox conversation — a subagent runs where the turn that spawned it runs, so the files it
        writes are the ones the parent reads. Inherited here rather than resolved per turn, so a
        grandchild carries the same one. The inserts do nothing on
        conflict, so a deterministic (`dedup_key`) child re-admitted by a recovery re-run of the
        spawning step settles on the rows already there — the first run's child stands, never a
        duplicate."""
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation)
                .values(
                    id=conversation_id,
                    workspace_id=self.parent.workspace_id,
                    agent_id=self.parent.agent_id,
                    surface=SUBAGENT_SURFACE,
                    sandbox_conversation_id=(
                        self.parent.sandbox_conversation_id or self.parent.conversation_id
                    ),
                    queue_key=str(turn_id),
                    member_id=audience_member(self.audience),
                    audience=str(self.audience),
                    title=conversation_name(inbound),
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
                    agent_id=self.parent.agent_id,
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
                raise ValueError("subagent dedup key belongs to another member request")
            if claimed.status != "queued":
                return False
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
        while True:
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).one()
            if row.terminal is not None:
                return TerminalFrame.model_validate(row.terminal)
            await asyncio.sleep(SUBAGENT_POLL_SECONDS)


@dataclass(frozen=True)
class SubagentResult:
    """A finished child's output delivered to the conversation that spawned it. The parent takes it
    as an ordinary arrival — folded into its live turn at the next round boundary, or admitted as
    its next turn once that turn has ended — so a parent never holds a turn open waiting on a
    child. Delivery is keyed on the child turn, so a recovery re-run of the delivering execution
    settles on the arrival already posted rather than a second one, and it carries the same
    schema-validated output a foreground spawn returns: a child that ended any way but `done`, or
    whose final answer does not match its profile's schema, arrives as that failure rather than as
    prose the parent would read as an answer.

    The arrival is posted first and the child stamped `delivered` after, both here and in the sweep
    that finds what this path missed: a crash between the two leaves the child `pending` and the
    next pass re-posts under the same key, which admits nothing. Stamping first would let that same
    crash retire a delivery no parent ever received."""

    invoker: TurnInvoker
    registry: SubagentRegistry

    async def deliver(self, child: Turn) -> None:
        if child.result_delivery != DELIVERY_PENDING or child.parent_turn_id is None:
            return
        if child.subagent_profile is None:
            raise RuntimeError("a delivering child turn carries no subagent profile")
        if child.terminal is None:
            raise RuntimeError("a subagent result is delivered only from a committed terminal")
        async with workspace_tx() as connection:
            parent = (
                await connection.execute(
                    sa.select(tables.turn.c.conversation_id, tables.turn.c.agent_id).where(
                        tables.turn.c.id == child.parent_turn_id,
                        tables.turn.c.workspace_id == child.workspace_id,
                    )
                )
            ).one()
        await self.invoker.invoke(
            parent.conversation_id,
            parent.agent_id,
            self._body(child.subagent_profile, child.id, child.terminal),
            f"{SUBAGENT_RESULT_KEY_PREFIX}{child.id}",
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

    def _body(self, profile: str, child_id: UUID, terminal: TerminalFrame) -> str:
        """The child's answer inside the envelope naming which child answered. A profile no longer
        registered still delivers: the parent learns its child ended and with what, which is the
        whole reason it ended its own turn, and an unresolvable profile walls the payload rather
        than reaching the parent as prose. `RESULT_CLOSE` is escaped inside the payload for the
        same reason the wall escapes its own: content that closes the element holding it continues
        as instructions to the parent."""
        resolved = next((known for known in self.registry.profiles if known.name == profile), None)
        payload, status = self._payload(resolved, terminal)
        if resolved is None or resolved.untrusted_output:
            payload = wall(profile, payload)
        return (
            RESULT_OPEN.format(profile=profile, subagent_id=child_id, status=status)
            + f"\n{payload.replace(RESULT_CLOSE, RESULT_CLOSE_ESCAPE)}\n"
            + RESULT_CLOSE
        )

    def _payload(
        self, resolved: SubagentProfile | None, terminal: TerminalFrame
    ) -> tuple[str, str]:
        if terminal.status != "done":
            diagnostic = ": ".join(
                part
                for part in (terminal.error_class, terminal.error_message or terminal.text)
                if part
            )
            return diagnostic, terminal.status
        if resolved is None:
            return RESULT_UNKNOWN_PROFILE, "invalid"
        model = resolved.output_model
        try:
            return model.model_validate_json(terminal.text).model_dump_json(), "done"
        except ValidationError as error:
            faults = "; ".join(
                f"{'.'.join(str(part) for part in fault['loc']) or model.__name__}: {fault['msg']}"
                for fault in error.errors(include_url=False, include_input=False)
            )
            return RESULT_INVALID.format(faults=faults), "invalid"
