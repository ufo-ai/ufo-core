"""The Slack surface on the core surface seam: verify an inbound event, key it to a thread
conversation, stream any attached files into the workspace, and admit a turn; then deliver the
terminal reply and stream the turn's shared files into that reply's thread.

While the turn runs, a per-turn status task tails its live frames off the hub and keeps a small
in-thread context message current — "Thinking…", then the model's own narration of each tool call —
deleted when the turn ends. It rides the lossy live leg by design: the durable reply is the
poller's job, so a crashed status task costs a stale status line, never a lost answer.

A reply whose turn ended by asking the user (`Writeback.question`) renders the question's options
as Block Kit buttons. The `interactive` route receives the click, admits the answer as the
conversation's next turn (idempotent per question message — the first click wins), and rewrites the
buttons into the chosen answer with who gave it.

Everything Slack-specific lives here — signature verification, thread keying, Block Kit rendering,
the chunked external-upload flow, the `url_private` download — reaching core only through the
privileged `SurfaceContext` (admit, identity, workspace write, credential read, tail) and the
streaming `BlobStore`. Attachments move without ever buffering a whole file: an inbound file
streams from `url_private` straight into the workspace before the turn runs, and a shared file
streams from the blob store straight to Slack's external-upload URL. Uploads fan out with
`asyncio.gather` on the one event loop — never a thread pool."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx

from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.hub import Parked, SkillLoad, Terminal, ToolCall
from ufo.sdk.surfaces import (
    AskUserInput,
    CredentialSlotUnset,
    SharedArtifact,
    SurfaceContext,
    Writeback,
)

SURFACE_SLACK = "slack"
SLACK_BOT_TOKEN_SLOT = "slack_bot_token"
SLACK_SIGNING_SECRET_SLOT = "slack_signing_secret"
SLACK_BOT_USER_ID_SLOT = "slack_bot_user_id"
SLACK_TEAM_ID_SLOT = "slack_team_id"


def url_verified_blob_key(workspace_id: UUID) -> str:
    """The marker written on each signature-verified `url_verification` handshake — the one event
    that proves Slack reached this deploy with the right signing secret. Keyed by workspace because
    hosted tenants share one blob bucket: a fixed key would let every tenant's handshake overwrite
    every other's. The body records a fingerprint of the verifying secret, so after a rotation the
    setup surface reads the workspace as pending until Slack re-verifies — never a stale
    "connected"."""
    return f"workspaces/{workspace_id}/surfaces/slack/url_verified"


def signing_secret_fingerprint(signing_secret: str) -> str:
    """A non-reversible fingerprint of the signing secret — stamped into the url-verified marker so
    the setup surface can tell a live verification from one left over from a rotated-out secret."""
    return hashlib.sha256(signing_secret.encode()).hexdigest()


SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_CHAT_UPDATE_URL = "https://slack.com/api/chat.update"
SLACK_CHAT_DELETE_URL = "https://slack.com/api/chat.delete"
SLACK_FILES_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
SLACK_FILES_COMPLETE_UPLOAD = "https://slack.com/api/files.completeUploadExternal"

STATUS_THINKING_TEXT = "Thinking…"
STATUS_WORKING_TEXT = "Working… ({tool})"
STATUS_SKILL_TEXT = "Loading skill {skill}…"
STATUS_TEXT_LIMIT = 200
STATUS_UPDATE_MIN_SECONDS = 1.0

ASK_ACTION_ID_PREFIX = "ask:"
MAX_ANSWER_BUTTONS = 10
SLACK_BUTTON_TEXT_LIMIT = 75
SLACK_BUTTON_VALUE_LIMIT = 2_000

SLACK_REPLAY_SECONDS = 300
MAX_SLACK_EVENT_BYTES = 1_000_000
MESSAGE_EVENT_TYPES = ("app_mention", "message")
MEMBER_MESSAGE_SUBTYPES = (None, "file_share")
SLACK_MARKDOWN_TEXT_LIMIT = 12_000
MAX_SLACK_MESSAGE_BYTES = 40_000
SLACK_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
SLACK_INVALID_BLOCKS_ERROR = "invalid_blocks"
SLACK_OVERSIZE_HEADING = "**Attachments (too large to upload):**"

SLACK_API_TIMEOUT_SECONDS = 20
SLACK_UPLOAD_READ_TIMEOUT_SECONDS = 60
SLACK_UPLOAD_WRITE_TIMEOUT_SECONDS = 600
SLACK_DOWNLOAD_TIMEOUT_SECONDS = 600
SLACK_FILE_HOST = "slack.com"
SLACK_FILE_HOST_SUFFIX = ".slack.com"
SLACK_INBOX_DIR = "slack-inbox"
MAX_INBOUND_FILES = 10
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
SLACK_INBOUND_FILE_MAX_BYTES = 25 * 1024 * 1024

SLACK_TURN_FAILED_TEXT = "⚠️ Something went wrong handling your message."
SLACK_TURN_CANCELLED_TEXT = "\U0001f6d1 That request was cancelled."
SLACK_EMPTY_REPLY_TEXT = "(no reply)"

_LOG = logging.getLogger("ufo_ext_slack")


class SlackSignatureError(Exception):
    """The request's Slack signature is missing, stale, or does not match the signing secret."""


class SlackApiError(RuntimeError):
    """A Slack API call returned `ok: false` or a malformed response."""


class SlackDownloadTooLarge(RuntimeError):
    """An inbound file whose streamed bytes exceed SLACK_INBOUND_FILE_MAX_BYTES — the caller skips
    the file rather than writing a partial download into the workspace."""


@dataclass(frozen=True)
class InboundFile:
    name: str
    url: str


@dataclass(frozen=True)
class Inbound:
    """A verified, gated Slack message reduced to what admission and identity need."""

    slack_user_id: str
    queue_key: str
    message_id: str
    is_dm: bool
    body: str
    files: tuple[InboundFile, ...]


def verify_slack_signature(
    headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None = None
) -> None:
    timestamp = headers.get("x-slack-request-timestamp")
    signature = headers.get("x-slack-signature")
    if not timestamp or not signature:
        raise SlackSignatureError("missing Slack signature headers")
    if not timestamp.lstrip("-").isdigit():
        raise SlackSignatureError("invalid Slack timestamp")
    clock = time.time() if now is None else now
    if abs(clock - int(timestamp)) > SLACK_REPLAY_SECONDS:
        raise SlackSignatureError("stale Slack timestamp")
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SlackSignatureError("invalid Slack signature")


def url_verification_challenge(body: bytes) -> str | None:
    """The challenge from a Slack `url_verification` handshake, else None for a real event."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "url_verification":
        return None
    challenge = payload.get("challenge")
    return challenge if isinstance(challenge, str) else ""


def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str:
    """The conversation key: a DM is keyed by its channel, a channel message by its thread root, so
    every reply in one thread shares one conversation."""
    if is_dm:
        return channel
    return f"{channel}:{root_ts}"


def slack_message_gated(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool:
    """Whether the agent should answer: always in a DM or an explicit mention, and in a channel only
    when the bot is @-mentioned — never on every passing message."""
    if event.get("type") == "app_mention" or is_dm:
        return True
    return f"<@{bot_user_id}>" in str(event.get("text") or "")


def slack_reply_body(
    channel: str,
    thread_ts: str | None,
    text: str,
    blocks: bool = True,
    actions: dict[str, object] | None = None,
) -> bytes:
    """The chat.postMessage body: one Block Kit `markdown` block so Slack renders the agent's own
    markdown natively, plus the answer-button `actions` block when the turn ended on a question —
    degrading to a text-only body (buttons and all) when the reply exceeds Slack's block-character
    or payload-byte caps, or when `blocks=False` forces plain text after Slack rejects the blocks as
    `invalid_blocks`. `text` always carries the whole reply as the notification fallback, and the
    question rides it in prose, so a degraded reply is still answerable by a typed reply."""
    if not text:
        raise ValueError("Slack reply text is required")
    base: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts is not None:
        base["thread_ts"] = thread_ts
    if blocks and len(text) <= SLACK_MARKDOWN_TEXT_LIMIT:
        block_list: list[dict[str, object]] = [{"type": "markdown", "text": text}]
        if actions is not None:
            block_list.append(actions)
        with_blocks = {**base, "blocks": block_list}
        encoded = json.dumps(with_blocks, separators=(",", ":")).encode()
        if len(encoded) <= MAX_SLACK_MESSAGE_BYTES:
            return encoded
    encoded = json.dumps(base, separators=(",", ":")).encode()
    if len(encoded) > MAX_SLACK_MESSAGE_BYTES:
        raise ValueError("Slack reply text is too large")
    return encoded


def slack_answer_actions(question: AskUserInput | None) -> dict[str, object] | None:
    """The actions block of answer buttons for a reply whose turn ended on a buttonable question: a
    single ask, with options, single-select, not free-text, and few enough choices for one row.
    Anything richer renders as prose alone — the member answers by replying in the thread, the flow
    every question supports regardless. The option label rides each button's `value`, so the click
    carries the answer itself and the interactive route never re-parses the message."""
    if question is None or len(question.questions) != 1:
        return None
    only = question.questions[0]
    if not only.options or len(only.options) > MAX_ANSWER_BUTTONS:
        return None
    if only.multi_select or only.free_text_only or only.allow_attachments:
        return None
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": option.label[:SLACK_BUTTON_TEXT_LIMIT]},
                "action_id": f"{ASK_ACTION_ID_PREFIX}{index}",
                "value": option.label[:SLACK_BUTTON_VALUE_LIMIT],
            }
            for index, option in enumerate(only.options)
        ],
    }


def _string_field(event: Mapping[str, object], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Slack event field {field!r} is required")
    return value


def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]:
    raw = event.get("files")
    if not isinstance(raw, list):
        return ()
    files: list[InboundFile] = []
    for item in raw[:MAX_INBOUND_FILES]:
        if not isinstance(item, dict) or item.get("mode") in ("tombstone", "hidden_by_limit"):
            continue
        url = item.get("url_private_download") or item.get("url_private")
        name = item.get("name")
        if isinstance(url, str) and url and isinstance(name, str) and name:
            files.append(InboundFile(name=name, url=url))
    return tuple(files)


async def ingest(ctx: SurfaceContext, request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_SLACK_EVENT_BYTES:
        return Response("Slack event too large", status_code=413)
    try:
        signing_secret = await ctx.credential(SLACK_SIGNING_SECRET_SLOT)
    except CredentialSlotUnset:
        # Slack probes the Request URL the moment the app is created from the manifest — before the
        # owner has filled the slots. That is a clean 401 (verification simply hasn't succeeded
        # yet), never a 500 with a stack trace.
        return Response("Slack signing secret is not configured yet", status_code=401)
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError as error:
        return Response(str(error), status_code=401)
    challenge = url_verification_challenge(raw)
    if challenge is not None:
        marker = json.dumps(
            {"fingerprint": signing_secret_fingerprint(signing_secret), "at": time.time()}
        ).encode()
        try:
            await ctx.blob.put(url_verified_blob_key(ctx.workspace_id), marker)
        except Exception:
            # The marker is a best-effort setup signal; a blob hiccup must not fail the handshake
            # Slack needs answered, or the owner can never verify the Request URL.
            _LOG.warning("slack url_verified marker write failed", exc_info=True)
        return JSONResponse({"challenge": challenge})
    inbound = await _to_inbound(ctx, raw)
    if inbound is None:
        return JSONResponse({"ok": True, "ignored": True})
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    member_id = await _resolve_member(ctx, bot_token, inbound) if inbound.is_dm else None
    conversation_id = await ctx.conversation_for(inbound.queue_key, member_id)
    body = inbound.body
    if inbound.files:
        downloaded = await _download_files(ctx, conversation_id, bot_token, inbound.files)
        body = f"{inbound.body}{_files_note(downloaded)}"
    agent_id = await ctx.default_agent()
    turn_id = await ctx.admit(conversation_id, agent_id, body, idempotency_key=inbound.message_id)
    _track_status(ctx, turn_id, inbound.queue_key)
    return JSONResponse({"ok": True})


async def _to_inbound(ctx: SurfaceContext, raw: bytes) -> Inbound | None:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Slack body must be an object")
    if payload.get("team_id") != await ctx.credential(SLACK_TEAM_ID_SLOT):
        return None
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") not in MESSAGE_EVENT_TYPES:
        return None
    if event.get("bot_id") is not None or event.get("subtype") not in MEMBER_MESSAGE_SUBTYPES:
        return None
    bot_user_id = await ctx.credential(SLACK_BOT_USER_ID_SLOT)
    user = event.get("user")
    if not isinstance(user, str) or not user or user == bot_user_id:
        return None
    is_dm = event.get("channel_type") == "im"
    if not slack_message_gated(event, bot_user_id, is_dm):
        return None
    channel = _string_field(event, "channel")
    ts = _string_field(event, "ts")
    root = event.get("thread_ts")
    root_ts = root if isinstance(root, str) and root else ts
    return Inbound(
        slack_user_id=user,
        queue_key=slack_thread_key(channel, root_ts, is_dm),
        message_id=f"{channel}:{ts}",
        is_dm=is_dm,
        body=str(event.get("text") or ""),
        files=_inbound_files(event),
    )


async def _resolve_member(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> UUID | None:
    linked = await ctx.linked_member(inbound.slack_user_id)
    if linked is not None:
        return linked
    email = await _slack_user_email(bot_token, inbound.slack_user_id)
    if email is None:
        return None
    return await ctx.link_member(inbound.slack_user_id, email)


async def _slack_user_email(bot_token: str, slack_user_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
        response = await client.get(
            SLACK_USERS_INFO_URL,
            params={"user": slack_user_id},
            headers={"Authorization": f"Bearer {bot_token}"},
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("ok") is not True:
        raise SlackApiError(str(payload.get("error")))
    user = payload.get("user")
    profile = user.get("profile") if isinstance(user, dict) else None
    email = profile.get("email") if isinstance(profile, dict) else None
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _slack_download_host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == SLACK_FILE_HOST or host.endswith(SLACK_FILE_HOST_SUFFIX)


async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]:
    """Stream a Slack `url_private` download in bounded chunks, refusing to attach the bot token to
    a non-Slack host (httpx also strips it on any cross-host redirect) and capping the total at
    SLACK_INBOUND_FILE_MAX_BYTES — an over-cap file raises SlackDownloadTooLarge mid-stream, so the
    blob store discards the partial write. The bytes never buffer whole — the caller writes each
    chunk straight into the workspace."""
    if not _slack_download_host_ok(url):
        raise ValueError("refusing to send the Slack bot token to a non-Slack host")
    total = 0
    async with httpx.AsyncClient(timeout=SLACK_DOWNLOAD_TIMEOUT_SECONDS) as client:
        async with client.stream(
            "GET", url, headers={"Authorization": f"Bearer {bot_token}"}, follow_redirects=True
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > SLACK_INBOUND_FILE_MAX_BYTES:
                    raise SlackDownloadTooLarge(url)
                yield chunk


@dataclass(frozen=True)
class DownloadedFiles:
    """What the inbound download produced: the workspace names of files that landed, and the Slack
    names of files skipped as over-cap — the note reports both so the model knows what it has."""

    delivered: tuple[str, ...]
    skipped: tuple[str, ...]


async def _download_files(
    ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]
) -> DownloadedFiles:
    used: set[str] = set()
    delivered: list[str] = []
    skipped: list[str] = []
    for file in files:
        name = _inbox_name(file.name, used)
        try:
            await ctx.write_workspace_file(
                conversation_id, f"{SLACK_INBOX_DIR}/{name}", _stream_download(bot_token, file.url)
            )
        except SlackDownloadTooLarge:
            skipped.append(file.name)
            continue
        delivered.append(name)
    return DownloadedFiles(tuple(delivered), tuple(skipped))


def _inbox_name(raw: str, used: set[str]) -> str:
    leaf = raw.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = leaf if leaf not in ("", ".", "..") else "file"
    name = leaf
    stem, dot, suffix = leaf.partition(".")
    index = 1
    while name in used:
        name = f"{stem}-{index}{dot}{suffix}"
        index += 1
    used.add(name)
    return name


def _files_note(downloaded: DownloadedFiles) -> str:
    clauses: list[str] = []
    if downloaded.delivered:
        listed = ", ".join(f"{SLACK_INBOX_DIR}/{name}" for name in downloaded.delivered)
        clauses.append(f"Attached files, saved in the workspace: {listed}")
    if downloaded.skipped:
        listed = ", ".join(downloaded.skipped)
        limit_mb = SLACK_INBOUND_FILE_MAX_BYTES // (1024 * 1024)
        clauses.append(f"Skipped files, too large to download (over {limit_mb} MB): {listed}")
    if not clauses:
        return ""
    return "\n\n" + "".join(f"[{clause}]" for clause in clauses)


def _status_blocks(text: str) -> list[dict[str, object]]:
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": f"⏳ _{text}_"}]}]


@dataclass(frozen=True)
class ThreadStatus:
    """Live feedback for one running turn: post a small context-block status in the thread, keep it
    current from the turn's hub frames — each tool call as the model's own `user_description` when
    it gave one — and delete it when the turn ends (the durable reply is the poller's job). An
    update inside STATUS_UPDATE_MIN_SECONDS of the last send is dropped, not delayed: the next
    distinct frame refreshes, and the delete ends the message regardless."""

    ctx: SurfaceContext
    turn_id: UUID
    channel: str
    thread_ts: str | None

    async def run(self) -> None:
        bot_token = await self.ctx.credential(SLACK_BOT_TOKEN_SLOT)
        async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
            ts = await self._post(client, bot_token)
            try:
                await self._follow(client, bot_token, ts)
            finally:
                await self._send(
                    client, bot_token, SLACK_CHAT_DELETE_URL, {"channel": self.channel, "ts": ts}
                )

    async def _post(self, client: httpx.AsyncClient, bot_token: str) -> str:
        body: dict[str, object] = {
            "channel": self.channel,
            "text": STATUS_THINKING_TEXT,
            "blocks": _status_blocks(STATUS_THINKING_TEXT),
        }
        if self.thread_ts is not None:
            body["thread_ts"] = self.thread_ts
        payload = await self._send(client, bot_token, SLACK_CHAT_POST_MESSAGE_URL, body)
        ts = payload.get("ts")
        if not isinstance(ts, str) or not ts:
            raise SlackApiError("Slack status response missing ts")
        return ts

    async def _follow(self, client: httpx.AsyncClient, bot_token: str, ts: str) -> None:
        shown = STATUS_THINKING_TEXT
        sent_at = time.monotonic()
        async for _cursor, frame in self.ctx.tail(self.turn_id):
            match frame:
                case Terminal() | Parked():
                    return
                case ToolCall(tool=tool, description=description):
                    text = description or STATUS_WORKING_TEXT.format(tool=tool)
                case SkillLoad(skill=skill):
                    text = STATUS_SKILL_TEXT.format(skill=skill)
                case _:
                    continue
            text = text[:STATUS_TEXT_LIMIT]
            if text == shown or time.monotonic() - sent_at < STATUS_UPDATE_MIN_SECONDS:
                continue
            await self._send(
                client,
                bot_token,
                SLACK_CHAT_UPDATE_URL,
                {"channel": self.channel, "ts": ts, "text": text, "blocks": _status_blocks(text)},
            )
            shown, sent_at = text, time.monotonic()

    async def _send(
        self, client: httpx.AsyncClient, bot_token: str, url: str, body: dict[str, object]
    ) -> dict[str, object]:
        return await _slack_ok(
            client.post(
                url,
                content=json.dumps(body),
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
        )


_STATUS_TASKS: dict[UUID, asyncio.Task[None]] = {}


def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str) -> None:
    """Spawn one ThreadStatus task per admitted turn — Slack redelivers events and admission dedupes
    them to the same turn id, so a redelivery must not spawn a second status message. Best-effort by
    design: a failure only logs, and the task always ends because the tail ends on the durable
    terminal state."""
    if turn_id in _STATUS_TASKS:
        return
    channel, separator, thread_ts = queue_key.partition(":")
    status = ThreadStatus(
        ctx=ctx, turn_id=turn_id, channel=channel, thread_ts=thread_ts if separator else None
    )
    task = asyncio.create_task(_run_status(status))
    _STATUS_TASKS[turn_id] = task
    task.add_done_callback(lambda _done: _STATUS_TASKS.pop(turn_id, None))


async def _run_status(status: ThreadStatus) -> None:
    try:
        await status.run()
    except Exception as error:
        _LOG.warning("slack status feedback failed for turn %s: %s", status.turn_id, error)


@dataclass(frozen=True)
class AnswerClick:
    """A verified button click on an ask_user question, reduced to what admission and the message
    rewrite need. The clicked option's label rides the button `value`; the message's fallback `text`
    is the reply prose the rewrite re-renders above the answer line."""

    slack_user_id: str
    queue_key: str
    message_ts: str
    message_text: str
    label: str
    response_url: str


async def interactive(ctx: SurfaceContext, request: Request) -> Response:
    """Slack interactivity ingest: verify the signed form payload, decode a click on an ask_user
    answer button, admit the answer as the conversation's next turn — idempotent per question
    message, so a double click or a second member's click joins the turn the first click won — and
    rewrite the buttons into the winning answer with who answered. Only the click whose body the
    turn stored rewrites (a losing click must not display an answer the agent never saw), and the
    rewrite rides its own task so the ack beats Slack's three-second budget — Block Kit allows no
    message in the direct response, only the ack."""
    raw = await request.body()
    if len(raw) > MAX_SLACK_EVENT_BYTES:
        return Response("Slack payload too large", status_code=413)
    try:
        signing_secret = await ctx.credential(SLACK_SIGNING_SECRET_SLOT)
    except CredentialSlotUnset:
        return Response("Slack signing secret is not configured yet", status_code=401)
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError as error:
        return Response(str(error), status_code=401)
    click = await _to_click(ctx, raw)
    if click is None:
        return JSONResponse({"ok": True, "ignored": True})
    member_id = await ctx.linked_member(click.slack_user_id)
    conversation_id = await ctx.conversation_for(click.queue_key, member_id)
    agent_id = await ctx.default_agent()
    body = f"[Answered by <@{click.slack_user_id}> via button] {click.label}"
    turn_id = await ctx.admit(
        conversation_id,
        agent_id,
        body,
        idempotency_key=f"{click.queue_key}:{click.message_ts}:answer",
    )
    _track_status(ctx, turn_id, click.queue_key)
    if await ctx.turn_inbound(turn_id) == body:
        _rewrite_in_background(click)
    return JSONResponse({"ok": True})


_REWRITE_TASKS: set[asyncio.Task[None]] = set()


def _rewrite_in_background(click: AnswerClick) -> None:
    task = asyncio.create_task(_run_rewrite(click))
    _REWRITE_TASKS.add(task)
    task.add_done_callback(_REWRITE_TASKS.discard)


async def _run_rewrite(click: AnswerClick) -> None:
    try:
        await _replace_buttons_with_answer(click)
    except Exception as error:
        _LOG.warning("slack answer rewrite failed for %s: %s", click.message_ts, error)


async def _to_click(ctx: SurfaceContext, raw: bytes) -> AnswerClick | None:
    form = parse_qs(raw.decode())
    encoded = form.get("payload")
    if not encoded:
        raise ValueError("Slack interactive body must carry a payload field")
    payload = json.loads(encoded[0])
    if not isinstance(payload, dict) or payload.get("type") != "block_actions":
        return None
    team = payload.get("team")
    team_id = team.get("id") if isinstance(team, dict) else None
    if team_id != await ctx.credential(SLACK_TEAM_ID_SLOT):
        return None
    label = _clicked_answer(payload)
    if label is None:
        return None
    channel_id = _string_field(_dict_field(payload, "channel"), "id")
    message = _dict_field(payload, "message")
    thread = message.get("thread_ts")
    return AnswerClick(
        slack_user_id=_string_field(_dict_field(payload, "user"), "id"),
        queue_key=(f"{channel_id}:{thread}" if isinstance(thread, str) and thread else channel_id),
        message_ts=_string_field(message, "ts"),
        message_text=str(message.get("text") or ""),
        label=label,
        response_url=_string_field(payload, "response_url"),
    )


def _clicked_answer(payload: Mapping[str, object]) -> str | None:
    """The clicked option's label when the click is on one of this surface's ask buttons, else None
    — any other interactive payload is ignored, not an error."""
    actions = payload.get("actions")
    action = actions[0] if isinstance(actions, list) and actions else None
    if not isinstance(action, dict):
        return None
    if not str(action.get("action_id") or "").startswith(ASK_ACTION_ID_PREFIX):
        return None
    value = action.get("value")
    return value if isinstance(value, str) and value else None


def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"Slack payload field {field!r} is required")
    return value


async def _replace_buttons_with_answer(click: AnswerClick) -> None:
    """Rewrite the question message through its `response_url`: the reply prose stays, the buttons
    become one small context line — the chosen answer and who answered. The URL comes from a
    signature-verified payload and must still be a Slack host before this surface will POST to
    it."""
    if not _slack_download_host_ok(click.response_url):
        raise ValueError("refusing to answer a non-Slack response_url")
    answered = f"✅ *{click.label}* · Answered by <@{click.slack_user_id}>"
    blocks: list[dict[str, object]] = []
    if click.message_text:
        blocks.append({"type": "markdown", "text": click.message_text})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": answered}]})
    async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            click.response_url,
            json={
                "replace_original": True,
                "text": click.message_text or answered,
                "blocks": blocks,
            },
        )
        response.raise_for_status()


def _reply_text(writeback: Writeback) -> str:
    """What to post for a terminal turn: the agent's reply for a done turn (a placeholder when it
    produced none), or a short outcome line so a failed or cancelled turn still answers."""
    if writeback.status == "failed":
        return SLACK_TURN_FAILED_TEXT
    if writeback.status == "cancelled":
        return SLACK_TURN_CANCELLED_TEXT
    return writeback.text or SLACK_EMPTY_REPLY_TEXT


def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str:
    """The reply text, plus a link block for any shared file too large to upload inline — a TTL
    download link so an over-cap artifact is delivered rather than silently dropped."""
    text = _reply_text(writeback)
    oversized = tuple(a for a in writeback.artifacts if a.size_bytes > SLACK_UPLOAD_MAX_BYTES)
    if not oversized:
        return text
    lines = "\n".join(_oversize_link_line(ctx, artifact) for artifact in oversized)
    return f"{text}\n\n{SLACK_OVERSIZE_HEADING}\n{lines}"


def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str:
    url = ctx.artifact_link(artifact)
    name = f"[{artifact.filename}]({url})" if url else artifact.filename
    return f"- {name} ({artifact.size_bytes} bytes)"


async def post(ctx: SurfaceContext, writeback: Writeback) -> str:
    """Post the reply to the thread and return its message ref (`channel:ts`) — the ref both records
    the delivery and, as the posted reply's own thread ts, roots any attached files under it. An
    `invalid_blocks` rejection is deterministic, so the reply re-posts once as plain text rather
    than the poller retrying the identical Block Kit body until it ages out."""
    channel, separator, thread_ts = writeback.queue_key.partition(":")
    thread = thread_ts if separator else None
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    text = _reply_with_oversize_links(ctx, writeback)
    actions = slack_answer_actions(writeback.question)
    async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
        payload = await _chat_post(
            client, bot_token, slack_reply_body(channel, thread, text, actions=actions)
        )
        if payload.get("error") == SLACK_INVALID_BLOCKS_ERROR:
            payload = await _chat_post(
                client, bot_token, slack_reply_body(channel, thread, text, blocks=False)
            )
    if payload.get("ok") is not True:
        raise SlackApiError(str(payload.get("error")))
    ts = payload.get("ts")
    if not isinstance(ts, str) or not ts:
        raise SlackApiError("Slack response missing ts")
    return f"{channel}:{ts}"


async def _chat_post(
    client: httpx.AsyncClient, bot_token: str, body: bytes
) -> Mapping[str, object]:
    """POST one chat.postMessage body and return its parsed payload without asserting `ok`, so the
    caller can branch on a recoverable `error` (an `invalid_blocks` retry) before failing."""
    response = await client.post(
        SLACK_CHAT_POST_MESSAGE_URL,
        content=body,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    response.raise_for_status()
    return response.json()


async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None:
    """Stream each shared file that fits the upload cap into the posted reply's thread, all at once
    on the event loop; an over-cap file is delivered as a link in `post`, not here. Best effort: a
    rejected file is logged and the rest still deliver, so an upload never re-posts the reply or
    blocks its siblings."""
    inline = tuple(a for a in writeback.artifacts if a.size_bytes <= SLACK_UPLOAD_MAX_BYTES)
    if not inline:
        return
    channel, _, reply_ts = reply_ref.partition(":")
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    results = await asyncio.gather(
        *(_upload_artifact(ctx, bot_token, channel, reply_ts, artifact) for artifact in inline),
        return_exceptions=True,
    )
    for artifact, result in zip(inline, results, strict=True):
        if isinstance(result, BaseException):
            _LOG.warning("slack attachment upload failed for %s: %s", artifact.filename, result)


async def _upload_artifact(
    ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str, artifact: SharedArtifact
) -> None:
    """The three-step external upload, streamed: reserve an upload URL for the exact byte length,
    POST the blob's bytes to it (streamed from the blob store, never buffered), then complete the
    upload into the thread with the caption or the plain filename as its title."""
    timeout = httpx.Timeout(
        SLACK_UPLOAD_READ_TIMEOUT_SECONDS, write=SLACK_UPLOAD_WRITE_TIMEOUT_SECONDS
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        reservation = await _slack_ok(
            client.post(
                SLACK_FILES_GET_UPLOAD_URL,
                headers={"Authorization": f"Bearer {bot_token}"},
                data={"filename": artifact.filename, "length": str(artifact.size_bytes)},
            )
        )
        upload_url = reservation.get("upload_url")
        file_id = reservation.get("file_id")
        if not isinstance(upload_url, str) or not isinstance(file_id, str):
            raise SlackApiError("Slack upload reservation missing upload_url or file_id")
        posted = await client.post(
            upload_url,
            content=ctx.blob.get_stream(artifact.blob_key),
            headers={"Content-Type": "application/octet-stream"},
        )
        posted.raise_for_status()
        await _slack_ok(
            client.post(
                SLACK_FILES_COMPLETE_UPLOAD,
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                content=json.dumps(
                    {
                        "files": [{"id": file_id, "title": artifact.subject or artifact.filename}],
                        "channel_id": channel,
                        "thread_ts": thread_ts,
                    }
                ),
            )
        )


async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]:
    response = await request
    response.raise_for_status()
    payload = response.json()
    if payload.get("ok") is not True:
        raise SlackApiError(str(payload.get("error")))
    return payload
