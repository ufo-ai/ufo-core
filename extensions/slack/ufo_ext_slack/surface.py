"""The Slack surface on the core surface seam: verify an inbound event, key it to a thread
conversation, stream any attached files into the workspace, and admit a turn; then deliver the
terminal reply and stream the turn's shared files into that reply's thread.

Everything Slack-specific lives here — signature verification, thread keying, Block Kit rendering,
the chunked external-upload flow, the `url_private` download — reaching core only through the
privileged `SurfaceContext` (admit, identity, workspace write, credential read) and the streaming
`BlobStore`. Attachments move without ever buffering a whole file: an inbound file streams from
`url_private` straight into the workspace before the turn runs, and a shared file streams from the
blob store straight to Slack's external-upload URL. Uploads fan out with `asyncio.gather` on the one
event loop — never a thread pool."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import httpx

from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.surfaces import SharedArtifact, SurfaceContext, Writeback

SURFACE_SLACK = "slack"
SLACK_BOT_TOKEN_SLOT = "slack_bot_token"
SLACK_SIGNING_SECRET_SLOT = "slack_signing_secret"
SLACK_BOT_USER_ID_SLOT = "slack_bot_user_id"
SLACK_TEAM_ID_SLOT = "slack_team_id"

SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_FILES_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
SLACK_FILES_COMPLETE_UPLOAD = "https://slack.com/api/files.completeUploadExternal"

SLACK_REPLAY_SECONDS = 300
MAX_SLACK_EVENT_BYTES = 1_000_000
MESSAGE_EVENT_TYPES = ("app_mention", "message")
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


def slack_reply_body(channel: str, thread_ts: str | None, text: str, blocks: bool = True) -> bytes:
    """The chat.postMessage body: one Block Kit `markdown` block so Slack renders the agent's own
    markdown natively, degrading to a text-only body when the reply exceeds Slack's block-character
    or payload-byte caps, or when `blocks=False` forces plain text after Slack rejects the blocks as
    `invalid_blocks`. `text` always carries the whole reply as the notification fallback."""
    if not text:
        raise ValueError("Slack reply text is required")
    base: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts is not None:
        base["thread_ts"] = thread_ts
    if blocks and len(text) <= SLACK_MARKDOWN_TEXT_LIMIT:
        with_blocks = {**base, "blocks": [{"type": "markdown", "text": text}]}
        encoded = json.dumps(with_blocks, separators=(",", ":")).encode()
        if len(encoded) <= MAX_SLACK_MESSAGE_BYTES:
            return encoded
    encoded = json.dumps(base, separators=(",", ":")).encode()
    if len(encoded) > MAX_SLACK_MESSAGE_BYTES:
        raise ValueError("Slack reply text is too large")
    return encoded


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
    signing_secret = await ctx.credential(SLACK_SIGNING_SECRET_SLOT)
    try:
        verify_slack_signature(request.headers, raw, signing_secret)
    except SlackSignatureError as error:
        return Response(str(error), status_code=401)
    challenge = url_verification_challenge(raw)
    if challenge is not None:
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
    await ctx.admit(conversation_id, agent_id, body, idempotency_key=inbound.message_id)
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
    if event.get("bot_id") is not None or event.get("subtype") is not None:
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
    async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
        payload = await _chat_post(client, bot_token, slack_reply_body(channel, thread, text))
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
