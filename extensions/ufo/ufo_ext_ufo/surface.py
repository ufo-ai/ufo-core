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
admits nothing and resumes tailing the conversation's latest turn."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from functools import partial
from uuid import UUID

from ufo.sdk.accounting import MICRO_USD_PER_USD
from ufo.sdk.audience import conversation_audience
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.http import PlainTextResponse, Request, Response, StreamingResponse
from ufo.sdk.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, TextDelta, ToolCall
from ufo.sdk.surfaces import (
    ConnectRequestInvalid,
    CredentialPrompt,
    CredentialRequestInvalid,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
)

SURFACE_UFO = "ufo"
PROMPT = ">"
POLL_SECONDS = 1
MAX_MESSAGE_BYTES = 40_000
MAX_SECRET_BYTES = 4_096
SECRET_HEADER = "x-ufo-secret"
SECRET_SLOT_HEADER = "x-ufo-slot"
QUEUE_KEY_SEPARATOR = ":"

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


async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None:
    """The `SurfaceSpec.identify` the shared fleet calls to scope a request before its handler runs:
    the workspace the request's bearer claims, or None to reject. The same bearer the handler
    re-verifies for the member email — workspace here, identity there, from the one signature."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return workspace_claim(token.strip())


def directives_for(
    frame: LiveFrame,
    streamed: bool,
    collect: tuple[CredentialPrompt, ...] = (),
    connect_message: str | None = None,
) -> tuple[bytes, ...]:
    """The directive lines one live frame renders to. Token deltas stream as `txt`; tool and skill
    activity narrates as `note`; a running cost meter is a transient `status`; the terminal frame
    caps the turn (`streamed` says the answer already reached the transcript as `txt`, `collect`
    names the credential prompts still awaiting values)."""
    match frame:
        case TextDelta():
            return (directive("txt", frame.text),) if frame.text else ()
        case ToolCall():
            return (directive("note", _activity(frame)),)
        case SkillLoad():
            return (directive("note", f"loading skill: {frame.skill}"),)
        case CostTick():
            cost = frame.cost_micro_usd / MICRO_USD_PER_USD
            return (directive("status", f"{frame.tokens} tok - ${cost:.6f}"),)
        case Terminal():
            return _answer(frame, streamed, collect, connect_message)
        case Parked():
            return (directive("say", frame.message), directive("ask", PROMPT))
    raise ValueError(f"unmapped live frame {type(frame).__name__}")


def _activity(frame: ToolCall) -> str:
    detail = frame.description or frame.preview
    return f"running {frame.tool}: {detail}" if detail else f"running {frame.tool}"


def _answer(
    terminal: Terminal,
    streamed: bool,
    collect: tuple[CredentialPrompt, ...] = (),
    connect_message: str | None = None,
) -> tuple[bytes, ...]:
    """Cap a turn. A done turn prompts (`ask`) after its answer — already streamed as `txt`, else
    said now, preceded by one `secret` line per still-unanswered credential prompt, so the shell
    collects exactly the missing values privately; a failure says its error and prompts so the
    member can retry; a cancel says so and ends the client session (`exit`), the conversation
    resuming on the next `ufo`."""
    frame = terminal.frame
    match frame.status:
        case "done":
            said = () if streamed else _say_lines(frame.text)
            sealed = "" if frame.credential_request is None else frame.credential_request.sealed
            secrets = tuple(
                directive("secret", sealed, prompt.slot, prompt.prompt) for prompt in collect
            )
            connect = () if connect_message is None else (directive("say", connect_message),)
            return (*said, *secrets, *connect, directive("ask", PROMPT))
        case "failed":
            return (
                *_say_lines(frame.text or frame.error_class or "the turn failed"),
                directive("ask", PROMPT),
            )
        case "cancelled":
            return (directive("say", "cancelled"), directive("exit", "0"))
    raise ValueError(f"unmapped terminal status {frame.status!r}")


def _say_lines(text: str) -> tuple[bytes, ...]:
    return tuple(directive("say", line) for line in (text.splitlines() or [text]))


async def stream_directives(
    frames: AsyncIterator[tuple[str, LiveFrame]],
    hold_seconds: float,
    pending: Callable[[str, str], Awaitable[bool]] | None = None,
    connect: Callable[[], Awaitable[str]] | None = None,
) -> AsyncIterator[bytes]:
    """Render a turn's live frames as directives, holding at most `hold_seconds`. A terminal or
    parked frame closes the stream on its own cap; if the hold elapses first the stream ends with
    `poll` so the shell reconnects to drain the durable answer. `pending` gates each prompt of a
    terminal frame's credential request, so a fulfilled or expired prompt never re-renders on
    reconnect while an unanswered sibling keeps asking."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + hold_seconds
    streamed = False
    terminated = False
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(_next(frames), remaining)
            except TimeoutError:
                break
            if item is None:
                break
            _cursor, frame = item
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
                        connect_message = "Connection request unavailable; ask me to connect again."
                    else:
                        connect_message = f"Complete the connection: {url}"
            lines = directives_for(frame, streamed, collect, connect_message)
            if lines and isinstance(frame, TextDelta):
                streamed = True
            for line in lines:
                yield line
            if isinstance(frame, Terminal | Parked):
                terminated = True
                break
    finally:
        if isinstance(frames, AsyncGenerator):
            await frames.aclose()
    if not terminated:
        yield directive("poll", str(POLL_SECONDS))


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


async def channel(ctx: SurfaceContext, request: Request) -> Response:
    """One held turn on a channel. The bearer names the member; the channel path scopes their
    conversation. A body admits a turn and streams it; an empty body admits nothing and resumes
    tailing the conversation's latest turn (or prompts when it holds none)."""
    email = _authenticated_email(request, ctx.workspace_id)
    if email is None:
        return PlainTextResponse("unauthorized", status_code=401)
    member_id = await ctx.linked_member(email) or await ctx.link_member(email, email)
    sealed = request.headers.get(SECRET_HEADER)
    if sealed:
        return await _fulfill_secret(ctx, request, member_id, sealed)
    queue_key = f"{email}{QUEUE_KEY_SEPARATOR}{request.path_params['channel']}"
    conversation_id = await ctx.conversation_for(queue_key, conversation_audience(member_id))
    body = (await request.body()).decode("utf-8", "replace").strip()
    if not body:
        turn_id = await ctx.latest_turn(conversation_id)
        if turn_id is None:
            return PlainTextResponse(directive("ask", PROMPT))
    else:
        if len(body.encode()) > MAX_MESSAGE_BYTES:
            return PlainTextResponse("message too large", status_code=413)
        turn_id = await ctx.admit(conversation_id, body, speaker_member_id=member_id)
    connect = None if member_id is None else partial(ctx.connect_url, turn_id, member_id)
    return StreamingResponse(
        stream_directives(
            ctx.tail(turn_id),
            HOLD_SECONDS,
            ctx.credential_prompt_pending,
            connect,
        ),
        media_type="text/plain",
    )


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


ROUTES = (SurfaceRoute(method="POST", path="{channel}", handler=channel),)
