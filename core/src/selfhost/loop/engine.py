"""One turn, top to bottom: mark running, load context, model round, terminal commit."""

import asyncio
import json
import time
from dataclasses import dataclass, replace
from uuid import UUID

import sqlalchemy as sa

from selfhost.accounting import read_turn_cost, record_turn_usage
from selfhost.blob import BlobStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ExtensionContext
from selfhost.hub import Hub, Terminal
from selfhost.loop.compaction import Compaction
from selfhost.loop.transcript import Conversation, Transcript
from selfhost.memory.service import MemoryService, recall_subjects
from selfhost.models.interface import (
    Message,
    ModelClient,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
)
from selfhost.o11y import emit_metric, log, turn_span
from selfhost.sandbox.session import SandboxSession
from selfhost.schema import tables
from selfhost.schema.records import Agent, TerminalFrame, TerminalStatus, Turn, Usage
from selfhost.tools.context import Spawn, ToolContext
from selfhost.tools.registry import ToolRegistry

MAX_OUTPUT_TOKENS = 16_000
MAX_TOOL_ROUNDS = 50
DELTA_FLUSH_BYTES = 2048
DELTA_FLUSH_SECONDS = 0.2
EMPTY_RESPONSE_NUDGE = "Previous model response was empty. Answer now."
TRANSCRIPT_WRITE_ATTEMPTS = 3
TRANSCRIPT_WRITE_RETRY_SECONDS = 0.5
COMMIT_RETRY_INITIAL_SECONDS = 1.0
COMMIT_RETRY_MAX_SECONDS = 30.0
RECALL_LIMIT = 8
RECALL_CONTEXT_PREFIX = "Relevant memory:\n"


def _parse_args(partials: list[str]) -> dict[str, object]:
    joined = "".join(partials)
    return json.loads(joined) if joined.strip() else {}


def _total_usage(usage_events: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(u.input_tokens for u in usage_events),
        output_tokens=sum(u.output_tokens for u in usage_events),
        cache_read_tokens=sum(u.cache_read_tokens for u in usage_events),
        cache_write_tokens=sum(u.cache_write_tokens for u in usage_events),
    )


@dataclass(frozen=True)
class TurnEngine:
    turn: Turn
    agent: Agent
    model: ModelClient
    transcript: Transcript
    compaction: Compaction
    hub: Hub
    sandbox: SandboxSession
    tools: ToolRegistry
    tool_ext: dict[str, ExtensionContext]
    blob: BlobStore
    spawn: Spawn
    memory: MemoryService
    member_id: UUID | None
    artifact_token_secret: str

    async def run(self) -> TerminalFrame:
        with turn_span(self.turn.id, self.turn.conversation_id):
            emit_metric("turn_started_total")
            log("turn.started", turn_id=str(self.turn.id), seq=self.turn.seq)
            usage_events: list[Usage] = []
            context = ToolContext(
                sandbox=self.sandbox,
                blob=self.blob,
                turn=self.turn,
                agent=self.agent,
                spawn=self.spawn,
                memory=self.memory,
                member_id=self.member_id,
                artifact_token_secret=self.artifact_token_secret,
            )
            try:
                if not await self._mark_running():
                    await self._persist_inbound()
                    return await self._publish_existing_terminal()
                recalled = await self._recalled_context()
                system = (
                    self.agent.prompt
                    if not recalled
                    else f"{self.agent.prompt}\n\n{RECALL_CONTEXT_PREFIX}{recalled}"
                )
                final_messages, answer = await self._model_round(
                    context, await self._load_messages(), usage_events, system
                )
                frame = await self._commit("done", usage_events, answer=answer)
                if frame.status == "done":
                    await self._persist_transcript(final_messages, answer)
                else:
                    await self._persist_inbound()
                return frame
            except asyncio.CancelledError:
                await self._bill_cancelled(usage_events)
                await self._persist_inbound()
                raise
            except Exception as error:
                await self._commit("failed", usage_events, error_class=type(error).__name__)
                await self._persist_inbound()
                raise

    async def _mark_running(self) -> bool:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(status="running", updated_at=sa.func.now())
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(("queued", "running")),
                )
            )
        return updated.rowcount == 1

    async def _load_messages(self) -> tuple[Message, ...]:
        return (*await self._prior_messages(), Message(role="user", content=self.turn.inbound))

    async def _prior_messages(self) -> tuple[Message, ...]:
        """The conversation before this turn; self-exclusion keeps a replay from reading its own
        write (seq >= this turn's) back as prior context."""
        stored = await self.transcript.read()
        if stored is None or stored.seq >= self.turn.seq:
            return ()
        return stored.messages

    async def _recalled_context(self) -> str:
        """Memory relevant to the inbound, rendered for the turn's system prompt — recomputed each
        turn and never written into the transcript, scoped to the turn's member and shared subjects.
        It rides the system (not a leading user message) so it neither breaks role alternation nor
        pollutes the durable conversation. Best-effort: a recall failure yields no context and never
        fails the turn — the live leg never fails the turn."""
        try:
            recalled = await self.memory.recall(
                self.turn.inbound, recall_subjects(self.member_id), RECALL_LIMIT
            )
        except Exception as error:
            log("recall.failed", turn_id=str(self.turn.id), error_class=type(error).__name__)
            return ""
        return "\n".join(f"- {item.body}" for item in recalled)

    async def _model_round(
        self,
        context: ToolContext,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
    ) -> tuple[tuple[Message, ...], str]:
        """Call the model until it answers with text and no tool calls; each tool-calling round
        dispatches the calls in the sandbox and feeds the results back as the next user turn."""
        nudged = False
        for _round in range(MAX_TOOL_ROUNDS):
            messages, compaction_usage = await self.compaction.maybe_compact(messages)
            usage_events.extend(compaction_usage)
            text, tool_calls = await self._stream_once(messages, usage_events, system)
            if not tool_calls:
                if text.strip():
                    return messages, text
                if nudged:
                    raise RuntimeError("model returned an empty response twice")
                nudged = True
                messages = (*messages, Message(role="user", content=EMPTY_RESPONSE_NUDGE))
                continue
            assistant_blocks = (*((TextBlock(text=text),) if text else ()), *tool_calls)
            results = tuple([await self._dispatch(context, call) for call in tool_calls])
            messages = (
                *messages,
                Message(role="assistant", content=assistant_blocks),
                Message(role="user", content=results),
            )
        raise RuntimeError(f"tool round limit exceeded ({MAX_TOOL_ROUNDS})")

    async def _stream_once(
        self, messages: tuple[Message, ...], usage_events: list[Usage], system: str
    ) -> tuple[str, tuple[ToolUseBlock, ...]]:
        request = ModelRequest(
            model=self.agent.model,
            system=system,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            tools=self.tools.schemas(),
        )
        parts: list[str] = []
        buffer: list[str] = []
        pending = 0
        last_flush = time.monotonic()
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []

        async def flush() -> None:
            nonlocal pending, last_flush
            if buffer:
                await self.hub.publish(self.turn.id, TextDelta(text="".join(buffer)))
                buffer.clear()
                pending = 0
            last_flush = time.monotonic()

        seen = len(usage_events)
        async for event in self.model.complete(request):
            match event:
                case TextDelta(text=chunk):
                    parts.append(chunk)
                    buffer.append(chunk)
                    pending += len(chunk)
                    if pending >= DELTA_FLUSH_BYTES or (
                        pending and time.monotonic() - last_flush >= DELTA_FLUSH_SECONDS
                    ):
                        await flush()
                case ToolCallStart(id=call_id, name=name):
                    call_names[call_id] = name
                    call_json[call_id] = []
                    call_order.append(call_id)
                case ToolCallDelta(id=call_id, partial_json=partial):
                    call_json[call_id].append(partial)
                case Usage():
                    usage_events.append(event)
        await flush()
        if len(usage_events) == seen:
            raise RuntimeError("model stream produced no usage")
        tool_calls = tuple(
            ToolUseBlock(
                id=call_id, name=call_names[call_id], input=_parse_args(call_json[call_id])
            )
            for call_id in call_order
        )
        return "".join(parts), tool_calls

    async def _dispatch(self, context: ToolContext, call: ToolUseBlock) -> ToolResultBlock:
        """Run one tool call in the sandbox; a bad name, bad arguments, or a raising handler
        become an is_error result the model can recover from, never a turn failure. An extension
        tool is handed its owning ExtensionContext; a builtin has no entry and runs ext=None."""
        try:
            tool = self.tools.get(call.name)
            args = tool.input_model.model_validate(call.input)
            result = await tool.handler(replace(context, ext=self.tool_ext.get(call.name)), args)
        except Exception as error:
            return ToolResultBlock(
                tool_use_id=call.id, content=f"{type(error).__name__}: {error}", is_error=True
            )
        return ToolResultBlock(
            tool_use_id=call.id,
            content="".join(block.text for block in result.content),
            is_error=result.is_error,
        )

    async def _commit(
        self,
        status: TerminalStatus,
        usage_events: list[Usage],
        answer: str = "",
        error_class: str | None = None,
    ) -> TerminalFrame:
        """Retries until the terminal state is durable: a client's wait always ends,
        so a database outage delays the commit rather than losing it."""
        delay = COMMIT_RETRY_INITIAL_SECONDS
        while True:
            try:
                frame = await self._commit_once(status, usage_events, answer, error_class)
                break
            except Exception as error:
                log(
                    "turn.commit_retry",
                    turn_id=str(self.turn.id),
                    error_class=type(error).__name__,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, COMMIT_RETRY_MAX_SECONDS)
        await self._publish(Terminal(frame=frame))
        emit_metric("turn_terminal_total", status=frame.status)
        log("turn.terminal", turn_id=str(self.turn.id), status=frame.status)
        return frame

    async def _commit_once(
        self,
        status: TerminalStatus,
        usage_events: list[Usage],
        answer: str,
        error_class: str | None,
    ) -> TerminalFrame:
        usage = _total_usage(usage_events)
        async with workspace_tx() as connection:
            await record_turn_usage(
                connection, self.turn.workspace_id, self.turn.id, self.agent.model, usage
            )
            cost = await read_turn_cost(connection, self.turn.id)
            tokens, micro_usd, model = cost if cost is not None else (0, 0, "")
            frame = TerminalFrame(
                status=status,
                text=answer,
                error_class=error_class,
                tokens=tokens,
                cost_micro_usd=micro_usd,
                model=model,
            )
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(
                    status=status,
                    terminal=frame.model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(("queued", "running")),
                )
            )
            if updated.rowcount == 0:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == self.turn.id)
                    )
                ).one()
                frame = TerminalFrame.model_validate(row.terminal)
        return frame

    async def _publish(self, frame: Terminal) -> None:
        """The live leg never fails the turn; the durable terminal is authoritative."""
        try:
            await self.hub.publish(self.turn.id, frame)
        except Exception as error:
            log(
                "hub.publish_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _bill_cancelled(self, usage_events: list[Usage]) -> None:
        """Best-effort: cancellation must not stall on billing, but consumed tokens count."""
        usage = _total_usage(usage_events)
        try:
            async with workspace_tx() as connection:
                await record_turn_usage(
                    connection, self.turn.workspace_id, self.turn.id, self.agent.model, usage
                )
        except Exception as error:
            log(
                "turn.cancel_billing_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _publish_existing_terminal(self) -> TerminalFrame:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == self.turn.id)
                )
            ).one()
        frame = TerminalFrame.model_validate(row.terminal)
        await self._publish(Terminal(frame=frame))
        return frame

    async def _persist_transcript(self, messages: tuple[Message, ...], answer: str) -> None:
        await self._write_conversation((*messages, Message(role="assistant", content=answer)))

    async def _persist_inbound(self) -> None:
        """Preserve the user's message on a non-done terminal so the next turn still sees it; the
        assistant's error or partial text is never persisted, and the monotonic guard lets a
        done turn's fuller transcript win over this at the same seq."""
        await self._write_conversation(
            (*await self._prior_messages(), Message(role="user", content=self.turn.inbound))
        )

    async def _write_conversation(self, messages: tuple[Message, ...]) -> None:
        conversation = Conversation(seq=self.turn.seq, messages=messages)
        for attempt in range(TRANSCRIPT_WRITE_ATTEMPTS):
            try:
                await self.transcript.write(conversation)
                return
            except Exception as error:
                log(
                    "transcript.write_failed",
                    turn_id=str(self.turn.id),
                    attempt=attempt + 1,
                    error_class=type(error).__name__,
                )
                await asyncio.sleep(TRANSCRIPT_WRITE_RETRY_SECONDS)
