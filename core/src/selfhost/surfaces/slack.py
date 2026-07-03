"""The Slack surface: verify an inbound event, key it to a thread conversation, and admit a turn.

A Slack mention fans out as several deliveries (`app_mention`, `message`, and retries) that all
carry the same `channel:ts`; that pair is the admission idempotency key, so one mention becomes
exactly one turn. A channel thread is shared by construction (`member_id` NULL); a DM resolves to
the member whose Slack email matches, linking a `surface_identity` the first time they speak."""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
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
from selfhost.schema.records import DEFAULT_AGENT_NAME
from selfhost.surfaces.admission import Admission

SURFACE_SLACK = "slack"
SLACK_BOT_TOKEN_SLOT = "slack_bot_token"
SLACK_SIGNING_SECRET_SLOT = "slack_signing_secret"
SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_REPLAY_SECONDS = 300
MAX_SLACK_EVENT_BYTES = 1_000_000
MESSAGE_EVENT_TYPES = ("app_mention", "message")

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
        await self.admission.admit(
            self.workspace_id,
            conversation_id,
            agent_id,
            inbound.body,
            idempotency_key=inbound.message_id,
        )
        return JSONResponse({"ok": True})

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


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    surface: SlackSurface = request.app.state.slack
    return await surface.ingest(request)
