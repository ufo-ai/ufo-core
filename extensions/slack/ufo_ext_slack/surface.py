"""The Slack surface on the core surface seam: verify an inbound event, key it to a thread
conversation, stream any attached files into the workspace, and admit a turn; then deliver the
terminal reply and stream the turn's shared files into the conversation's thread. The agent
answers when addressed — a DM or an @-mention — and, once a mention has made a thread its
conversation, every member reply in that thread, un-mentioned included, like any participant
pulled into a thread. A conversation-starting channel turn carries a bounded digest of ambient
context fetched from Slack at admit time — the thread's earlier un-addressed messages when
mentioned mid-thread, the channel's recent messages when starting a fresh thread; after that
every member reply is its own turn, so the transcript itself holds the thread. A first-time DM
speaker resolves by Slack-confirmed email: an existing member links, and a same-domain teammate
joins as a new member — only the owner ever onboards through the CLI.

While the turn runs, a per-turn status task tails its live frames off the hub and keeps the
thread's native status (`assistant.threads.setStatus`) current — "Thinking…", then the model's own
narration of each tool call — cleared when the turn ends. The status is thread-keyed state, not a
message: a duplicate writer (Slack redelivers events, and every replica runs its own task)
overwrites it rather than stacking a second indicator, and within a process the newest turn is a
thread's one writer. It rides the lossy live leg by design: the durable reply is the poller's job,
so a crashed status task costs a stale status, never a lost answer.

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
import re
import time
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.hub import Parked, SkillLoad, Terminal, ToolCall
from ufo.sdk.sandbox import BlobStore
from ufo.sdk.surfaces import (
    AskUserInput,
    ConnectRequest,
    ConnectRequestInvalid,
    CredentialSlotUnset,
    SharedArtifact,
    SurfaceAuth,
    SurfaceContext,
    SurfaceDeliveryError,
    SurfaceWorkspaceUnknown,
    TurnContext,
    Writeback,
)

SURFACE_SLACK = "slack"
SLACK_BOT_TOKEN_SLOT = "slack_bot_token"
SLACK_SIGNING_SECRET_SLOT = "slack_signing_secret"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
SLACK_IDENTITY_TIMEOUT_SECONDS = 20
TEAM_ID_PATTERN = r"^T[A-Z0-9]+$"
BOT_USER_ID_PATTERN = r"^[UW][A-Z0-9]+$"
MALFORMED_IDENTITY_ERROR = "malformed identity"


class SlackIdentityError(RuntimeError):
    def __init__(self, error: str):
        self.error = error
        super().__init__(error)


class SlackIdentity(BaseModel):
    """The app's derived identity — the team and bot-user ids `auth.test` proved for the stored
    bot token, pinned to that token's fingerprint so a rotation reads as absent until
    `slack_connect` re-derives. Not credentials: derived metadata, custodied as the surface's own
    record beside its url-verified marker."""

    bot_token_fingerprint: str
    team_id: str
    bot_user_id: str


def identity_blob_key(workspace_id: UUID) -> str:
    return f"workspaces/{workspace_id}/surfaces/slack/identity"


def bot_token_fingerprint(bot_token: str) -> str:
    return hashlib.sha256(bot_token.encode()).hexdigest()


async def read_identity(
    blob: BlobStore, workspace_id: UUID, bot_token: str
) -> SlackIdentity | None:
    """The stored identity record, or None when absent, unreadable, or derived from a since-rotated
    token — never a stale team/bot id gating events for the wrong app."""
    key = identity_blob_key(workspace_id)
    if not await blob.exists(key):
        return None
    try:
        identity = SlackIdentity.model_validate_json(await blob.get(key))
    except ValueError:
        return None
    if identity.bot_token_fingerprint != bot_token_fingerprint(bot_token):
        return None
    return identity


@dataclass(frozen=True)
class SlackIdentityResolver:
    blob: BlobStore
    workspace_id: UUID
    bot_token: str

    async def resolve(self) -> SlackIdentity:
        """Return the app identity bound to the bot token, proving and persisting it when absent."""
        identity = await read_identity(self.blob, self.workspace_id, self.bot_token)
        if identity is not None:
            return identity
        identity = await self._prove()
        await self.blob.put(
            identity_blob_key(self.workspace_id), identity.model_dump_json().encode()
        )
        return identity

    async def _prove(self) -> SlackIdentity:
        try:
            async with httpx.AsyncClient(timeout=SLACK_IDENTITY_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    SLACK_AUTH_TEST_URL,
                    headers={"authorization": f"Bearer {self.bot_token}"},
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise SlackIdentityError(f"unreachable: {error}") from error
        if not isinstance(payload, dict):
            raise SlackIdentityError("malformed response")
        if payload.get("ok") is not True:
            raise SlackIdentityError(str(payload.get("error") or "no error given"))
        team_id = payload.get("team_id")
        bot_user_id = payload.get("user_id")
        if not isinstance(team_id, str) or not re.match(TEAM_ID_PATTERN, team_id):
            raise SlackIdentityError(MALFORMED_IDENTITY_ERROR)
        if not isinstance(bot_user_id, str) or not re.match(BOT_USER_ID_PATTERN, bot_user_id):
            raise SlackIdentityError(MALFORMED_IDENTITY_ERROR)
        return SlackIdentity(
            bot_token_fingerprint=bot_token_fingerprint(self.bot_token),
            team_id=team_id,
            bot_user_id=bot_user_id,
        )


async def _identity(ctx: SurfaceContext) -> SlackIdentity | None:
    try:
        bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    except CredentialSlotUnset:
        return None
    return await read_identity(ctx.blob, ctx.workspace_id, bot_token)


_IDENTITY_TASKS: dict[UUID, asyncio.Task[None]] = {}


def _prove_identity_in_background(ctx: SurfaceContext) -> None:
    if ctx.workspace_id in _IDENTITY_TASKS:
        return
    task = asyncio.create_task(_run_identity_proof(ctx))
    _IDENTITY_TASKS[ctx.workspace_id] = task

    def _untrack(done: asyncio.Task[None]) -> None:
        if _IDENTITY_TASKS.get(ctx.workspace_id) is done:
            del _IDENTITY_TASKS[ctx.workspace_id]

    task.add_done_callback(_untrack)


async def _run_identity_proof(ctx: SurfaceContext) -> None:
    try:
        bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
        await SlackIdentityResolver(ctx.blob, ctx.workspace_id, bot_token).resolve()
    except SlackIdentityError as error:
        _LOG.error("slack identity proof failed: %s", error)
    except Exception:
        _LOG.error("slack identity proof failed", exc_info=True)


def url_verified_blob_key(workspace_id: UUID) -> str:
    """The marker written on a signature-verified inbound request — proof Slack reached this deploy
    with the signing secret currently stored, whether by the `url_verification` handshake or a real
    event. Keyed by workspace because hosted tenants share one blob bucket: a fixed key would let
    every tenant's marker overwrite every other's. The body records a fingerprint of the verifying
    secret, so after a rotation `slack_connect` reads the workspace as pending until Slack's next
    signed request — never a stale "connected"."""
    return f"workspaces/{workspace_id}/surfaces/slack/url_verified"


def signing_secret_fingerprint(signing_secret: str) -> str:
    """A non-reversible fingerprint of the signing secret — stamped into the url-verified marker so
    `slack_connect` can tell a live verification from one left over from a rotated-out secret."""
    return hashlib.sha256(signing_secret.encode()).hexdigest()


SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_CONVERSATIONS_REPLIES_URL = "https://slack.com/api/conversations.replies"
SLACK_CONVERSATIONS_HISTORY_URL = "https://slack.com/api/conversations.history"
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_ASSISTANT_STATUS_URL = "https://slack.com/api/assistant.threads.setStatus"
SLACK_FILES_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
SLACK_FILES_COMPLETE_UPLOAD = "https://slack.com/api/files.completeUploadExternal"

STATUS_THINKING_TEXT = "Thinking…"
STATUS_WORKING_TEXT = "Working… ({tool})"
STATUS_SKILL_TEXT = "Loading skill {skill}…"
STATUS_CLEAR_TEXT = ""
STATUS_TEXT_LIMIT = 200
STATUS_UPDATE_MIN_SECONDS = 1.0
STATUS_REFRESH_SECONDS = 90.0

ASK_ACTION_ID_PREFIX = "ask:"
CONNECT_ACTION_ID = "connect"
MAX_ANSWER_BUTTONS = 10
SLACK_BUTTON_TEXT_LIMIT = 75
SLACK_BUTTON_VALUE_LIMIT = 2_000

SLACK_REPLAY_SECONDS = 300
MAX_SLACK_EVENT_BYTES = 1024 * 1024
SLACK_RAW_BODY_STATE_KEY = "slack_raw_body"
MESSAGE_EVENT_TYPES = ("app_mention", "message")
MEMBER_MESSAGE_SUBTYPES = (None, "file_share", "thread_broadcast")

AMBIENT_FETCH_LIMIT = 100
AMBIENT_CHANNEL_FETCH_LIMIT = 15
AMBIENT_FETCH_TIMEOUT_SECONDS = 2.5
AMBIENT_MESSAGE_SUBTYPES = (None, "file_share", "thread_broadcast")
AMBIENT_MESSAGE_CHAR_LIMIT = 400
AMBIENT_DIGEST_MAX_CHARS = 8_000
AMBIENT_THREAD_HEADER = (
    "[Thread messages for context — not addressed to you; answer the final message:]"
)
AMBIENT_CHANNEL_HEADER = (
    "[Recent channel messages for context — not addressed to you; answer the final message:]"
)
AMBIENT_OMITTED_MARKER = "[… earlier messages omitted …]"
SLACK_MARKDOWN_TEXT_LIMIT = 12_000
SLACK_SECTION_TEXT_LIMIT = 3_000
SLACK_CONTEXT_TEXT_LIMIT = 3_000
MAX_SLACK_MESSAGE_BYTES = 40_000
MAX_SLACK_BLOCK_MESSAGE_BYTES = 100_000
SLACK_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
SLACK_INVALID_BLOCKS_ERROR = "invalid_blocks"
SLACK_OVERSIZE_HEADING = "**Attachments (too large to upload):**"

SLACK_API_TIMEOUT_SECONDS = 20
SLACK_RETRY_AFTER_MAX_SECONDS = 2_147_483_647
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


class SlackBodyTooLarge(Exception):
    """The streamed Slack request crossed the inbound payload bound."""


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
    """A verified, gated Slack message reduced to what admission, identity, and the thread status
    need. `conversation_id` is the channel message's already-conversing conversation — one holding
    an admitted turn — None for a DM or a conversation-starting message; it is the participation
    that admits an un-addressed reply, and the signal that the transcript already holds the thread
    so no ambient digest is fetched."""

    slack_user_id: str
    queue_key: str
    message_id: str
    ts: str
    is_dm: bool
    body: str
    files: tuple[InboundFile, ...]
    conversation_id: UUID | None


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


_RAW_BODY_MISSING = object()
_RAW_BODY_OVERFLOW = object()


async def _slack_request_body(request: Request) -> bytes:
    state = request.scope.setdefault("state", {})
    cached = state.get(SLACK_RAW_BODY_STATE_KEY, _RAW_BODY_MISSING)
    if cached is _RAW_BODY_OVERFLOW:
        raise SlackBodyTooLarge
    if cached is not _RAW_BODY_MISSING:
        if not isinstance(cached, bytes):
            raise RuntimeError("Slack raw body cache is invalid")
        return cached
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_SLACK_EVENT_BYTES:
            request.state.slack_raw_body = _RAW_BODY_OVERFLOW
            raise SlackBodyTooLarge
        chunks.append(chunk)
    raw = b"".join(chunks)
    request.state.slack_raw_body = raw
    return raw


def url_verification_challenge(body: bytes) -> str | None:
    """The challenge from a Slack `url_verification` handshake, else None for a real event."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "url_verification":
        return None
    challenge = payload.get("challenge")
    return challenge if isinstance(challenge, str) else ""


def slack_team_hint(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            encoded = parse_qs(body.decode()).get("payload")
            payload = json.loads(encoded[0]) if encoded else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("team_id")
    if not isinstance(candidate, str):
        team = payload.get("team")
        candidate = team.get("id") if isinstance(team, dict) else None
    if not isinstance(candidate, str) or re.fullmatch(TEAM_ID_PATTERN, candidate) is None:
        return None
    return candidate


def slack_installation_id(team_id: str) -> str:
    return f"team:{team_id}"


async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None:
    """Resolve a canonical Slack callback through its registered team, then authenticate the exact
    bytes with that workspace's signing secret before core binds its RLS scope. The team id is only
    a lookup hint; unknown teams and bad signatures share the same rejection. URL verification has
    no team id, so its bounded challenge echoes without binding or storing state."""
    try:
        raw = await _slack_request_body(request)
    except SlackBodyTooLarge:
        return None
    challenge = url_verification_challenge(raw)
    if challenge is not None:
        return JSONResponse({"challenge": challenge})
    team_id = slack_team_hint(raw)
    if team_id is None:
        return None
    workspace_id = await auth.workspace(slack_installation_id(team_id))
    if workspace_id is None:
        return None
    try:
        signing_secret = await auth.credential(workspace_id, SLACK_SIGNING_SECRET_SLOT)
    except SurfaceWorkspaceUnknown:
        return None
    except CredentialSlotUnset:
        return None
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError:
        return None
    return workspace_id


def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str:
    """The conversation key: a DM is keyed by its channel, a channel message by its thread root, so
    every reply in one thread shares one conversation."""
    if is_dm:
        return channel
    return f"{channel}:{root_ts}"


def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool:
    """Whether the message addresses the agent directly — always in a DM, in a channel only by
    @-mention. Direct address always admits; an un-addressed channel message is admitted only as a
    reply in a thread the agent already converses in (`_participating_conversation`), never as
    passing top-level traffic."""
    if event.get("type") == "app_mention" or is_dm:
        return True
    return f"<@{bot_user_id}>" in str(event.get("text") or "")


def slack_reply_body(
    channel: str,
    thread_ts: str | None,
    text: str,
    metadata: str,
    blocks: bool = True,
    actions: dict[str, object] | None = None,
    sections: bool = False,
) -> bytes:
    """The chat.postMessage body: one Block Kit `markdown` block so Slack renders the agent's own
    markdown natively, plus the answer-button `actions` block when the turn ended on a question and
    a final `context` block for the turn's accounting and model metadata —
    degrading to a text-only body when a reply without required actions exceeds Slack's block or
    payload caps. Action-bearing replies split their text across bounded blocks; `sections=True`
    uses conservative section blocks after Slack rejects markdown blocks as `invalid_blocks`.
    `text` always carries the whole reply as the notification fallback."""
    if not text or not metadata:
        raise ValueError("Slack reply text and metadata are required")
    if len(metadata) > SLACK_CONTEXT_TEXT_LIMIT:
        raise ValueError("Slack reply metadata is too large")
    base: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts is not None:
        base["thread_ts"] = thread_ts
    if blocks and (len(text) <= SLACK_MARKDOWN_TEXT_LIMIT or actions is not None):
        limit = SLACK_SECTION_TEXT_LIMIT if sections else SLACK_MARKDOWN_TEXT_LIMIT
        chunks = [text[start : start + limit] for start in range(0, len(text), limit)]
        block_list: list[dict[str, object]] = [
            (
                {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
                if sections
                else {"type": "markdown", "text": chunk}
            )
            for chunk in chunks
        ]
        if actions is not None:
            block_list.append(actions)
        block_list.append(
            {"type": "context", "elements": [{"type": "plain_text", "text": metadata}]}
        )
        with_blocks = {**base, "blocks": block_list}
        encoded = json.dumps(with_blocks, separators=(",", ":")).encode()
        bound = MAX_SLACK_BLOCK_MESSAGE_BYTES if actions is not None else MAX_SLACK_MESSAGE_BYTES
        if len(encoded) <= bound:
            return encoded
    base["text"] = f"{text}\n\n{metadata}"
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


def slack_connect_actions(
    request: ConnectRequest | None, turn_id: UUID
) -> dict[str, object] | None:
    """The requester-checked private OAuth handoff for a terminal connect request."""
    if request is None:
        return None
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": f"Connect {request.provider}"[:SLACK_BUTTON_TEXT_LIMIT],
                },
                "action_id": CONNECT_ACTION_ID,
                "value": str(turn_id),
            }
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


_URL_VERIFIED_WRITTEN: dict[UUID, str] = {}


async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None:
    """Record that Slack reached this deploy with a request the stored secret verified — the signal
    the `slack_connect` tool's `connected` state reads. Callers gate what counts as proof: the
    `url_verification` handshake (Slack's own URL check, which carries no team) or a request from
    the configured team — never a stray on-team-mismatch event, which would read as connected while
    the team gate drops everything. The cache holds the fingerprint this process last wrote, so the
    write stays off the hot path yet any rotation — including back to an earlier secret — re-stamps
    on the next proof. Best effort: a blob hiccup must not fail the request Slack needs answered."""
    fingerprint = signing_secret_fingerprint(signing_secret)
    if _URL_VERIFIED_WRITTEN.get(ctx.workspace_id) == fingerprint:
        return
    marker = json.dumps({"fingerprint": fingerprint, "at": time.time()}).encode()
    try:
        await ctx.blob.put(url_verified_blob_key(ctx.workspace_id), marker)
    except Exception:
        _LOG.warning("slack url_verified marker write failed", exc_info=True)
        return
    _URL_VERIFIED_WRITTEN[ctx.workspace_id] = fingerprint


async def ingest(ctx: SurfaceContext, request: Request) -> Response:
    try:
        raw = await _slack_request_body(request)
    except SlackBodyTooLarge:
        return Response("Slack event too large", status_code=413)
    try:
        signing_secret = await ctx.credential(SLACK_SIGNING_SECRET_SLOT)
    except CredentialSlotUnset:
        # Slack probes the Request URL the moment the app is created from the manifest — before the
        # owner can hold the secret Slack only mints with the app. Echoing the caller's own
        # challenge stores nothing and grants nothing, so it is safe unsigned, and app creation
        # verifies clean instead of showing a failed handshake. Real events stay 401 until the
        # slots are filled.
        challenge = url_verification_challenge(raw)
        if challenge is not None:
            return JSONResponse({"challenge": challenge})
        return Response("Slack signing secret is not configured yet", status_code=401)
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError as error:
        return Response(str(error), status_code=401)
    challenge = url_verification_challenge(raw)
    if challenge is not None:
        await _mark_url_verified(ctx, signing_secret)
        return JSONResponse({"challenge": challenge})
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Slack body must be an object")
    identity = await _identity(ctx)
    if identity is None:
        _prove_identity_in_background(ctx)
        return Response("Slack identity is being verified", status_code=503)
    if payload.get("team_id") != identity.team_id:
        return JSONResponse({"ok": True, "ignored": True})
    await _mark_url_verified(ctx, signing_secret)
    inbound = await _to_inbound(ctx, payload, identity)
    if inbound is None:
        return JSONResponse({"ok": True, "ignored": True})
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    sender, context = await asyncio.gather(
        _slack_user(bot_token, inbound.slack_user_id),
        _ambient_context(ctx, bot_token, inbound, identity),
    )
    member_id = await _resolve_member(ctx, inbound.slack_user_id, inbound.is_dm, sender)
    conversation_id = inbound.conversation_id
    if conversation_id is None:
        conversation_id = await ctx.conversation_for(
            inbound.queue_key, member_id if inbound.is_dm else None
        )
    body = f"{context}{inbound.body}"
    if inbound.files:
        downloaded = await _download_files(ctx, conversation_id, bot_token, inbound.files)
        body = f"{body}{_files_note(downloaded)}"
    agent_id = await ctx.default_agent()
    turn_id = await ctx.admit(
        conversation_id,
        agent_id,
        body,
        idempotency_key=inbound.message_id,
        context=_turn_context(sender),
        speaker_member_id=member_id,
    )
    _track_status(ctx, turn_id, inbound.queue_key, inbound.ts)
    return JSONResponse({"ok": True})


async def _to_inbound(
    ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity
) -> Inbound | None:
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") not in MESSAGE_EVENT_TYPES:
        return None
    if event.get("bot_id") is not None or event.get("subtype") not in MEMBER_MESSAGE_SUBTYPES:
        return None
    bot_user_id = identity.bot_user_id
    user = event.get("user")
    if not isinstance(user, str) or not user or user == bot_user_id:
        return None
    is_dm = event.get("channel_type") == "im"
    addressed = slack_message_addressed(event, bot_user_id, is_dm)
    root = event.get("thread_ts")
    root_ts = root if isinstance(root, str) and root else None
    if not addressed and root_ts is None:
        return None
    channel = _string_field(event, "channel")
    ts = _string_field(event, "ts")
    queue_key = slack_thread_key(channel, root_ts or ts, is_dm)
    conversation_id = None if is_dm else await _participating_conversation(ctx, queue_key)
    if not addressed and conversation_id is None:
        return None
    return Inbound(
        slack_user_id=user,
        queue_key=queue_key,
        message_id=f"{channel}:{ts}",
        ts=ts,
        is_dm=is_dm,
        body=str(event.get("text") or ""),
        files=_inbound_files(event),
        conversation_id=conversation_id,
    )


async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None:
    """The thread's conversation once it holds an admitted turn, else None. Participation is the
    transcript, never a bare conversation row: the row is created mid-ingest before the
    conversation-starting turn is admitted, and gating on it alone would let a racing reply be
    admitted ahead of that turn — or let a redelivery of the starting mention skip its ambient
    backfill."""
    conversation_id = await ctx.find_conversation(queue_key)
    if conversation_id is None:
        return None
    if await ctx.latest_turn(conversation_id) is None:
        return None
    return conversation_id


@dataclass(frozen=True)
class SlackUser:
    """The sender facts one users.info read yields: the display fields for the turn's <context>
    tag and the email the DM member resolution needs. The email anchors member identity, so it is
    carried only when Slack has confirmed it (`is_email_confirmed`) — an unconfirmed address is no
    email at all."""

    name: str | None
    email: str | None
    timezone: str | None


async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None:
    """One best-effort users.info read per inbound, feeding both the turn's <context> tag and the
    DM member link. It shares the ambient fetch's short timeout so ingest answers inside Slack's
    event ack; a failed read logs and returns None — the turn is admitted without sender context,
    and only an unlinked DM (which cannot resolve its member) fails loud instead."""
    try:
        async with httpx.AsyncClient(timeout=AMBIENT_FETCH_TIMEOUT_SECONDS) as client:
            payload = await _slack_ok(
                client.get(
                    SLACK_USERS_INFO_URL,
                    params={"user": slack_user_id},
                    headers={"Authorization": f"Bearer {bot_token}"},
                )
            )
    except Exception as error:
        _LOG.warning("slack users.info failed for %s: %s", slack_user_id, error)
        return None
    user = payload.get("user")
    if not isinstance(user, dict):
        return None
    profile = user.get("profile")
    email = profile.get("email") if isinstance(profile, dict) else None
    if user.get("is_email_confirmed") is not True:
        email = None
    name = user.get("real_name") or user.get("name")
    timezone = user.get("tz")
    return SlackUser(
        name=name if isinstance(name, str) and name else None,
        email=email.strip() if isinstance(email, str) and email.strip() else None,
        timezone=timezone if isinstance(timezone, str) and timezone else None,
    )


def _turn_context(sender: SlackUser | None) -> TurnContext:
    """The admitted turn's ambient context from the sender read; a timezone Slack reports that is
    not a known zone is dropped with a log rather than failing the member's message."""
    if sender is None:
        return TurnContext()
    line = (
        f"{sender.name} ({sender.email})"
        if sender.name and sender.email
        else sender.name or sender.email
    )
    try:
        return TurnContext(sender=line, timezone=sender.timezone)
    except ValidationError:
        _LOG.warning("slack timezone %r is not a known zone; dropped", sender.timezone)
        return TurnContext(sender=line)


async def _resolve_member(
    ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None
) -> UUID | None:
    """The speaker's member: the already-linked identity, else what their Slack-confirmed email
    resolves — an existing member links, and a same-domain email joins them as a new member, so
    only the owner ever onboards through the CLI and teammates become members on first contact.
    No confirmed email leaves the turn without a speaker."""
    linked = await ctx.linked_member(slack_user_id)
    if linked is not None:
        return linked
    if sender is None:
        if is_dm:
            raise SlackApiError(
                f"users.info unavailable; cannot resolve the DM member {slack_user_id}"
            )
        return None
    if sender.email is None:
        return None
    return await ctx.join_member(slack_user_id, sender.email)


async def _ambient_context(
    ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity
) -> str:
    """A digest of the ambient messages a conversation-starting turn cannot have in its transcript —
    the traffic from before the agent was addressed. A first mid-thread mention reads the whole
    thread (unbounded above, so a reply racing this very ingest rides the digest instead of
    vanishing — the trigger itself carries the mention and is dropped); a top-level mention reads
    the channel's recent messages as context for its fresh thread. Only the conversation-starting
    turn fetches: once the conversation holds a turn, every member reply is admitted as its own
    turn, so the transcript holds the thread and a refetch would only duplicate it. One bounded
    page — a thread past the page limit keeps its earliest page, the root anchor, and drops the
    overflow. Best-effort by design with its own short timeout, so ingest answers inside Slack's
    three-second event ack — a failed or slow fetch logs and the mention is admitted with its
    plain body."""
    if inbound.is_dm or inbound.conversation_id is not None:
        return ""
    channel, _, root_ts = inbound.queue_key.partition(":")
    trigger_ts = inbound.message_id.partition(":")[2]
    if root_ts == trigger_ts:
        url = SLACK_CONVERSATIONS_HISTORY_URL
        header = AMBIENT_CHANNEL_HEADER
        params: dict[str, str | int] = {
            "channel": channel,
            "latest": trigger_ts,
            "inclusive": "false",
            "limit": AMBIENT_CHANNEL_FETCH_LIMIT,
        }
    else:
        url = SLACK_CONVERSATIONS_REPLIES_URL
        header = AMBIENT_THREAD_HEADER
        params = {
            "channel": channel,
            "ts": root_ts,
            "limit": AMBIENT_FETCH_LIMIT,
        }
    bot_user_id = identity.bot_user_id
    try:
        async with httpx.AsyncClient(timeout=AMBIENT_FETCH_TIMEOUT_SECONDS) as client:
            payload = await _slack_ok(
                client.get(url, params=params, headers={"Authorization": f"Bearer {bot_token}"})
            )
    except Exception as error:
        _LOG.warning("slack ambient context fetch failed for %s: %s", inbound.queue_key, error)
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    return _ambient_digest(messages, bot_user_id, header)


def _ambient_digest(messages: list[object], bot_user_id: str, header: str) -> str:
    """Fetched Slack messages rendered as bounded context lines: member messages only, the bot's
    own replies and any bot-mentioning message dropped — every mention was gated in as its own turn,
    so it already lives in the transcript. Over the digest cap, the oldest line (the thread root,
    the "summarize this" anchor) and the newest lines that fit survive, with the omission marked."""
    kept: list[tuple[float, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("bot_id") is not None or item.get("subtype") not in AMBIENT_MESSAGE_SUBTYPES:
            continue
        user, ts = item.get("user"), item.get("ts")
        text = str(item.get("text") or "").strip()
        if not isinstance(user, str) or not user or user == bot_user_id:
            continue
        if not isinstance(ts, str) or not text or f"<@{bot_user_id}>" in text:
            continue
        try:
            stamp = float(ts)
            minute = datetime.fromtimestamp(stamp, tz=UTC).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OverflowError, OSError):
            continue
        kept.append((stamp, f"[{minute}] <@{user}>: {text[:AMBIENT_MESSAGE_CHAR_LIMIT]}"))
    kept.sort(key=lambda entry: entry[0])
    lines = [line for _, line in kept]
    if not lines:
        return ""
    if sum(len(line) + 1 for line in lines) > AMBIENT_DIGEST_MAX_CHARS:
        budget = AMBIENT_DIGEST_MAX_CHARS - len(lines[0]) - len(AMBIENT_OMITTED_MARKER) - 2
        tail: list[str] = []
        for line in reversed(lines[1:]):
            if budget < len(line) + 1:
                break
            tail.append(line)
            budget -= len(line) + 1
        lines = [lines[0], AMBIENT_OMITTED_MARKER, *reversed(tail)]
    joined = "\n".join(lines)
    return f"{header}\n{joined}\n\n"


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


@dataclass(frozen=True)
class ThreadStatus:
    """Live feedback for one running turn through the thread's native status
    (`assistant.threads.setStatus`): "Thinking…" the moment the turn is admitted, then the turn's
    hub frames — each tool call as the model's own `user_description` when it gave one — cleared
    when the turn ends (the durable reply is the poller's job). The status is state on the thread,
    not a message, and the thread has one writer — the newest turn (`_THREAD_WRITERS`) — so an
    outrun sibling's writes, its clear included, are skipped rather than blanking the status the
    member is watching. Slack drops a status two minutes after its last write, so a quiet stretch
    re-stamps the shown text every STATUS_REFRESH_SECONDS. An update inside
    STATUS_UPDATE_MIN_SECONDS of the last send is dropped, not delayed: the next distinct frame
    refreshes, and the clear ends the status regardless."""

    ctx: SurfaceContext
    turn_id: UUID
    channel: str
    thread_ts: str

    async def run(self) -> None:
        bot_token = await self.ctx.credential(SLACK_BOT_TOKEN_SLOT)
        async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
            await self._set(client, bot_token, STATUS_THINKING_TEXT)
            try:
                await self._follow(client, bot_token)
            finally:
                await self._set(client, bot_token, STATUS_CLEAR_TEXT)

    async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> None:
        if (
            _THREAD_WRITERS.get((self.ctx.workspace_id, self.channel, self.thread_ts))
            != self.turn_id
        ):
            return
        await _slack_ok(
            client.post(
                SLACK_ASSISTANT_STATUS_URL,
                content=json.dumps(
                    {"channel_id": self.channel, "thread_ts": self.thread_ts, "status": status}
                ),
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
        )

    async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None:
        shown = STATUS_THINKING_TEXT
        sent_at = time.monotonic()
        frames = aiter(self.ctx.tail(self.turn_id))
        upcoming = asyncio.ensure_future(anext(frames))
        try:
            while True:
                done, _pending = await asyncio.wait([upcoming], timeout=STATUS_REFRESH_SECONDS)
                if not done:
                    await self._set(client, bot_token, shown)
                    sent_at = time.monotonic()
                    continue
                try:
                    _cursor, frame = upcoming.result()
                except StopAsyncIteration:
                    return
                upcoming = asyncio.ensure_future(anext(frames))
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
                await self._set(client, bot_token, text)
                shown, sent_at = text, time.monotonic()
        finally:
            upcoming.cancel()
            await asyncio.gather(upcoming, return_exceptions=True)


_STATUS_TASKS: dict[UUID, asyncio.Task[None]] = {}
_THREAD_WRITERS: dict[tuple[UUID, str, str], UUID] = {}


def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None:
    """Spawn one ThreadStatus task per admitted turn — Slack redelivers events and admission dedupes
    them to the same turn id, so a redelivery must not double the tail work. The status anchors to
    the conversation's thread — the root in a channel, the member's own message in a DM (a DM
    conversation has no root, and the status API demands a thread) — and the new turn takes over as
    the thread's writer. Best-effort by design: a failure only logs, and the task always ends
    because the tail ends on the durable terminal state."""
    if turn_id in _STATUS_TASKS:
        return
    channel, separator, root_ts = queue_key.partition(":")
    thread = (channel, root_ts if separator else message_ts)
    writer = (ctx.workspace_id, *thread)
    status = ThreadStatus(ctx=ctx, turn_id=turn_id, channel=thread[0], thread_ts=thread[1])
    _THREAD_WRITERS[writer] = turn_id
    task = asyncio.create_task(_run_status(status))
    _STATUS_TASKS[turn_id] = task

    def _untrack(_done: asyncio.Task[None]) -> None:
        _STATUS_TASKS.pop(turn_id, None)
        if _THREAD_WRITERS.get(writer) == turn_id:
            del _THREAD_WRITERS[writer]

    task.add_done_callback(_untrack)


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
    is_dm: bool
    message_ts: str
    message_text: str
    label: str
    response_url: str


@dataclass(frozen=True)
class ConnectClick:
    """A verified click on a terminal connect handoff."""

    slack_user_id: str
    turn_id: UUID
    response_url: str


async def interactive(ctx: SurfaceContext, request: Request) -> Response:
    """Slack interactivity ingest: verify the signed form payload, decode a click on an ask_user
    answer button, admit the answer as the conversation's next turn — idempotent per question
    message, so a double click or a second member's click joins the turn the first click won — and
    rewrite the buttons into the winning answer with who answered. Only the click whose exact body
    the answer key stored (`admitted_body` — the turn it opened or the queue row it landed as)
    rewrites, so a losing click never displays an answer the agent won't see. The rewrite rides
    its own task so the ack beats Slack's three-second budget — Block Kit allows no message in
    the direct response, only the ack."""
    try:
        raw = await _slack_request_body(request)
    except SlackBodyTooLarge:
        return Response("Slack payload too large", status_code=413)
    try:
        signing_secret = await ctx.credential(SLACK_SIGNING_SECRET_SLOT)
    except CredentialSlotUnset:
        return Response("Slack signing secret is not configured yet", status_code=401)
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError as error:
        return Response(str(error), status_code=401)
    identity = await _identity(ctx)
    if identity is None:
        _prove_identity_in_background(ctx)
        return Response("Slack identity is being verified", status_code=503)
    click = _to_click(raw, identity)
    if click is None:
        return JSONResponse({"ok": True, "ignored": True})
    await _mark_url_verified(ctx, signing_secret)
    member_id = await ctx.linked_member(click.slack_user_id)
    match click:
        case ConnectClick():
            if member_id is None:
                text = "This connection request is not available to you."
            else:
                try:
                    url = await ctx.connect_url(click.turn_id, member_id)
                except ConnectRequestInvalid:
                    text = (
                        "This connection request is no longer available. Ask me to connect again."
                    )
                else:
                    text = f"Complete the connection privately: <{url}|Open authorization>"
            _ephemeral_in_background(click.response_url, text)
        case AnswerClick():
            if member_id is None:
                bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
                sender = await _slack_user(bot_token, click.slack_user_id)
                member_id = await _resolve_member(ctx, click.slack_user_id, click.is_dm, sender)
            conversation_id = await ctx.find_conversation(click.queue_key)
            if conversation_id is None:
                return JSONResponse({"ok": True, "ignored": True})
            if click.is_dm and member_id is not None:
                conversation_id = await ctx.conversation_for(click.queue_key, member_id)
            agent_id = await ctx.default_agent()
            body = f"[Answered by <@{click.slack_user_id}> via button] {click.label}"
            answer_key = f"{click.queue_key}:{click.message_ts}:answer"
            turn_id = await ctx.admit(
                conversation_id,
                agent_id,
                body,
                idempotency_key=answer_key,
                speaker_member_id=member_id,
            )
            _track_status(ctx, turn_id, click.queue_key, click.message_ts)
            if await ctx.admitted_body(answer_key) == body:
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


def _ephemeral_in_background(response_url: str, text: str) -> None:
    task = asyncio.create_task(_post_ephemeral(response_url, text))
    _REWRITE_TASKS.add(task)
    task.add_done_callback(_REWRITE_TASKS.discard)


async def _post_ephemeral(response_url: str, text: str) -> None:
    try:
        if not _slack_download_host_ok(response_url):
            raise ValueError("refusing to answer a non-Slack response_url")
        async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
            response = await client.post(
                response_url,
                json={"response_type": "ephemeral", "replace_original": False, "text": text},
            )
        response.raise_for_status()
    except Exception as error:
        _LOG.warning("slack private connect response failed: %s", error)


def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None:
    form = parse_qs(raw.decode())
    encoded = form.get("payload")
    if not encoded:
        raise ValueError("Slack interactive body must carry a payload field")
    payload = json.loads(encoded[0])
    if not isinstance(payload, dict) or payload.get("type") != "block_actions":
        return None
    team = payload.get("team")
    team_id = team.get("id") if isinstance(team, dict) else None
    if team_id != identity.team_id:
        return None
    actions = payload.get("actions")
    action = actions[0] if isinstance(actions, list) and actions else None
    if not isinstance(action, dict):
        return None
    action_id = action.get("action_id")
    value = action.get("value")
    if not isinstance(action_id, str) or not isinstance(value, str) or not value:
        return None
    user_id = _string_field(_dict_field(payload, "user"), "id")
    response_url = _string_field(payload, "response_url")
    if action_id == CONNECT_ACTION_ID:
        try:
            turn_id = UUID(value)
        except ValueError:
            return None
        return ConnectClick(slack_user_id=user_id, turn_id=turn_id, response_url=response_url)
    if not action_id.startswith(ASK_ACTION_ID_PREFIX):
        return None
    channel_id = _string_field(_dict_field(payload, "channel"), "id")
    message = _dict_field(payload, "message")
    thread = message.get("thread_ts")
    return AnswerClick(
        slack_user_id=user_id,
        queue_key=(f"{channel_id}:{thread}" if isinstance(thread, str) and thread else channel_id),
        is_dm=channel_id.startswith("D"),
        message_ts=_string_field(message, "ts"),
        message_text=str(message.get("text") or ""),
        label=value,
        response_url=response_url,
    )


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
    """The reply text, plus the terminal hint when the turn asked for credentials (Slack never
    collects a secret — the member's own terminal does), plus a link block for any shared file too
    large to upload inline — a TTL download link so an over-cap artifact is delivered rather than
    silently dropped."""
    text = _reply_text(writeback)
    if writeback.credential_request is not None:
        text = (
            f"{text}\n\n:lock: {writeback.credential_request.reason} — open your terminal, run "
            "`ufo`, and ask me there to continue; secrets never pass through chat."
        )
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
    """Post the reply to the thread and return its message ref (`channel:ts`), the delivery record.
    An `invalid_blocks` rejection is deterministic, so the reply re-posts once as plain text rather
    than the poller retrying the identical Block Kit body until it ages out."""
    channel, separator, thread_ts = writeback.queue_key.partition(":")
    thread = thread_ts if separator else None
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    text = _reply_with_oversize_links(ctx, writeback)
    answer_actions = slack_answer_actions(writeback.question)
    connect_actions = slack_connect_actions(writeback.connect_request, writeback.turn_id)
    actions = answer_actions or connect_actions
    model = writeback.model or "no-model"
    params = f"-[{writeback.reasoning}]" if writeback.reasoning is not None else ""
    metadata = (
        f"${writeback.cost_micro_usd / 1_000_000:.6f} "
        f"({writeback.tokens:,} tokens, {writeback.cache_percent}% cached) · "
        f"{model}{params}"
    )[:SLACK_CONTEXT_TEXT_LIMIT]
    async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
        payload = await _chat_post(
            client, bot_token, slack_reply_body(channel, thread, text, metadata, actions=actions)
        )
        if payload.get("error") == SLACK_INVALID_BLOCKS_ERROR:
            payload = await _chat_post(
                client,
                bot_token,
                slack_reply_body(
                    channel,
                    thread,
                    text,
                    metadata,
                    blocks=connect_actions is not None,
                    actions=connect_actions,
                    sections=connect_actions is not None,
                ),
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
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            error_payload = response.json()
        except ValueError:
            error_code = None
        else:
            match error_payload:
                case {"error": str() as value} if value:
                    error_code = value
                case _:
                    error_code = None
        retry_after_seconds = None
        retry_after = response.headers.get("retry-after")
        if response.status_code == 429 and retry_after is not None and retry_after.isdecimal():
            try:
                retry_after_seconds = min(int(retry_after), SLACK_RETRY_AFTER_MAX_SECONDS)
            except ValueError:
                retry_after_seconds = SLACK_RETRY_AFTER_MAX_SECONDS
        error_suffix = f": {error_code}" if error_code is not None else ""
        raise SurfaceDeliveryError(
            f"chat.postMessage HTTP {response.status_code}{error_suffix}",
            http_status=response.status_code,
            retry_after_seconds=retry_after_seconds,
        ) from error
    return response.json()


async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None:
    """Stream each shared file that fits the upload cap into the conversation, all at once on the
    event loop; an over-cap file is delivered as a link in `post`, not here. The upload targets the
    queue key — the member's thread in a channel, the channel itself in a DM — because Slack forbids
    threading on a reply's ts, and the bot reply is itself a thread reply in a channel. Best effort:
    a rejected file is logged and the rest still deliver, so an upload never re-posts the reply or
    blocks its siblings."""
    inline = tuple(a for a in writeback.artifacts if a.size_bytes <= SLACK_UPLOAD_MAX_BYTES)
    if not inline:
        return
    channel, separator, thread_ts = writeback.queue_key.partition(":")
    thread = thread_ts if separator else None
    bot_token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    results = await asyncio.gather(
        *(_upload_artifact(ctx, bot_token, channel, thread, artifact) for artifact in inline),
        return_exceptions=True,
    )
    for artifact, result in zip(inline, results, strict=True):
        if isinstance(result, BaseException):
            _LOG.warning("slack attachment upload failed for %s: %s", artifact.filename, result)


async def _upload_artifact(
    ctx: SurfaceContext,
    bot_token: str,
    channel: str,
    thread_ts: str | None,
    artifact: SharedArtifact,
) -> None:
    """The three-step external upload, streamed: reserve an upload URL for the exact byte length,
    POST the blob's bytes to it (streamed from the blob store, never buffered), then complete the
    upload into the channel or parent thread with the caption or the plain filename as its title."""
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
                        **({"thread_ts": thread_ts} if thread_ts is not None else {}),
                    }
                ),
            )
        )


async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]:
    response = await request
    response.raise_for_status()
    payload = response.json()
    if payload.get("ok") is not True:
        raise SlackApiError(f"{response.url.path}: {payload.get('error')}")
    return payload
