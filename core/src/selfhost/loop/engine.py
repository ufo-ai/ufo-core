"""One turn, top to bottom: mark running, load context, model round, terminal commit."""

import asyncio
import time
from dataclasses import dataclass

import sqlalchemy as sa

from selfhost.accounting import read_turn_cost, record_turn_usage
from selfhost.db import workspace_tx
from selfhost.hub import Hub, Terminal
from selfhost.loop.transcript import Conversation, Transcript
from selfhost.models import Message, ModelClient, ModelRequest, TextDelta
from selfhost.o11y import emit_metric, log, turn_span
from selfhost.schema import tables
from selfhost.schema.records import Agent, TerminalFrame, TerminalStatus, Turn, Usage

MAX_OUTPUT_TOKENS = 16_000
DELTA_FLUSH_BYTES = 2048
DELTA_FLUSH_SECONDS = 0.2
EMPTY_RESPONSE_NUDGE = "Previous model response was empty. Answer now."
TRANSCRIPT_WRITE_ATTEMPTS = 3
TRANSCRIPT_WRITE_RETRY_SECONDS = 0.5
COMMIT_RETRY_INITIAL_SECONDS = 1.0
COMMIT_RETRY_MAX_SECONDS = 30.0


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
    hub: Hub

    async def run(self) -> TerminalFrame:
        with turn_span(self.turn.id, self.turn.conversation_id):
            emit_metric("turn_started_total")
            log("turn.started", turn_id=str(self.turn.id), seq=self.turn.seq)
            usage_events: list[Usage] = []
            try:
                if not await self._mark_running():
                    await self._persist_inbound()
                    return await self._publish_existing_terminal()
                final_messages, answer = await self._model_round(
                    await self._load_messages(), usage_events
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

    async def _model_round(
        self, messages: tuple[Message, ...], usage_events: list[Usage]
    ) -> tuple[tuple[Message, ...], str]:
        answer = await self._stream_once(messages, usage_events)
        if answer.strip():
            return messages, answer
        nudged = (*messages, Message(role="user", content=EMPTY_RESPONSE_NUDGE))
        answer = await self._stream_once(nudged, usage_events)
        if not answer.strip():
            raise RuntimeError("model returned an empty response twice")
        return nudged, answer

    async def _stream_once(
        self, messages: tuple[Message, ...], usage_events: list[Usage]
    ) -> str:
        request = ModelRequest(
            model=self.agent.model,
            system=self.agent.prompt,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        parts: list[str] = []
        buffer: list[str] = []
        pending = 0
        last_flush = time.monotonic()

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
                case Usage():
                    usage_events.append(event)
        await flush()
        if len(usage_events) == seen:
            raise RuntimeError("model stream produced no usage")
        return "".join(parts)

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
