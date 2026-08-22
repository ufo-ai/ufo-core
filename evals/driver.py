"""The live workspace driver: what the operator runner hands the harness so a case runs as a real
turn against a running `ufoctl serve`. It fills the two steps the scoped ExtensionContext cannot —
opening a fresh conversation per case and awaiting an admitted turn's terminal transcript — by
reaching the workspace's own rows and blob store, and it builds the ExtensionContext bound to the
shared admission invoker (the same producer every surface and job admits through). Enqueuing needs a
running serve to drain the turn queue; the driver awaits a queued or running durable workflow, then
reads the terminal row and its exact-sequence transcript. A wait that reaches its deadline cancels
the turn — cancelled terminal committed, DBOS workflow durably cancelled — so the runner never
advances over a still-running predecessor."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, WorkflowHandleAsync
from dbos import error as dbos_error
from sqlalchemy.ext.asyncio import AsyncConnection

from evals.harness.capability import UndeliveredRound, WorkspaceFile
from evals.harness.timing import TurnStep
from ufo.blob import BlobNotFound, BlobStore
from ufo.db import workspace_tx
from ufo.ext.context import Trajectory
from ufo.kinds.governance import prompt_digest
from ufo.loop.engine import DispatchResult, StreamResult
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
from ufo.object_name import validate_object_name
from ufo.schema import tables
from ufo.schema.records import PENDING, ReasoningEffort, TurnContext, Usage
from ufo.sdk.models import (
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.turns.cancellation import cancel_one_turn
from ufo.turns.transcript import Conversation, TranscriptDecodeError, decode, encode, transcript_key
from ufo.workspace import ws

EVAL_SURFACE = "eval"
CANDIDATE_AGENT_NAME = "candidate-{proposal_id}"
POLL_INTERVAL_SECONDS = 1.0
WORKFLOW_WAIT_SECONDS = 300.0
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})
WORKFLOW_STATUSES = frozenset({"queued", "running"})
FAILED_WORKFLOW_STATUSES = frozenset({"ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED", "CANCELLED"})


async def resolve_workspace_and_agent(
    agent_name: str, workspace_id: UUID | None = None
) -> tuple[UUID, UUID, str, str, ReasoningEffort]:
    """Resolve the named target agent in an explicit workspace or the dedicated workspace."""
    if workspace_id is None:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                    ).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == agent_name,
                    )
                )
            ).one()
    return workspace_id, agent.id, agent.prompt, agent.model, agent.reasoning


async def seed_candidate_agent(
    proposal_id: UUID, workspace_id: UUID | None = None
) -> tuple[UUID, str]:
    """Seed a disposable scratch agent carrying a pending proposal's candidate prompt and its base
    agent's model and reasoning effort, and return the workspace and the scratch agent's name — the
    arm the harness runs to measure a self-improvement proposal's cross-suite impact. The candidate
    varies only the prompt body; model, reasoning effort, workspace, and the suites stay fixed
    against the baseline run, so the before/after diff isolates the proposal. Upsert by name: a
    re-run against the same proposal reseeds one stable `candidate-<proposal>` agent rather than
    accreting rows."""
    if workspace_id is None:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    name = CANDIDATE_AGENT_NAME.format(proposal_id=proposal_id)
    validate_object_name(name)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            proposal = (
                await connection.execute(
                    sa.select(
                        tables.proposal.c.agent_id,
                        tables.proposal.c.body,
                        tables.proposal.c.status,
                    ).where(
                        tables.proposal.c.workspace_id == workspace_id,
                        tables.proposal.c.id == proposal_id,
                    )
                )
            ).one_or_none()
            if proposal is None:
                raise ValueError(f"no proposal {proposal_id} in workspace {workspace_id}")
            if proposal.status != PENDING:
                raise ValueError(f"proposal {proposal_id} is {proposal.status}, not pending")
            prompt = proposal.body.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise ValueError(f"proposal {proposal_id} carries no prompt body")
            base = (
                await connection.execute(
                    sa.select(tables.agent.c.model, tables.agent.c.reasoning).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.id == proposal.agent_id,
                    )
                )
            ).one()
            existing = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == name,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name=name,
                        prompt=prompt,
                        model=base.model,
                        reasoning=base.reasoning,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                await connection.execute(
                    sa.update(tables.agent)
                    .values(
                        prompt=prompt,
                        model=base.model,
                        reasoning=base.reasoning,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.agent.c.id == existing)
                )
    return workspace_id, name


@dataclass(frozen=True)
class WorkspaceDriver:
    workspace_id: UUID
    agent_id: UUID
    agent_prompt: str
    blob: BlobStore
    dbos: DBOSClient
    workspace_root: Path
    agent_model: str = "eval"
    pricing: Pricing = CORE_PRICING
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    workflow_wait_seconds: float = WORKFLOW_WAIT_SECONDS

    async def open(
        self,
        case_name: str,
        member_key: str | None = None,
        workspace_files: tuple[WorkspaceFile, ...] = (),
        prior_messages: tuple[str, ...] = (),
        undelivered: tuple[UndeliveredRound, ...] = (),
        shared: bool = False,
    ) -> UUID:
        """Open one isolated eval conversation, bound to the member who speaks in it. A case names
        its member by the exact workspace `member.email`; one that names none speaks as the
        workspace's founding admin, so a case reads to the runtime as the member message it is
        written as rather than as a background fire.

        `shared` leaves the conversation unowned, which is what a shared room is: the
        `conversation_audience_member` check holds `member_id` not-null exactly when the audience is
        that member's private subject, so a conversation cannot be both owned and shared. Ownership
        is not authorship — the member who speaks rides `turn.speaker_member_id`, which `admit`
        carries, so a shared-audience case still speaks as its asker.

        An absent email fails rather than degrading to
        shared-only recall. Seeded undelivered rounds land as the narration-plus-tool-call pairs
        they were, so the case message reads to the model as a member writing into a turn already
        at work."""
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            member_id = await self._speaker(connection, member_key)
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}:{case_name}:{conversation_id}",
                    member_id=None if shared else member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            if prior_messages:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        agent_id=self.agent_id,
                        seq=1,
                        status="done",
                        inbound=prior_messages[0],
                        terminal={"status": "done", "text": "Done.", "model": self.agent_model},
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        if prior_messages:
            messages = tuple(
                Message(role="user" if index % 2 == 0 else "assistant", content=content)
                for index, content in enumerate(prior_messages)
            )
            for index, round in enumerate(undelivered):
                call_id = f"undelivered-{index}"
                messages = (
                    *messages,
                    Message(
                        role="assistant",
                        content=(
                            TextBlock(text=round.narration),
                            ToolUseBlock(id=call_id, name=round.tool, input=round.input),
                        ),
                    ),
                    Message(
                        role="user",
                        content=(ToolResultBlock(tool_use_id=call_id, content=round.result),),
                    ),
                )
            await self.blob.put(
                transcript_key(conversation_id), encode(Conversation(seq=1, messages=messages))
            )
        for item in workspace_files:
            target = self.workspace_path(conversation_id, item.path)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, item.content)
        return conversation_id

    async def steps(self, turn_id: UUID) -> tuple[TurnStep, ...]:
        """The turn's durable engine steps, in the order the workflow recorded them. A tool call's
        step carries the tool-use id its result was memoized under, which is what names it.

        A round's cost is reported only as far as the record supports it, never inferred and never
        raised over. A terminal with no model — the frame a cancel writes for a turn the harness
        stopped waiting on — prices nothing, so those rounds carry their tokens and no cost. The
        rounds are also only part of what a turn spends: a compaction rides its own step, the
        browser `find` ranking meters onto a dispatch, and a resumed attempt bills on top of a step
        log that starts empty, so the terminal's residual cost lands on the last round only when the
        rounds account for every token the terminal counts. A completed model or tool step also
        carries the message its memoized output rebuilds, so an eval timeout retains the trajectory
        that finished before cancellation."""
        recorded = await self.dbos.list_workflow_steps_async(str(turn_id))
        stream_usage: dict[int, Usage] = {}
        for index, step in enumerate(recorded):
            output = step.get("output")
            if not isinstance(output, StreamResult):
                continue
            stream_usage[index] = Usage(
                input_tokens=sum(item.input_tokens for item in output.usages),
                output_tokens=sum(item.output_tokens for item in output.usages),
                cache_read_tokens=sum(item.cache_read_tokens for item in output.usages),
                cache_write_5m_tokens=sum(item.cache_write_5m_tokens for item in output.usages),
                cache_write_30m_tokens=sum(item.cache_write_30m_tokens for item in output.usages),
                cache_write_1h_tokens=sum(item.cache_write_1h_tokens for item in output.usages),
            )
        resources: dict[int, tuple[int, int | None]] = {}
        if stream_usage:
            async with workspace_tx() as connection:
                terminal = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one()
            model = terminal.get("model", "") if terminal else ""
            terminal_tokens = terminal.get("tokens", 0) if terminal else 0
            terminal_cost = terminal.get("cost_micro_usd", 0) if terminal else 0
            costs: dict[int, int] = {}
            for index, usage in stream_usage.items():
                tokens = sum(
                    (
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.cache_read_tokens,
                        usage.cache_write_5m_tokens,
                        usage.cache_write_30m_tokens,
                        usage.cache_write_1h_tokens,
                    )
                )
                if model:
                    costs[index] = self.pricing.micro_usd(model, usage) if tokens else 0
                resources[index] = (tokens, costs.get(index))
            if costs and sum(tokens for tokens, _cost in resources.values()) == terminal_tokens:
                last = next(reversed(costs))
                costs[last] += terminal_cost - sum(costs.values())
                if costs[last] < 0:
                    raise RuntimeError(f"turn {turn_id} step cost exceeds its terminal")
                resources[last] = (resources[last][0], costs[last])
        steps: list[TurnStep] = []
        for index, step in enumerate(recorded):
            messages: tuple[Message, ...] = ()
            match step.get("output"):
                case DispatchResult() as dispatched:
                    call_id = dispatched.tool_use_id
                    call_ids: tuple[str, ...] = ()
                    step_tokens: int | None = None
                    step_cost_micro_usd: int | None = None
                    if dispatched.image_refs:
                        images: list[ImageBlock] = []
                        for ref in dispatched.image_refs:
                            images.append(
                                ImageBlock(
                                    source=ImageSource(
                                        media_type=ref.media_type,
                                        data=(await self.blob.get(ref.blob_key)).decode(),
                                    )
                                )
                            )
                        content: str | tuple[TextBlock | ImageBlock, ...] = (
                            *((TextBlock(text=dispatched.text),) if dispatched.text else ()),
                            *images,
                        )
                    else:
                        content = dispatched.text
                    messages = (
                        Message(
                            role="user",
                            content=(
                                ToolResultBlock(
                                    tool_use_id=dispatched.tool_use_id,
                                    content=content,
                                    is_error=dispatched.is_error,
                                    activity=dispatched.activity,
                                ),
                            ),
                        ),
                    )
                case StreamResult() as streamed:
                    step_tokens, step_cost_micro_usd = resources[index]
                    call_id = ""
                    call_ids = tuple(call.id for call in streamed.tool_calls)
                    blocks = (
                        *streamed.reasoning,
                        *((TextBlock(text=streamed.text),) if streamed.text else ()),
                        *streamed.tool_calls,
                    )
                    if blocks:
                        messages = (Message(role="assistant", content=blocks),)
                    elif streamed.partial_output:
                        messages = (Message(role="assistant", content=streamed.partial_output),)
                case _:
                    call_id = ""
                    call_ids = ()
                    step_tokens = step_cost_micro_usd = None
            steps.append(
                TurnStep(
                    function_name=step["function_name"],
                    started_at_epoch_ms=step.get("started_at_epoch_ms"),
                    completed_at_epoch_ms=step.get("completed_at_epoch_ms"),
                    call_id=call_id,
                    call_ids=call_ids,
                    tokens=step_tokens,
                    cost_micro_usd=step_cost_micro_usd,
                    messages=messages,
                )
            )
        return tuple(steps)

    async def _speaker(self, connection: AsyncConnection, member_key: str | None) -> UUID | None:
        """The member a conversation speaks as: the one a case names — fail loud on an email this
        workspace does not carry — else the workspace's founding admin, the member `ufoctl init`
        seats.

        A workspace with no admin at all speaks unbound. Only a materialized corpus builds one:
        `memory_100` inserts exactly the members its audience bindings name, and its shared-audience
        cases carry no email on purpose, since a speaker there would union that member's private
        subject into what the case may recall and change what the grader sees. Choosing an arbitrary
        member for them would corrupt the grade; refusing would abort the run. So the corpus keeps
        the shared reading it was built for, and every ordinary workspace gets its owner."""
        selection = sa.select(tables.member.c.id).where(
            tables.member.c.workspace_id == self.workspace_id
        )
        if member_key is not None:
            found = (
                await connection.execute(selection.where(tables.member.c.email == member_key))
            ).scalar_one_or_none()
            if found is None:
                raise ValueError(
                    f"eval member_key {member_key!r} is not a member email in this workspace"
                )
            return found
        return (
            await connection.execute(
                selection.where(tables.member.c.is_admin.is_(True))
                .order_by(tables.member.c.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _owner(self, connection: AsyncConnection, conversation_id: UUID) -> UUID | None:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID:
        """Admit one case message as the member who speaks it, through the same `MemberAdmission`
        every surface admits through — so a case exercises the member path it is written as, seat
        gate and mid-turn folding included.

        `speaker_key` is that member's email, carried by the case rather than read off the
        conversation: a shared room is unowned (`member_id` is null) yet still has someone talking
        in it, so authorship cannot be derived from ownership. Absent one, the conversation's own
        member speaks, which is the private-conversation case."""
        async with workspace_tx() as connection:
            speaker = await (
                self._speaker(connection, speaker_key)
                if speaker_key is not None
                else self._owner(connection, conversation_id)
            )
            sender = (
                None
                if speaker is None
                else (
                    await connection.execute(
                        sa.select(tables.member.c.email).where(tables.member.c.id == speaker)
                    )
                ).scalar_one()
            )
        admitter = MemberAdmission(
            admission=Admission(dbos=self.dbos, durable_surfaces=frozenset()),
            workspace_id=self.workspace_id,
        )
        admitted = await admitter.admit(
            conversation_id,
            message,
            idempotency_key,
            context=None if sender is None else TurnContext(sender=sender),
            speaker_member_id=speaker,
        )
        return admitted.turn_id

    async def stage(self, conversation_id: UUID, path: str, source: Path) -> None:
        target = self.workspace_path(conversation_id, path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, source, target)

    def workspace_path(self, conversation_id: UUID, rel: str) -> Path:
        """The host location of a conversation workspace file — the directory serve's local
        carrier serves `/workspace` from, which the eval process shares a filesystem with."""
        return self.workspace_root / str(conversation_id) / rel

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            return await self._trajectory(conversation_id, row.seq)
        if row.status not in WORKFLOW_STATUSES:
            return None
        try:
            async with asyncio.timeout(self.workflow_wait_seconds):
                handle: WorkflowHandleAsync[object]
                while True:
                    try:
                        handle = await self.dbos.retrieve_workflow_async(str(turn_id))
                        break
                    except dbos_error.DBOSNonExistentWorkflowError:
                        async with workspace_tx() as connection:
                            row = (
                                await connection.execute(
                                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                                        tables.turn.c.id == turn_id
                                    )
                                )
                            ).one_or_none()
                        if row is None:
                            return None
                        if row.status in TERMINAL_STATUSES:
                            return await self._trajectory(conversation_id, row.seq)
                        if row.status not in WORKFLOW_STATUSES:
                            return None
                        if row.status == "running":
                            raise
                        await asyncio.sleep(self.poll_interval_seconds)
                try:
                    await handle.get_result(polling_interval_sec=self.poll_interval_seconds)
                except Exception:
                    workflow = await handle.get_status()
                    if workflow.status not in FAILED_WORKFLOW_STATUSES:
                        raise
        except TimeoutError:
            return await self._cancel_overdue(conversation_id, turn_id)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None or row.status not in TERMINAL_STATUSES:
            return None
        return await self._trajectory(conversation_id, row.seq)

    async def cancel(self, turn_id: UUID) -> bool:
        """Terminalize a live turn through the shared `cancel_one_turn` primitive: cancel its DBOS
        workflow — dequeuing a queued run, preempting a streaming model round — then commit its
        cancelled terminal. Returns False when the turn already reached its own terminal (the
        deadline racing its own done commit), which the primitive leaves untouched. The research
        subagents a delegated turn spawned are cancelled by the serve process's cancel reconciler,
        which sweeps any turn left live under a cancelled ancestor; the primitive's
        cancel-before-commit ordering keeps a crash mid-cancel from orphaning this root, which the
        reconciler never re-examines."""
        return await cancel_one_turn(self.dbos, turn_id) is not None

    async def _cancel_overdue(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        """The wait's deadline fired: terminalize the turn before the runner advances. A turn that
        reached its own terminal in the race settles normally."""
        if await self.cancel(turn_id):
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None or row.status not in TERMINAL_STATUSES:
            return None
        return await self._trajectory(conversation_id, row.seq)

    async def _trajectory(self, conversation_id: UUID, turn_seq: int) -> Trajectory | None:
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None
        try:
            conversation = decode(body)
        except TranscriptDecodeError:
            return None
        if conversation.seq != turn_seq:
            return None
        return Trajectory(
            conversation_id=conversation_id,
            agent_id=self.agent_id,
            agent_prompt=self.agent_prompt,
            agent_prompt_digest=prompt_digest(self.agent_prompt),
            messages=conversation.messages,
        )
