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


@dataclass(frozen=True)
class TurnEngine:
    turn: Turn
    agent: Agent
    model: ModelClient
    transcript: Transcript
    hub: Hub

    async def run(self) -> TerminalFrame:
        with turn_span(str(self.turn.id), str(self.turn.conversation_id)):
            emit_metric("turn_started_total")
            log("turn.started", turn_id=str(self.turn.id), seq=self.turn.seq)
            usage_events: list[Usage] = []
            try:
                if not await self._mark_running():
                    return await self._publish_existing_terminal()
                messages = await self._load_messages()
                final_messages, answer = await self._model_round(messages, usage_events)
                frame = await self._commit("done", usage_events, answer=answer)
                await self._persist_transcript(final_messages, answer)
                return frame
            except Exception as error:
                await self._commit("failed", usage_events, error_class=type(error).__name__)
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
        stored = await self.transcript.read()
        prior = () if stored is None or stored.seq >= self.turn.seq else stored.messages
        return (*prior, Message(role="user", content=self.turn.inbound))

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
        usage = Usage(
            input_tokens=sum(u.input_tokens for u in usage_events),
            output_tokens=sum(u.output_tokens for u in usage_events),
            cache_read_tokens=sum(u.cache_read_tokens for u in usage_events),
            cache_write_tokens=sum(u.cache_write_tokens for u in usage_events),
        )
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
        await self.hub.publish(self.turn.id, Terminal(frame=frame))
        emit_metric("turn_terminal_total", status=frame.status)
        log("turn.terminal", turn_id=str(self.turn.id), status=frame.status)
        return frame

    async def _publish_existing_terminal(self) -> TerminalFrame:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == self.turn.id)
                )
            ).one()
        frame = TerminalFrame.model_validate(row.terminal)
        await self.hub.publish(self.turn.id, Terminal(frame=frame))
        return frame

    async def _persist_transcript(self, messages: tuple[Message, ...], answer: str) -> None:
        conversation = Conversation(
            seq=self.turn.seq,
            messages=(*messages, Message(role="assistant", content=answer)),
        )
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
