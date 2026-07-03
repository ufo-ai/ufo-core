"""The Slack surface: verify an inbound event, key it to a thread conversation, and admit a turn.

A Slack mention fans out as several deliveries (`app_mention`, `message`, and retries) that all
carry the same `channel:ts`; that pair is the admission idempotency key, so one mention becomes
exactly one turn. A channel thread is shared by construction (`member_id` NULL); a DM resolves to
the member whose Slack email matches, linking a `surface_identity` the first time they speak."""

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from selfhost.config import SlackSurfaceConfig
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME, TerminalFrame
from selfhost.surfaces.admission import Admission

SURFACE_SLACK = "slack"
SLACK_BOT_TOKEN_SLOT = "slack_bot_token"
SLACK_SIGNING_SECRET_SLOT = "slack_signing_secret"
SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_REPLAY_SECONDS = 300
MAX_SLACK_EVENT_BYTES = 1_000_000
MESSAGE_EVENT_TYPES = ("app_mention", "message")

WRITEBACK_PENDING = "pending"
WRITEBACK_CLAIMED = "claimed"
WRITEBACK_DELIVERED = "delivered"
WRITEBACK_FAILED = "failed"
TURN_DONE = "done"
SLACK_MARKDOWN_TEXT_LIMIT = 12_000
MAX_SLACK_MESSAGE_BYTES = 40_000
MAX_WRITEBACK_ERROR_CHARS = 2_048
WRITEBACK_POLL_SECONDS = 1.0
WRITEBACK_CLAIM_SECONDS = 300
WRITEBACK_CLAIM_BATCH = 16

router = APIRouter()


class SlackSignatureError(Exception):
    """The request's Slack signature is missing, stale, or does not match the signing secret."""


class SlackApiError(RuntimeError):
    """A Slack API call returned `ok: false`."""


class Inbound(BaseModel):
    """A verified, gated Slack message ready for admission — the untrusted event reduced to exactly
    what the turn queue and member resolution need."""

    slack_user_id: str
    queue_key: str
    message_id: str
    is_dm: bool
    body: str


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


def slack_message_id(channel: str, ts: str) -> str:
    """The message identity every delivery of one message shares — NOT the event id, which differs
    across the `app_mention`/`message` fan-out and retries."""
    return f"{channel}:{ts}"


def slack_message_gated(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool:
    """Whether the agent should answer: always in a DM or an explicit mention, and in a channel only
    when the bot is @-mentioned — never on every passing message."""
    if event.get("type") == "app_mention" or is_dm:
        return True
    return f"<@{bot_user_id}>" in str(event.get("text") or "")


def _string_field(event: Mapping[str, object], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Slack event field {field!r} is required")
    return value


def slack_reply_body(channel: str, thread_ts: str | None, text: str) -> bytes:
    """The chat.postMessage body: one Block Kit `markdown` block so Slack renders the agent's own
    markdown natively, degrading to a text-only body when the reply exceeds Slack's block-character
    or payload-byte caps. `text` always carries the whole reply as the notification fallback."""
    if not text:
        raise ValueError("Slack reply text is required")
    base: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts is not None:
        base["thread_ts"] = thread_ts
    if len(text) <= SLACK_MARKDOWN_TEXT_LIMIT:
        with_blocks = {**base, "blocks": [{"type": "markdown", "text": text}]}
        encoded = json.dumps(with_blocks, separators=(",", ":")).encode()
        if len(encoded) <= MAX_SLACK_MESSAGE_BYTES:
            return encoded
    encoded = json.dumps(base, separators=(",", ":")).encode()
    if len(encoded) > MAX_SLACK_MESSAGE_BYTES:
        raise ValueError("Slack reply text is too large")
    return encoded


@dataclass(frozen=True)
class SlackSurface:
    admission: Admission
    credentials: CredentialStore
    http: httpx.AsyncClient
    config: SlackSurfaceConfig
    workspace_id: UUID

    async def ingest(self, request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_SLACK_EVENT_BYTES:
            raise HTTPException(413, "Slack event too large")
        secret = await self.credentials.get(self.workspace_id, SLACK_SIGNING_SECRET_SLOT)
        try:
            verify_slack_signature(request.headers, raw, secret)
        except SlackSignatureError as error:
            raise HTTPException(401, str(error)) from error
        challenge = url_verification_challenge(raw)
        if challenge is not None:
            return JSONResponse({"challenge": challenge})
        inbound = self._to_inbound(raw)
        if inbound is None:
            return JSONResponse({"ok": True, "ignored": True})
        conversation_id = await self._conversation_for(inbound)
        agent_id = await self._default_agent()
        turn_id = await self.admission.admit(
            self.workspace_id,
            conversation_id,
            agent_id,
            inbound.body,
            idempotency_key=inbound.message_id,
        )
        await self._enqueue_writeback(turn_id)
        return JSONResponse({"ok": True})

    async def _enqueue_writeback(self, turn_id: UUID) -> None:
        """Register the turn for delivery. A redelivery deduped to the existing turn hits the
        primary key; the poller already owns that writeback, so the collision is dropped."""
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.writeback).values(
                        turn_id=turn_id,
                        workspace_id=self.workspace_id,
                        status=WRITEBACK_PENDING,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("slack.writeback_exists", turn_id=str(turn_id))

    def _to_inbound(self, raw: bytes) -> Inbound | None:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Slack body must be an object")
        team = payload.get("team_id")
        if team != self.config.team_id:
            raise HTTPException(403, "Slack team does not match this deploy")
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") not in MESSAGE_EVENT_TYPES:
            return None
        if event.get("bot_id") is not None or event.get("subtype") is not None:
            return None
        user = event.get("user")
        if not isinstance(user, str) or not user or user == self.config.bot_user_id:
            return None
        is_dm = event.get("channel_type") == "im"
        if not slack_message_gated(event, self.config.bot_user_id, is_dm):
            return None
        channel = _string_field(event, "channel")
        ts = _string_field(event, "ts")
        root = event.get("thread_ts")
        root_ts = root if isinstance(root, str) and root else ts
        return Inbound(
            slack_user_id=user,
            queue_key=slack_thread_key(channel, root_ts, is_dm),
            message_id=slack_message_id(channel, ts),
            is_dm=is_dm,
            body=str(event.get("text") or ""),
        )

    async def _conversation_for(self, inbound: Inbound) -> UUID:
        member_id = await self._resolve_member(inbound.slack_user_id) if inbound.is_dm else None
        lookup = sa.select(tables.conversation.c.id).where(
            tables.conversation.c.workspace_id == self.workspace_id,
            tables.conversation.c.surface == SURFACE_SLACK,
            tables.conversation.c.queue_key == inbound.queue_key,
        )
        async with workspace_tx() as connection:
            found = (await connection.execute(lookup)).one_or_none()
        if found is not None:
            return found.id
        conversation_id = uuid4()
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
                        surface=SURFACE_SLACK,
                        queue_key=inbound.queue_key,
                        member_id=member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("slack.conversation_create_lost_race", queue_key=inbound.queue_key)
            async with workspace_tx() as connection:
                return (await connection.execute(lookup)).one().id
        return conversation_id

    async def _resolve_member(self, slack_user_id: str) -> UUID | None:
        async with workspace_tx() as connection:
            linked = (
                await connection.execute(
                    sa.select(tables.surface_identity.c.member_id).where(
                        tables.surface_identity.c.workspace_id == self.workspace_id,
                        tables.surface_identity.c.surface == SURFACE_SLACK,
                        tables.surface_identity.c.external_id == slack_user_id,
                    )
                )
            ).one_or_none()
        if linked is not None:
            return linked.member_id
        email = await self._slack_user_email(slack_user_id)
        if email is None:
            return None
        async with workspace_tx() as connection:
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == self.workspace_id,
                        sa.func.lower(tables.member.c.email) == email.lower(),
                    )
                )
            ).one_or_none()
        if member is None:
            return None
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=self.workspace_id,
                        member_id=member.id,
                        surface=SURFACE_SLACK,
                        external_id=slack_user_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("slack.identity_link_race", slack_user_id=slack_user_id)
        return member.id

    async def _slack_user_email(self, slack_user_id: str) -> str | None:
        token = await self.credentials.get(self.workspace_id, SLACK_BOT_TOKEN_SLOT)
        response = await self.http.get(
            SLACK_USERS_INFO_URL,
            params={"user": slack_user_id},
            headers={"Authorization": f"Bearer {token}"},
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

    async def _default_agent(self) -> UUID:
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.name == DEFAULT_AGENT_NAME,
                    )
                )
            ).one_or_none()
        if agent is None:
            raise RuntimeError(f"no agent named {DEFAULT_AGENT_NAME!r} for the Slack surface")
        return agent.id


@dataclass(frozen=True)
class WritebackPoller:
    """Durable delivery of Slack replies. The hub is lossy, so the reply is never posted from a live
    Terminal frame: this poller claims writebacks whose turn is done, posts the Block Kit reply to
    the thread with the bot token (in-process, not through the sandbox proxy), records the message
    ts, then marks delivered. A claim (worker id + expiry) makes it safe under `serve`'s concurrent
    instances — Postgres skips a peer's locked rows, SQLite's single writer serializes them — and a
    compare-and-swap on the claim owner means only the worker still holding a claim finalizes it."""

    credentials: CredentialStore
    http: httpx.AsyncClient
    workspace_id: UUID
    worker_id: str

    async def run(self) -> None:
        while True:
            try:
                await self.drain()
            except Exception as error:
                log("slack.writeback_drain_failed", error_class=type(error).__name__)
            await asyncio.sleep(WRITEBACK_POLL_SECONDS)

    async def drain(self) -> None:
        for row in await self._claim():
            if row.last_error is not None:
                log("slack.writeback_retry", turn_id=str(row.turn_id), last_error=row.last_error)
            await self._deliver(row.turn_id, row.reply_ref)

    async def _claim(self) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.writeback.c.turn_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(
                tables.writeback.c.workspace_id == self.workspace_id,
                tables.writeback.c.status.in_((WRITEBACK_PENDING, WRITEBACK_FAILED)),
                tables.turn.c.status == TURN_DONE,
                sa.or_(
                    tables.writeback.c.claim_expires_at.is_(None),
                    tables.writeback.c.claim_expires_at <= now,
                ),
            )
            .order_by(tables.writeback.c.created_at)
            .limit(WRITEBACK_CLAIM_BATCH)
            .with_for_update(skip_locked=True, of=tables.writeback)
            .cte("claimable")
        )
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.update(tables.writeback)
                    .where(tables.writeback.c.turn_id == claimable.c.turn_id)
                    .values(
                        status=WRITEBACK_CLAIMED,
                        claimed_by=self.worker_id,
                        claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.writeback.c.turn_id,
                        tables.writeback.c.reply_ref,
                        tables.writeback.c.last_error,
                    )
                )
            ).all()

    async def _deliver(self, turn_id: UUID, reply_ref: str | None) -> None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal, tables.conversation.c.queue_key)
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == turn_id)
                )
            ).one()
        channel, separator, thread_ts = row.queue_key.partition(":")
        text = TerminalFrame.model_validate(row.terminal).text
        try:
            if reply_ref is None:
                reply_ref = await self._post(channel, thread_ts if separator else None, text)
                await self._record_ref(turn_id, reply_ref)
            await self._finalize(turn_id, WRITEBACK_DELIVERED, None)
        except Exception as error:
            log(
                "slack.writeback_post_failed",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )
            await self._finalize(turn_id, WRITEBACK_FAILED, str(error)[:MAX_WRITEBACK_ERROR_CHARS])

    async def _post(self, channel: str, thread_ts: str | None, text: str) -> str:
        token = await self.credentials.get(self.workspace_id, SLACK_BOT_TOKEN_SLOT)
        response = await self.http.post(
            SLACK_CHAT_POST_MESSAGE_URL,
            content=slack_reply_body(channel, thread_ts, text),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise SlackApiError(str(payload.get("error")))
        ts = payload.get("ts")
        if not isinstance(ts, str) or not ts:
            raise SlackApiError("Slack response missing ts")
        return ts

    async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(reply_ref=reply_ref, updated_at=sa.func.now())
            )

    async def _finalize(self, turn_id: UUID, status: str, last_error: str | None) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    status=status,
                    last_error=last_error,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    surface: SlackSurface = request.app.state.slack
    return await surface.ingest(request)
