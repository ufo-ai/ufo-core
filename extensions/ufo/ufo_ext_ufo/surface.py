"""The ufo member surface: the server-driven terminal wire the `ufo` shell client renders.

Every screen is decided here and streamed back as tab-separated directive lines the shell can
`read`. The surface is live (it holds the member's connection and tails the turn's hub frames), so
it admits without writeback the way the web surface does, and reaches core only through the
privileged `SurfaceContext` a CI gate pins to `ufo.sdk`.

Auth is a stateless HMAC bearer minted by the gateway extension over `{"ws", "email", "exp"}` —
the `ufo.sdk.bearer` codec, secret in `UFO_TOKEN_SECRET`. The token's email is this surface's
external id — resolved to a member the first
time they speak; a token whose email names no member still gets a conversation, unlinked.

One POST is one held stream. A body is the member's message, admitted onto the durable queue and
tailed as directives up to `HOLD_SECONDS` (just under the shell's `curl --max-time 90`); an answer
that outruns the hold ends the stream with `poll` and the shell reconnects with an empty body, which
admits nothing and resumes tailing the conversation's latest turn.

A turn's own end leaves the member at the prompt, and the conversation can still speak first: a
delivered subagent result or a fired monitor admits a turn no member message founded. So a stream
that ends on its terminal also ends with `since` and `listen`, and the client reconnects empty
every `LISTEN_SECONDS` while it idles, marking each reconnect with `x-ufo-listen`. A marked
reconnect whose `since` names the conversation's latest turn — already terminal, already
rendered — answers `since` and `listen` alone, so idling costs one line; one that finds a newer
turn falls into the resume tail and prints it as it runs. An unmarked empty reconnect is a `poll`
or severed-stream resume whose cursor may stand mid-turn, so it always drains the tail.

A send (`x-ufo-send`) is the one POST that holds nothing: it admits its body, answers with the
`sent` ack, and returns. It is the request a member's second message rides while their first turn
still runs — admission speed rather than the held stream's next boundary — and the consequences
reach them down the stream they are already holding."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from functools import partial
from uuid import UUID

from pydantic import ValidationError

from ufo.sdk.accounting import MICRO_USD_PER_USD
from ufo.sdk.audience import conversation_audience
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.credentials import CredentialValueInvalid
from ufo.sdk.http import PlainTextResponse, Request, Response, StreamingResponse
from ufo.sdk.hub import (
    Absorbed,
    Activity,
    CostTick,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SubagentActivity,
    Terminal,
    TextDelta,
)
from ufo.sdk.models import Message, ToolResultBlock, ToolUseBlock
from ufo.sdk.o11y import log
from ufo.sdk.surfaces import (
    ConnectRequestInvalid,
    Conversation,
    CredentialPrompt,
    CredentialRequestInvalid,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
    TerminalGone,
    TerminalOp,
    TurnContext,
    member_message_text,
)

SURFACE_UFO = "ufo"
SOURCE = "ufo cli"
PROMPT = ">"
RESUMED_NOTE = "the service restarted; this turn resumed"
POLL_SECONDS = 1
LISTEN_SECONDS = 2
MAX_MESSAGE_BYTES = 40_000
MAX_SECRET_BYTES = 4_096
MAX_OP_REPLY_BYTES = 100 * 1024 * 1024
SECRET_HEADER = "x-ufo-secret"
SECRET_SLOT_HEADER = "x-ufo-slot"
CWD_HEADER = "x-ufo-cwd"
OP_HEADER = "x-ufo-op"
STOP_HEADER = "x-ufo-stop"
SEND_HEADER = "x-ufo-send"
SEND_ID_HEADER = "x-ufo-send-id"
UNSEND_HEADER = "x-ufo-unsend"
SINCE_HEADER = "x-ufo-since"
LISTEN_HEADER = "x-ufo-listen"
OP_ERR_HEADER = "x-ufo-op-err"
SCRIPT_HEADER = "x-ufo-script"
TIMEZONE_HEADER = "x-ufo-timezone"
CLIENT_VERSION_ENV = "UFO_CLIENT_VERSION"
QUEUE_KEY_SEPARATOR = ":"
TURN_FAILED_MESSAGE = "The agent could not complete the request. Try again."

# Hold a live stream open just under the shell's `curl --max-time 90`, so a turn that outruns the
# hold ends on `poll` (the shell reconnects) rather than the client's own timeout truncating it.
HOLD_SECONDS = 85.0


def directive(verb: str, *fields: str) -> bytes:
    """One directive line: the verb and its escaped fields joined by tabs, newline-terminated. Tabs,
    newlines, and backslashes in a payload are escaped so a field never breaks the line framing the
    shell splits on; carriage returns are dropped."""
    escaped = [
        field.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "").replace("\n", "\\n")
        for field in fields
    ]
    return ("\t".join([verb, *escaped]) + "\n").encode()


@dataclass(frozen=True)
class SharedFile:
    """One file a turn shared, as the terminal renders it: the download name, its size, and the
    absolute link a member can open — empty when artifact delivery is unconfigured (no token secret
    or no public base URL), which names the file without a link the way Slack degrades."""

    filename: str
    size_bytes: int
    url: str


async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]:
    """Every file the turn shared, in share order, each carrying the absolute download link. The
    link is minted here because only the surface holds the deploy's token secret and public base —
    `share_file` hands the model a host-less path by design."""
    return tuple(
        SharedFile(
            filename=artifact.filename,
            size_bytes=artifact.size_bytes,
            url=ctx.artifact_link(artifact) or "",
        )
        for artifact in await ctx.shared_artifacts(turn_id)
    )


async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None:
    """The `SurfaceSpec.identify` the shared fleet calls to scope a request before its handler runs:
    the workspace the request's bearer claims, or None to reject. The same bearer the handler
    re-verifies for the member email — workspace here, identity there, from the one signature."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return workspace_claim(token.strip())


HISTORY_CHAR_BUDGET = 20_000


def history_directives(conversation: Conversation) -> tuple[bytes, ...]:
    """The conversation so far, rendered for a fresh resume: the member's messages as `you`, the
    agent's replies as `say`. Trailing replies are left off — the tail replays the latest turn's
    frames, and a reply said here too would print twice. The newest messages win the budget.

    A turn's steps stand over the reply they produced, as the one `note` line that counts them:
    every text but the turn's last was written between calls, so it is a step of the work rather
    than a reply of its own, and a round that wrote its text and then dispatched work was cut there
    — its text is a step too and the turn states no words. This is the shape the live turn rolled
    up into, so a resume re-reads what the session showed."""
    active = {
        block.tool_use_id
        for message in conversation.messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.activity
    }
    said: list[tuple[bool, str, int]] = []
    answer = ""
    steps = 0
    for message in conversation.messages:
        text = _history_text(message)
        if message.role == "assistant":
            calls = _dispatched(message, active)
            if text.strip():
                steps += int(bool(answer))
                answer = "" if calls else text
                steps += int(bool(calls))
            steps += calls
            continue
        if not text.strip():
            continue
        if answer or steps:
            said.append((False, answer, steps))
        answer, steps = "", 0
        said.append((True, text, 0))
    if answer or steps:
        said.append((False, answer, steps))
    while said and not said[-1][0]:
        said.pop()
    kept: list[tuple[bool, str, int]] = []
    budget = HISTORY_CHAR_BUDGET
    for member, text, rolled in reversed(said):
        budget -= len(text)
        if budget < 0 and kept:
            break
        kept.append((member, text, rolled))
    kept.reverse()
    lines: list[bytes] = []
    for member, text, rolled in kept:
        if member:
            lines.append(directive("you", text))
            continue
        if rolled:
            # The web's fold states the same words.
            plural = "" if rolled == 1 else "s"
            lines.append(directive("note", f"Completed {rolled} step{plural}"))
        if text:
            lines.append(directive("say", text))
    return tuple(lines)


def _dispatched(message: Message, active: set[str]) -> int:
    """How many calls a round dispatched: a call the model wrote but that never entered dispatch
    narrated nothing live either, so it is no step here."""
    if isinstance(message.content, str):
        return 0
    return sum(
        1 for block in message.content if isinstance(block, ToolUseBlock) and block.id in active
    )


def _history_text(message: Message) -> str:
    if isinstance(message.content, str):
        raw = message.content
    else:
        raw = "\n\n".join(block.text for block in message.content if block.type == "text")
    return member_message_text(raw) if message.role == "user" else raw


def directives_for(
    frame: LiveFrame,
    streamed: bool,
    collect: tuple[CredentialPrompt, ...] = (),
    connect_message: str | None = None,
    files: tuple[SharedFile, ...] = (),
    exits: bool = True,
) -> tuple[bytes, ...]:
    """The directive lines one live frame renders to. Token deltas stream as `txt`; tool-run
    activity and the running cost meter are transient `status` lines; the terminal frame
    caps the turn (`streamed` says the answer already reached the transcript as `txt`, `collect`
    names the credential prompts still awaiting values, `files` the ones it shared). A drain names
    the member arrivals it folded (`absorbed`), which is how a client holding a message it sent
    mid-turn learns the agent has taken that message up. A reply the turn delivered mid-flight says
    itself (`say`): the member has been sent those words, and they never rode the token stream. The
    same frame carries a linked notice when a member comments from the portal. A turn the fleet
    resumed after the process running it died narrates that as a `note`, on the frame — a client's
    notes already carry every other thing the turn is doing, so this one needs no grace to keep it
    clear of the answer."""
    match frame:
        case TextDelta():
            return (directive("txt", frame.text),) if frame.text else ()
        case Activity():
            return (directive("status", frame.text),)
        case SubagentActivity():
            return _subagent_note(frame)
        case CostTick():
            cost = frame.cost_micro_usd / MICRO_USD_PER_USD
            return (directive("status", f"{frame.tokens} tok - ${cost:.6f}"),)
        case Terminal():
            return _answer(frame, streamed, collect, connect_message, files, exits)
        case Parked():
            return (directive("say", frame.message), directive("ask", PROMPT))
        case Absorbed():
            arrivals = tuple(str(arrival) for arrival in frame.arrivals)
            return (directive("absorbed", *arrivals),) if arrivals else ()
        case Resumed():
            return (directive("note", RESUMED_NOTE),)
        case Reply():
            return (directive("say", frame.text),) if frame.text else ()
    raise ValueError(f"unmapped live frame {type(frame).__name__}")


def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]:
    """A run's dispatches narrate as notes under the run's name; its start and terminal are the
    parent's spawn narration and result, so they add no line."""
    label = frame.name or frame.profile
    if frame.activity:
        return (directive("status", f"{label}: {frame.activity}"),)
    return ()


def _answer(
    terminal: Terminal,
    streamed: bool,
    collect: tuple[CredentialPrompt, ...] = (),
    connect_message: str | None = None,
    files: tuple[SharedFile, ...] = (),
    exits: bool = True,
) -> tuple[bytes, ...]:
    """Cap a turn. A done turn prompts (`ask`) after its answer — already streamed as `txt`, else
    said now, followed by one `file` line per file the turn shared and one `secret` line per
    still-unanswered credential prompt, so the shell collects exactly the missing values privately;
    a failure says what to do next and prompts.

    A cancel divides on whether it carries words. An admission refusal cancels with its reason: the
    member reads it and, on the stream whose request admitted the refused turn (`exits`), the
    client session ends (`exit`), the conversation resuming on the next `ufo`. On any other
    stream — an idle listen or a reconnect rendering a turn the member did not found — the reason
    is said and the prompt returns, because ending a session over a background turn's refusal would
    close a terminal nobody touched. A member's own stop carries none — the turn ended because they
    pressed Esc — so the turn is capped and the prompt returns.

    Files render on every terminal status, not only `done`: the upload committed before the turn
    reached its end, so a turn that shared a file and then failed or was cancelled still owes the
    member the link — which is the whole of the silent drop this repairs."""
    frame = terminal.frame
    shared = tuple(
        directive("file", file.filename, str(file.size_bytes), file.url) for file in files
    )
    match frame.status:
        case "done":
            said = () if streamed else _say_lines(frame.text)
            sealed = "" if frame.credential_request is None else frame.credential_request.sealed
            secrets = tuple(
                directive("secret", sealed, prompt.slot, prompt.prompt) for prompt in collect
            )
            connect = () if connect_message is None else (directive("say", connect_message),)
            return (*said, *shared, *secrets, *connect, directive("ask", PROMPT))
        case "failed":
            safe_error = (
                frame.error_message
                if frame.error_class in (CredentialValueInvalid.__name__, TerminalGone.__name__)
                else None
            )
            return (
                *_say_lines(safe_error or TURN_FAILED_MESSAGE),
                *shared,
                directive("ask", PROMPT),
            )
        case "cancelled":
            if frame.text:
                closing = directive("exit", "0") if exits else directive("ask", PROMPT)
                return (*_say_lines(frame.text), *shared, closing)
            return (directive("say", "cancelled"), *shared, directive("ask", PROMPT))
    raise ValueError(f"unmapped terminal status {frame.status!r}")


def _say_lines(text: str) -> tuple[bytes, ...]:
    return tuple(directive("say", line) for line in (text.splitlines() or [text]))


async def stream_directives(
    tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]],
    hold_seconds: float,
    pending: Callable[[str, str], Awaitable[bool]] | None = None,
    connect: Callable[[], Awaitable[str]] | None = None,
    files: Callable[[], Awaitable[tuple[SharedFile, ...]]] | None = None,
    ops: Callable[[], Awaitable[TerminalOp]] | None = None,
    *,
    turn_id: UUID,
    since: str = "",
    moved_on: Callable[[], Awaitable[bool]] | None = None,
    exits: bool = True,
) -> AsyncIterator[bytes]:
    """Render a turn's live frames as directives, holding at most `hold_seconds`. A terminal or
    parked frame closes the stream on its own cap; if the hold elapses first the stream ends with
    `poll` so the shell reconnects to drain the durable answer. `pending` gates each prompt of a
    terminal frame's credential request, so a fulfilled or expired prompt never re-renders on
    reconnect while an unanswered sibling keeps asking. `files` reads what the turn shared, once the
    turn has ended and only then — the rows land during the turn, so reading earlier would report a
    partial set. The tail's scope is entered here because the route returns its response before a
    single frame is read.

    `ops` is the terminal rendezvous: each op the turn asks of the member's machine is raced
    against the turn's own frames, rendered as one `run` directive, and ends the stream — the
    client goes to execute, and its reply is its next request, which resumes this tail. No `poll`
    follows a `run`, because the reply is already the reconnect.

    Every end the client comes back from is preceded by `since`, naming the turn and the cursor of
    the last frame rendered; the client echoes it and the next tail opens after that frame. The
    terminal prints as it reads, so a tail that opened at the ring's first frame would reprint every
    note it already showed — and an op ends the stream, so a turn calling three tools would print
    its first note three times. The cursor is the one the client was given back when this stream
    rendered nothing, so a quiet hold never walks it backwards, and a frame taken off the
    subscription but not rendered — one in flight when the op won the race — is replayed rather than
    lost, because it stands after the cursor this names.

    A stream that ends on its own terminal divides on what that end leaves the member. When
    `moved_on` says the conversation already holds a newer live turn — a stop founded the next turn
    on a message the member had already sent — the terminal is followed by `since` and an immediate
    `poll`, a cursor-carrying resume onto that turn rather than a fresh join that would replay
    history. When the terminal leaves the prompt, it is followed by `since` and `listen`: the
    conversation can be woken without the member — a subagent result delivered, a monitor fired —
    and the reconnects the listen names are how those turns reach a terminal nobody is typing into.
    A cancel that carries words exits the client unless this stream answers a marked idle listen
    (`exits` false): the member did not found the refused turn — a scheduled firing a cap refused,
    say — and ending their idle session over it would close a terminal nobody touched, so the
    reason is said and the prompt returns, listening. A park resumes under the same turn id, so a
    listen that would re-render its notice on every reconnect never follows one; a parked turn's
    resumption reaches the member on their next message."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + hold_seconds
    streamed = False
    terminated = False
    prompting = False
    ran = False
    rendered_cursor = since
    frame_task: asyncio.Task[tuple[str, LiveFrame] | None] | None = None
    op_task: asyncio.Task[TerminalOp] | None = None
    async with tail as frames:
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                if frame_task is None:
                    frame_task = asyncio.ensure_future(_next(frames))
                if ops is not None and op_task is None:
                    op_task = asyncio.ensure_future(ops())
                waiting: set[asyncio.Task[object]] = {frame_task}
                if op_task is not None:
                    waiting.add(op_task)
                done, _ = await asyncio.wait(
                    waiting, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    break
                if op_task is not None and op_task in done:
                    try:
                        op = op_task.result()
                    except TerminalGone:
                        op_task = None
                        ops = None
                        continue
                    op_task = None
                    yield directive("since", str(turn_id), rendered_cursor)
                    yield directive(
                        "run",
                        op.op_id,
                        op.kind,
                        op.name,
                        str(op.timeout_s),
                        op.arg,
                        op.params,
                    )
                    ran = True
                    break
                item = frame_task.result()
                frame_task = None
                if item is None:
                    break
                cursor, frame = item
                collect: tuple[CredentialPrompt, ...] = ()
                if (
                    isinstance(frame, Terminal)
                    and frame.frame.credential_request is not None
                    and pending is not None
                ):
                    request = frame.frame.credential_request
                    collect = tuple(
                        [p for p in request.prompts if await pending(request.sealed, p.slot)]
                    )
                connect_message: str | None = None
                if isinstance(frame, Terminal) and frame.frame.connect_request is not None:
                    if connect is None:
                        connect_message = "Connection request unavailable; ask me to connect again."
                    else:
                        try:
                            url = await connect()
                        except ConnectRequestInvalid:
                            connect_message = (
                                "Connection request unavailable; ask me to connect again."
                            )
                        else:
                            connect_message = f"[Complete the connection]({url})"
                shared: tuple[SharedFile, ...] = ()
                if isinstance(frame, Terminal) and files is not None:
                    shared = await files()
                lines = directives_for(
                    frame, streamed, collect, connect_message, shared, exits=exits
                )
                if lines and isinstance(frame, TextDelta):
                    streamed = True
                for line in lines:
                    yield line
                rendered_cursor = cursor
                if isinstance(frame, Terminal | Parked):
                    terminated = True
                    prompting = isinstance(frame, Terminal) and not (
                        exits and frame.frame.status == "cancelled" and bool(frame.frame.text)
                    )
                    break
        finally:
            for task in (frame_task, op_task):
                if task is not None:
                    task.cancel()
            for task in (frame_task, op_task):
                if task is not None:
                    with suppress(asyncio.CancelledError, TerminalGone):
                        await task
    if not terminated and not ran:
        yield directive("since", str(turn_id), rendered_cursor)
        yield directive("poll", str(POLL_SECONDS))
    elif terminated and moved_on is not None and await moved_on():
        yield directive("since", str(turn_id), rendered_cursor)
        yield directive("poll", "0")
    elif prompting:
        yield directive("since", str(turn_id), rendered_cursor)
        yield directive("listen", str(LISTEN_SECONDS))


async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None:
    """The next frame, or None at end — so the deadline `wait_for` never has to route a
    StopAsyncIteration out through its own task."""
    try:
        return await anext(frames)
    except StopAsyncIteration:
        return None


def _authenticated_email(request: Request, workspace_id: UUID) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return verify_token(token.strip(), workspace_id)


def _utf8_header(request: Request, name: str) -> str:
    """A header the client sent as raw UTF-8 bytes, recovered. HTTP header values are ISO-8859-1 by
    the spec, so the ASGI server decodes each byte to a codepoint; re-encoding latin-1 is total and
    reverses that exactly, and the UTF-8 decode then yields the string the client meant — the
    member's `pwd -P`, which is a path and may hold non-ASCII. Pure ASCII is invariant through both
    steps, so a plain path is untouched."""
    raw = request.headers.get(name, "").strip()
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")


def _stale_client(request: Request) -> bool:
    """Whether this client's x-ufo-script version differs from the client the deploy serves
    (`UFO_CLIENT_VERSION`) — unset in local dev, so no install is ever pushed there. The stale
    client is told to update by an `install` prepended to its next screen; an op reply and a
    secret fulfillment stay pure, so the directive rides only a message or resume stream."""
    served = os.environ.get(CLIENT_VERSION_ENV, "")
    return bool(served) and request.headers.get(SCRIPT_HEADER, "").strip() != served


def _resumed_from(request: Request, turn_id: UUID) -> str:
    """Where the client's last stream got to, from the `since` directive it was given back — the
    turn it names and the cursor within it. The turn is what makes it safe to honour: a client
    holding a cursor for the turn before this one would otherwise start this turn's tail partway
    through and lose the frames before it, so a cursor naming any other turn is dropped and the
    tail opens at the ring's first frame, which is what a turn nothing has printed yet wants."""
    named, _, cursor = request.headers.get(SINCE_HEADER, "").strip().partition(":")
    return cursor if named == str(turn_id) else ""


def _turn_context(email: str, request: Request) -> TurnContext:
    """The admitted turn's ambient context: the member as sender, and the zone their client
    reported so the turn's time reads as the member's own; a reported zone that is not a known
    IANA name is dropped with a log rather than failing the member's message."""
    source = f"{SOURCE} ({email})"
    zone = request.headers.get(TIMEZONE_HEADER, "").strip()
    if not zone:
        return TurnContext(sender=email, source=source)
    try:
        return TurnContext(sender=email, timezone=zone, source=source)
    except ValidationError:
        log("ufo.timezone_dropped", zone=zone)
        return TurnContext(sender=email, source=source)


async def channel(ctx: SurfaceContext, request: Request) -> Response:
    """One held turn on a channel. The bearer names the member; the channel path scopes their
    conversation. A body admits a turn and streams it; an empty body admits nothing and resumes
    tailing the conversation's latest turn (or prompts when it holds none). An empty body carrying
    `x-ufo-listen` whose `since` names that latest turn, found already terminal, is the idle
    listen: the client only sends the header once a `listen` directive armed it, and a `listen` is
    only ever issued after the turn's end was rendered — so the answer is `since` and `listen`
    alone, and the tail is never re-opened on a turn it would only re-say. A `poll` or severed-
    stream reconnect carries no header, so a turn that committed while the client was off the wire
    still drains its durable end through the tail.

    A client standing in a directory names it in `x-ufo-cwd`, and that terminal is the
    conversation's sandbox: the first admitted turn claims the binding on the row and tells the
    member where the agent works, the held stream publishes the terminal for the claim's ops, and
    a reply to an op arrives as the next request under `x-ufo-op` — resolved here, never admitted,
    never in the transcript — which then resumes the same tail.

    A stop (`x-ufo-stop`, the member's Esc) admits nothing either: it ends the conversation's
    running turn and resumes that same tail, which replays to the cancelled terminal the stop
    committed. A press with nothing running resumes the tail unchanged.

    A send (`x-ufo-send`) admits and answers without holding anything — the one request that is not
    a stream."""
    email = _authenticated_email(request, ctx.workspace_id)
    if email is None:
        return PlainTextResponse("unauthorized", status_code=401)
    member_id = await ctx.linked_member(email) or await ctx.link_member(email, email)
    sealed = request.headers.get(SECRET_HEADER)
    if sealed:
        return await _fulfill_secret(ctx, request, member_id, sealed)
    cwd = _utf8_header(request, CWD_HEADER)
    if cwd and not cwd.startswith("/"):
        return PlainTextResponse("x-ufo-cwd must be an absolute path", status_code=400)
    queue_key = f"{email}{QUEUE_KEY_SEPARATOR}{request.path_params['channel']}"
    conversation_id = await ctx.conversation_for(queue_key, conversation_audience(member_id))
    if request.headers.get(SEND_HEADER, "").strip():
        return await _send(ctx, request, conversation_id, member_id, email, cwd)
    if UNSEND_HEADER in request.headers:
        unsend = request.headers[UNSEND_HEADER].strip()
        return await _unsend(ctx, request, conversation_id, member_id, unsend)
    note: bytes | None = None
    resumed = False
    update = b""
    marked = bool(request.headers.get(LISTEN_HEADER, "").strip())
    op_id = request.headers.get(OP_HEADER, "").strip()
    if op_id:
        reply = await request.body()
        if len(reply) > MAX_OP_REPLY_BYTES:
            ctx.terminal_resolve(
                conversation_id, op_id, b"", "E2BIG: the reply is too large", member_id
            )
            return PlainTextResponse("op reply too large", status_code=413)
        failed = _utf8_header(request, OP_ERR_HEADER) or None
        ctx.terminal_resolve(conversation_id, op_id, bytes(reply), failed, member_id)
        turn_id = await ctx.latest_turn(conversation_id)
        if turn_id is None:
            return PlainTextResponse(directive("ask", PROMPT))
    elif request.headers.get(STOP_HEADER, "").strip():
        if await request.body():
            return PlainTextResponse("a stop admits no message", status_code=400)
        turn_id = await ctx.latest_turn(conversation_id)
        if turn_id is None:
            return PlainTextResponse(directive("ask", PROMPT))
        await ctx.stop_turn(conversation_id, turn_id)
    else:
        if _stale_client(request):
            update = directive("install")
        body = (await request.body()).decode("utf-8", "replace").strip()
        if not body:
            resumed = True
            turn_id = await ctx.latest_turn(conversation_id)
            if turn_id is None:
                return PlainTextResponse(update + directive("ask", PROMPT))
            named, _, held = request.headers.get(SINCE_HEADER, "").strip().partition(":")
            if marked and named == str(turn_id) and await ctx.turn_is_terminal(turn_id):
                return PlainTextResponse(
                    directive("since", named, held) + directive("listen", str(LISTEN_SECONDS))
                )
        else:
            if len(body.encode()) > MAX_MESSAGE_BYTES:
                return PlainTextResponse("message too large", status_code=413)
            if cwd and await ctx.claim_terminal(conversation_id, cwd):
                note = directive("note", f"Workspace: {cwd}")
            turn_id = (
                await ctx.admit(
                    conversation_id,
                    body,
                    context=_turn_context(email, request),
                    speaker_member_id=member_id,
                )
            ).turn_id
    connect = None if member_id is None else partial(ctx.connect_url, turn_id, member_id)
    since = _resumed_from(request, turn_id)
    history: tuple[bytes, ...] = ()
    if resumed and request.headers.get(SINCE_HEADER) is None:
        transcript = await ctx.read_transcript(conversation_id)
        if transcript is not None:
            history = history_directives(transcript)

    async def moved_on() -> bool:
        latest = await ctx.latest_turn(conversation_id)
        return latest is not None and latest != turn_id and not await ctx.turn_is_terminal(latest)

    directives = stream_directives(
        ctx.tail(turn_id, since),
        HOLD_SECONDS,
        ctx.credential_prompt_pending,
        connect,
        partial(shared_files, ctx, turn_id),
        ops=partial(ctx.next_terminal_op, conversation_id, op_id or None) if cwd else None,
        turn_id=turn_id,
        since=since,
        moved_on=moved_on,
        exits=not marked,
    )

    async def bound() -> AsyncIterator[bytes]:
        if cwd:
            ctx.terminal_connect(conversation_id, cwd, member_id)
        try:
            if update:
                yield update
            for line in history:
                yield line
            if note is not None:
                yield note
            async for line in directives:
                yield line
        finally:
            if cwd:
                ctx.terminal_disconnect(conversation_id)

    return StreamingResponse(bound(), media_type="text/plain")


async def _send(
    ctx: SurfaceContext,
    request: Request,
    conversation_id: UUID,
    member_id: UUID | None,
    email: str,
    cwd: str,
) -> Response:
    """Admit one message and answer it, holding nothing. The member's stream is a second request
    already tailing this conversation, so the consequences have somewhere to arrive and this one
    owes only the ack: the turn the message belongs to, whether this delivery opened that turn's
    run, and the `arrival_id` the turn's `absorbed` line will carry when it folds the row in — the
    id a client holds its queued row against. A send with nothing running opens a turn like any
    other message; the stream picks it up.

    `x-ufo-send-id` is the delivery's identity, scoped to the conversation and carried into the
    engine's own idempotency: a resend over a link that dropped the answer joins the turn the first
    delivery landed in rather than saying it twice. The ack states what this delivery did, so a
    resend that finds the turn already opened, or the row already folded, reports the turn and no
    longer claims the run or the pending arrival.

    No terminal is connected here. The ops rendezvous and the frame tail are the held stream's, and
    a send that connected one would take its refcount and its watcher. The binding itself is still
    this request's to make when it is the first message: `cwd` claims it exactly as an admitting
    stream does, and the claim that lands names the directory the agent works in."""
    try:
        send_id = UUID(request.headers.get(SEND_ID_HEADER, "").strip())
    except ValueError:
        return PlainTextResponse(f"{SEND_ID_HEADER} must be a uuid", status_code=400)
    body = (await request.body()).decode("utf-8", "replace").strip()
    if not body:
        return PlainTextResponse("a send carries a message", status_code=400)
    if len(body.encode()) > MAX_MESSAGE_BYTES:
        return PlainTextResponse("message too large", status_code=413)
    note = b""
    if cwd and await ctx.claim_terminal(conversation_id, cwd):
        note = directive("note", f"Workspace: {cwd}")
    admitted = await ctx.admit(
        conversation_id,
        body,
        idempotency_key=f"{conversation_id}{QUEUE_KEY_SEPARATOR}send{QUEUE_KEY_SEPARATOR}{send_id}",
        context=_turn_context(email, request),
        speaker_member_id=member_id,
    )
    ack = directive(
        "sent",
        str(admitted.turn_id),
        "1" if admitted.opened_run else "0",
        "" if admitted.arrival_id is None else str(admitted.arrival_id),
    )
    return PlainTextResponse(ack + note)


async def _unsend(
    ctx: SurfaceContext,
    request: Request,
    conversation_id: UUID,
    member_id: UUID | None,
    unsend: str,
) -> Response:
    """Take back a message the member sent that no turn has taken up — the recall behind pressing
    Up on a queued row. The named arrival is deleted iff it is still pending and the acting member
    spoke it; 200 empty says the words are the member's again, and 409 says they already left
    their hands — folded into a turn, founded onto one, or retracted before. Like a stop, an
    unsend admits nothing."""
    if await request.body():
        return PlainTextResponse("an unsend admits no message", status_code=400)
    if member_id is None:
        return PlainTextResponse("only a member retracts a message", status_code=403)
    try:
        arrival_id = UUID(unsend)
    except ValueError:
        return PlainTextResponse(f"{UNSEND_HEADER} must be an arrival id", status_code=400)
    if await ctx.retract_arrival(conversation_id, arrival_id, member_id):
        return PlainTextResponse(b"")
    return PlainTextResponse("already taken up", status_code=409)


async def _fulfill_secret(
    ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str
) -> Response:
    """Land one privately-entered credential value: the body is the raw secret and never becomes a
    message — no turn is admitted and nothing reaches the transcript. The privileged fulfillment
    verifies the seal (workspace, requesting member, named slot, freshness) before the encrypted
    store takes the value; the shell renders the returned `say` line and moves on."""
    slot = request.headers.get(SECRET_SLOT_HEADER, "").strip()
    value = (await request.body()).decode("utf-8", "replace").strip()
    if not slot or not value:
        return PlainTextResponse(directive("say", "nothing stored - empty value"), status_code=400)
    if len(value.encode()) > MAX_SECRET_BYTES:
        return PlainTextResponse(
            directive("say", "nothing stored - value too large"), status_code=413
        )
    try:
        await ctx.fulfill_credential_request(sealed, slot, value, member_id)
    except CredentialRequestInvalid as error:
        return PlainTextResponse(directive("say", f"not stored - {error}"), status_code=403)
    return PlainTextResponse(directive("say", f"stored {slot}"))


async def op_body(ctx: SurfaceContext, request: Request) -> Response:
    """The bytes an in-flight op sends down to the terminal — what a write's relay `curl`s into
    its staged temp file. An authenticated read projection: the op id is unguessable and single-use,
    the bearer must be the member the binding was made under, and nothing is created or admitted."""
    email = _authenticated_email(request, ctx.workspace_id)
    if email is None:
        return PlainTextResponse("unauthorized", status_code=401)
    member_id = await ctx.linked_member(email)
    queue_key = f"{email}{QUEUE_KEY_SEPARATOR}{request.path_params['channel']}"
    body = await ctx.terminal_op_body(queue_key, request.path_params["op_id"], member_id)
    if body is None:
        return PlainTextResponse("no such op", status_code=404)
    return Response(content=body, media_type="application/octet-stream")


async def system_skills(ctx: SurfaceContext, request: Request) -> Response:
    if _authenticated_email(request, ctx.workspace_id) is None:
        return PlainTextResponse("unauthorized", status_code=401)
    bundle = ctx.system_skill_bundle
    etag = f'"{bundle.digest}"'
    headers = {"etag": etag, "cache-control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=bundle.archive,
        media_type="application/zip",
        headers=headers,
    )


ROUTES = (
    SurfaceRoute(method="POST", path="{channel}", handler=channel),
    SurfaceRoute(method="GET", path="{channel}/op/{op_id}", handler=op_body),
    SurfaceRoute(method="GET", path="{channel}/skills", handler=system_skills),
)
