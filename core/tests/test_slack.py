import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selfhost.config import SlackSurfaceConfig
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.memory.service import SHARED_SUBJECT, member_subject, recall_subjects
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame
from selfhost.surfaces.admission import Admission
from selfhost.surfaces.slack import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_CHAT_POST_MESSAGE_URL,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    WRITEBACK_DELIVERED,
    WRITEBACK_PENDING,
    SlackSignatureError,
    SlackSurface,
    WritebackPoller,
    slack_message_id,
    slack_thread_key,
    url_verification_challenge,
    verify_slack_signature,
)
from selfhost.surfaces.slack import router as slack_router

TEAM_ID = "T0000001"
BOT_USER_ID = "UBOT00000"
SIGNING_SECRET = "signing-secret"
BOT_TOKEN = "xoxb-test"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _sign(body: bytes, timestamp: int) -> dict[str, str]:
    base = b"v0:" + str(timestamp).encode() + b":" + body
    signature = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {
        "x-slack-request-timestamp": str(timestamp),
        "x-slack-signature": signature,
    }


def _event_body(**event: object) -> bytes:
    return json.dumps({"team_id": TEAM_ID, "event": event}).encode()


def _users_info_transport(email_by_user: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        user = request.url.params.get("user")
        email = email_by_user.get(str(user))
        if email is None:
            return httpx.Response(200, json={"ok": True, "user": {"profile": {}}})
        return httpx.Response(200, json={"ok": True, "user": {"profile": {"email": email}}})

    return httpx.MockTransport(handler)


async def _seed(*, member_email: str | None = None) -> tuple[UUID, UUID | None]:
    workspace_id, agent_id = uuid4(), uuid4()
    member_id = uuid4() if member_email is not None else None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if member_email is not None:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=member_email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, member_id


async def _surface_client(
    workspace_id: UUID, email_by_user: dict[str, str]
) -> tuple[AsyncClient, StubDbos]:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, SIGNING_SECRET)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, BOT_TOKEN)
    dbos = StubDbos()
    app = FastAPI()
    app.state.slack = SlackSurface(
        admission=Admission(dbos=dbos),
        credentials=store,
        http=AsyncClient(transport=_users_info_transport(email_by_user)),
        config=SlackSurfaceConfig(enable=True, team_id=TEAM_ID, bot_user_id=BOT_USER_ID),
        workspace_id=workspace_id,
    )
    app.include_router(slack_router)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://slack")
    return client, dbos


async def _post(client: AsyncClient, body: bytes) -> httpx.Response:
    return await client.post("/slack/events", content=body, headers=_sign(body, int(time.time())))


async def _conversation(workspace_id: UUID, queue_key: str) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.id, tables.conversation.c.member_id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key == queue_key,
                )
            )
        ).one()


def test_signature_verify_accepts_valid_and_rejects_tampered() -> None:
    body = b'{"type":"event_callback"}'
    now = int(time.time())
    headers = _sign(body, now)
    verify_slack_signature(headers, body, SIGNING_SECRET, now=now)
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(headers, body + b"x", SIGNING_SECRET, now=now)
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(headers, body, "wrong-secret", now=now)


def test_url_verification_returns_challenge() -> None:
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()
    assert url_verification_challenge(body) == "c-123"
    assert url_verification_challenge(json.dumps({"type": "event_callback"}).encode()) is None


def test_message_identity_shares_channel_ts_but_threads_by_root() -> None:
    assert slack_message_id("C1", "100.5") == "C1:100.5"
    assert slack_thread_key("C1", "100.5", is_dm=False) == "C1:100.5"
    assert slack_thread_key("C1", "50.0", is_dm=False) == "C1:50.0"
    assert slack_thread_key("D1", "100.5", is_dm=True) == "D1"


async def test_bad_signature_is_rejected(db: None) -> None:
    workspace_id, _ = await _seed()
    client, _ = await _surface_client(workspace_id, {})
    body = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="<@UBOT00000>")
    bad = {"x-slack-request-timestamp": str(int(time.time())), "x-slack-signature": "v0=bad"}
    async with client:
        response = await client.post("/slack/events", content=body, headers=bad)
    assert response.status_code == 401


async def test_challenge_handshake_over_http(db: None) -> None:
    workspace_id, _ = await _seed()
    client, _ = await _surface_client(workspace_id, {})
    body = json.dumps({"type": "url_verification", "challenge": "handshake"}).encode()
    async with client:
        response = await _post(client, body)
    assert response.status_code == 200
    assert response.json() == {"challenge": "handshake"}


async def test_one_mention_fans_out_to_exactly_one_turn(db: None) -> None:
    workspace_id, _ = await _seed()
    client, dbos = await _surface_client(workspace_id, {})
    mention = "<@UBOT00000> hi"
    deliveries = [
        _event_body(type="app_mention", user="U1", channel="C1", ts="100.5", text=mention),
        _event_body(type="message", user="U1", channel="C1", ts="100.5", text=mention),
        _event_body(type="app_mention", user="U1", channel="C1", ts="100.5", text=mention),
    ]
    async with client:
        for body in deliveries:
            assert (await _post(client, body)).status_code == 200
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.idempotency_key).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert len(turns) == 1
    assert turns[0].idempotency_key == "C1:100.5"
    assert dbos.enqueued == [str(turns[0].id)]
    conversation = await _conversation(workspace_id, "C1:100.5")
    assert conversation.member_id is None


def _recording_post_transport(posted: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})

    return httpx.MockTransport(handler)


async def _seed_slack_turn(workspace_id: UUID, queue_key: str, status: str, text: str) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = TerminalFrame(status=status, text=text).model_dump(mode="json") if text else None
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface=SURFACE_SLACK,
                queue_key=queue_key,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="ask",
                terminal=terminal,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.writeback).values(
                turn_id=turn_id,
                workspace_id=workspace_id,
                status=WRITEBACK_PENDING,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _writeback(turn_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.writeback.c.status, tables.writeback.c.reply_ref).where(
                    tables.writeback.c.turn_id == turn_id
                )
            )
        ).one()


async def test_writeback_poller_posts_block_kit_reply_to_thread(db: None) -> None:
    workspace_id, _ = await _seed()
    turn_id = await _seed_slack_turn(workspace_id, "C5:200.0", "done", "hi **there**")
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, BOT_TOKEN)
    posted: list[httpx.Request] = []
    poller = WritebackPoller(
        credentials=store,
        http=AsyncClient(transport=_recording_post_transport(posted)),
        workspace_id=workspace_id,
        worker_id="worker-1",
    )
    async with poller.http:
        await poller.drain()
    assert len(posted) == 1
    assert str(posted[0].url) == SLACK_CHAT_POST_MESSAGE_URL
    assert posted[0].headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    body = json.loads(posted[0].content)
    assert body["channel"] == "C5"
    assert body["thread_ts"] == "200.0"
    assert body["blocks"] == [{"type": "markdown", "text": "hi **there**"}]
    delivered = await _writeback(turn_id)
    assert delivered.status == WRITEBACK_DELIVERED
    assert delivered.reply_ref == "999.100"


async def test_writeback_poller_leaves_a_pending_turn_undelivered(db: None) -> None:
    workspace_id, _ = await _seed()
    turn_id = await _seed_slack_turn(workspace_id, "C6:1.0", "queued", "")
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, BOT_TOKEN)
    posted: list[httpx.Request] = []
    poller = WritebackPoller(
        credentials=store,
        http=AsyncClient(transport=_recording_post_transport(posted)),
        workspace_id=workspace_id,
        worker_id="worker-1",
    )
    async with poller.http:
        await poller.drain()
    assert posted == []
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING


async def test_dm_links_member_while_channel_thread_is_shared(db: None) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    client, _ = await _surface_client(workspace_id, {"UBEE": "bee@example.com"})
    dm = _event_body(
        type="message", channel_type="im", user="UBEE", channel="D9", ts="7.0", text="hey"
    )
    channel = _event_body(
        type="app_mention", user="UBEE", channel="C9", ts="8.0", text="<@UBOT00000> yo"
    )
    async with client:
        assert (await _post(client, dm)).status_code == 200
        assert (await _post(client, channel)).status_code == 200
    dm_conversation = await _conversation(workspace_id, "D9")
    channel_conversation = await _conversation(workspace_id, "C9:8.0")
    assert dm_conversation.member_id == member_id
    assert channel_conversation.member_id is None
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == SURFACE_SLACK,
                    tables.surface_identity.c.external_id == "UBEE",
                )
            )
        ).one()
    assert linked.member_id == member_id
    assert member_subject(member_id) in recall_subjects(member_id)
    assert recall_subjects(None) == frozenset({SHARED_SUBJECT})
