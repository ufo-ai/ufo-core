"""The conversation's own terminal: what says a conversation has one, and the relay that holds it.

A member who watched an agent write code in a sandbox wants to look at that sandbox themselves, so
a conversation that has done coding work carries a terminal chip. The chip is drawn off the turn
records alone — a conversation whose sandbox has paused, or which never grew one, still shows it,
because the work happened whatever state the container is in now. Opening it is what provisions:
the socket resumes the sandbox and runs a PTY in `/workspace`, and holding the socket open is what
keeps the container awake under it.
"""

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ufo.sdk.http import WebSocket, WebSocketDisconnect
from ufo.sdk.models import Message, ToolUseBlock
from ufo.sdk.o11y import log
from ufo.sdk.sandbox import (
    WORKSPACE_DIR,
    SandboxUnreachable,
    ShellSession,
    ShellSize,
    ShellUnsupported,
)
from ufo.sdk.surfaces import SurfaceContext, Turn

CODING_PROFILE_NAMES = frozenset({"coding", "fable_escalation"})
"""The subagent profiles that run repository work. Named here rather than imported: an extension
answers for its own text, and the web surface reads these off turn rows it did not write."""
CODING_SKILL_NAME = "coding"
SHELL_COLS_MAX = 500
SHELL_ROWS_MAX = 200
"""What a terminal's grid is bounded to. The size comes from a browser that is free to state any
number, and it reaches a PTY's ioctl."""
SHELL_RENEW_SECONDS = 60
"""How often an open socket renews the sandbox lease. A carrier that leases its containers pauses
one that has gone quiet, and a member reading their build output types nothing for minutes, so the
connection itself — not their keystrokes — is what says someone is still there."""
SHELL_PRESENCE_SECONDS = 180
"""How long the last frame from the viewer holds the container awake. A tab the member left open
behind another window keeps its socket, so the socket alone cannot say anyone is watching: the
terminal beats while its tab is visible, and a lease is renewed only for a viewer heard from inside
this span."""
SHELL_INPUT_MAX_BYTES = 64 * 1024
"""The largest keystroke frame accepted. Input is a member's typing and a paste; nothing a
terminal sends legitimately approaches this, and without a bound one frame sizes the process."""
SHELL_CLOSE_FAULT = 1011
SHELL_UNREACHABLE_MESSAGE = "This conversation's sandbox is not reachable."
SHELL_UNSUPPORTED_MESSAGE = "This deploy's sandbox runs no interactive shell."


class ShellSizePayload(BaseModel):
    """A control frame off the wire: the grid the viewer's terminal measured. Every text frame the
    browser sends is one of these — the first states the size the shell starts at, and each later
    one resizes it — while keystrokes travel as binary, so nothing a member types can be read as
    control."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cols: int = Field(ge=1, le=SHELL_COLS_MAX)
    rows: int = Field(ge=1, le=SHELL_ROWS_MAX)


@dataclass
class _Presence:
    """When the viewer was last heard from, on the monotonic clock."""

    at: float


def coding_intent(turns: Iterable[Turn], messages: Iterable[Message]) -> bool:
    """Whether this conversation has done coding work: a turn ran one of the coding profiles, or a
    round loaded the coding skill. Read off records the conversation already holds, so the answer
    survives a paused sandbox, a reclaimed one, and a conversation that never opened one."""
    if any(turn.subagent_profile in CODING_PROFILE_NAMES for turn in turns):
        return True
    return any(
        block.name == "load_skill" and block.input.get("name") == CODING_SKILL_NAME
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolUseBlock)
    )


async def shell_state(ctx: SurfaceContext, conversation_id: UUID) -> dict[str, object]:
    """What the chip draws itself from: whether this conversation has a terminal at all, and
    whether its sandbox is up right now. Neither read provisions anything — the transcript and the
    spawned turns are records, and the sandbox question is the stored handle alone — so a member
    opening a conversation never wakes a container by being looked at."""
    spawned, recorded, bound = await asyncio.gather(
        ctx.conversation_subagent_turns(conversation_id),
        ctx.read_transcript(conversation_id),
        ctx.conversation_sandbox_bound(conversation_id),
    )
    available = coding_intent(spawned, () if recorded is None else recorded.messages)
    return {"available": available, "active": bound}


@dataclass(frozen=True)
class ShellRelay:
    """One member's terminal on one conversation's sandbox, from the accepted handshake to the
    close.

    The socket is accepted before the sandbox is touched, because resuming a paused container takes
    seconds a browser should spend drawing a terminal rather than waiting on a handshake with
    nothing to show. Bytes cross raw in both directions: output is whatever the PTY wrote, and
    input is whatever the member typed, so an escape sequence, a paste and a control character all
    survive the trip.
    """

    ctx: SurfaceContext
    websocket: WebSocket
    conversation_id: UUID
    presence: _Presence = field(default_factory=lambda: _Presence(monotonic()))

    async def run(self) -> None:
        await self.websocket.accept()
        size = await self._opening_size()
        if size is None:
            return
        try:
            async with self.ctx.conversation_shell(
                self.conversation_id, WORKSPACE_DIR, size, self._to_viewer
            ) as shell:
                await self._pump(shell)
        except SandboxUnreachable:
            await self._end(SHELL_UNREACHABLE_MESSAGE)
        except ShellUnsupported:
            await self._end(SHELL_UNSUPPORTED_MESSAGE)

    async def _opening_size(self) -> ShellSize | None:
        """The first control frame, which states the grid the shell is born at. A viewer that
        disconnects or opens with anything else gets no sandbox work done on its behalf."""
        try:
            message = await self.websocket.receive()
        except WebSocketDisconnect:
            return None
        if message["type"] == "websocket.disconnect":
            return None
        stated = self._control(message.get("text"))
        if stated is None:
            await self._end("A terminal opens with its size.")
            return None
        return ShellSize(cols=stated.cols, rows=stated.rows)

    def _control(self, text: str | None) -> ShellSizePayload | None:
        if text is None:
            return None
        try:
            return ShellSizePayload.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError):
            return None

    async def _pump(self, shell: ShellSession) -> None:
        """The three things that can end a terminal, raced: the member closing the tab, the shell
        exiting, and the connection failing under the renewal that holds the sandbox awake.
        Whichever lands first cancels the others, exactly as the site relay ends its own pair."""
        work = (
            asyncio.create_task(self._from_viewer(shell)),
            asyncio.create_task(shell.wait()),
            asyncio.create_task(self._renew(shell)),
        )
        done, pending = await asyncio.wait(work, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            failure = task.exception()
            if failure is not None:
                raise failure

    async def _from_viewer(self, shell: ShellSession) -> None:
        while True:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            self.presence.at = monotonic()
            text = message.get("text")
            if text is not None:
                stated = self._control(text)
                if stated is not None:
                    await shell.resize(ShellSize(cols=stated.cols, rows=stated.rows))
                continue
            typed = message.get("bytes") or b""
            if len(typed) > SHELL_INPUT_MAX_BYTES:
                await self._end("That paste is too large for the terminal.")
                return
            await shell.send(typed)

    async def _to_viewer(self, data: bytes) -> None:
        await self.websocket.send_bytes(data)

    async def _renew(self, shell: ShellSession) -> None:
        """Hold the container awake for as long as someone is watching this terminal. The viewer
        beats while its tab is visible and stops when it is hidden, so a shell left open in a
        background tab lets the sandbox pause on the carrier's own clock."""
        while True:
            await asyncio.sleep(SHELL_RENEW_SECONDS)
            if monotonic() - self.presence.at <= SHELL_PRESENCE_SECONDS:
                await shell.renew()

    async def _end(self, reason: str) -> None:
        """Close with the sentence the terminal prints. A member who asked for a shell and got none
        reads why in the terminal itself, which is the only surface this connection has."""
        log("web.shell.ended", conversation_id=str(self.conversation_id), reason=reason)
        await self.websocket.close(code=SHELL_CLOSE_FAULT, reason=reason)
