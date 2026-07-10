"""The Slack extension end to end on the surface seam: signature verification and Block Kit
rendering as pure functions, then the real handlers through mounted ingest and the core writeback
poller — inbound files streamed into the workspace, the reply posted as a Block Kit message, and a
shared file streamed to Slack's chunked external-upload API. Slack's HTTP is a MockTransport (a
dependency stand-in); every assertion reads the durable rows and blobs core wrote, or the exact
requests the handlers emitted."""

import asyncio
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_slack.surface as slack
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_slack.manifest import manifest as slack_manifest

import ufo.surfaces.hub_tail as hub_tail
from ufo.artifact_token import verify_artifact_token
from ufo.blob import BlobNotFound, FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.ext.surface import WRITEBACK_DELIVERED, workspace_key
from ufo.hub import InProcessHub, Terminal, ToolCall
from ufo.schema import tables
from ufo.schema.records import (
    WRITEBACK_PENDING,
    AskQuestion,
    AskUserInput,
    QuestionOption,
    TerminalFrame,
)
from ufo.serve import _mount_surfaces

TEAM_ID = "T0000001"
BOT_USER_ID = "UBOT00000"
SIGNING_SECRET = "signing-secret"
BOT_TOKEN = "xoxb-test"
UPLOAD_URL = "https://files.slack.com/upload/session-1"
RESPONSE_URL = "https://hooks.slack.com/actions/T0000001/123/abc"
ARTIFACT_SECRET = "artifact-token-secret"
PUBLIC_BASE_URL = "https://ufo.example.test"

REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
async def _settle_status_tasks(db: None):
    """A status task lives as long as its turn, and no turn ever terminates under the stubbed
    queue — settle them so no task outlives its test. Layered UNDER the test's own patches: a
    fallback Slack transport (a task ending after the test's mock is undone must never dial the
    real API) and a fast durable poll; teardown marks the tracked turns cancelled and waits for
    each task to end on that durable state — ended, not cancelled, so no query is abandoned
    mid-flight."""
    patch = pytest.MonkeyPatch()
    fallback = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True, "channel": "C0", "ts": "0.0"})
    )

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_ASYNC_CLIENT(transport=fallback, **kwargs)

    patch.setattr(slack.httpx, "AsyncClient", factory)
    patch.setattr(hub_tail, "TERMINAL_POLL_SECONDS", 0.05)
    try:
        yield
        await asyncio.gather(*slack._REWRITE_TASKS, return_exceptions=True)
        tasks = dict(slack._STATUS_TASKS)
        if tasks:
            frame = TerminalFrame(status="cancelled").model_dump(mode="json")
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .where(tables.turn.c.id.in_(list(tasks)))
                    .values(status="cancelled", terminal=frame, updated_at=sa.func.now())
                )
            await asyncio.wait_for(
                asyncio.gather(*tasks.values(), return_exceptions=True), timeout=10
            )
        slack._STATUS_TASKS.clear()
        slack._THREAD_WRITERS.clear()
    finally:
        patch.undo()


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _mock_transport(recorder: list[httpx.Request], users: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_USERS_INFO_URL:
            email = users.get(str(request.url.params.get("user")))
            user: dict[str, object] = {"profile": {"email": email} if email else {}}
            if email:
                user |= {"real_name": "Bee Jones", "tz": "America/New_York"}
            return httpx.Response(200, json={"ok": True, "user": user})
        if url in (slack.SLACK_CONVERSATIONS_REPLIES_URL, slack.SLACK_CONVERSATIONS_HISTORY_URL):
            return httpx.Response(200, json={"ok": True, "messages": []})
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})
        if url == slack.SLACK_ASSISTANT_STATUS_URL:
            return httpx.Response(200, json={"ok": True})
        if url == RESPONSE_URL:
            return httpx.Response(200, text="ok")
        if url == slack.SLACK_FILES_GET_UPLOAD_URL:
            return httpx.Response(200, json={"ok": True, "upload_url": UPLOAD_URL, "file_id": "F1"})
        if url == UPLOAD_URL:
            return httpx.Response(200, text="OK")
        if url == slack.SLACK_FILES_COMPLETE_UPLOAD:
            return httpx.Response(200, json={"ok": True, "files": [{"id": "F1"}]})
        if url.startswith("https://files.slack.com/files-pri/"):
            return httpx.Response(200, content=b"INBOUND-BYTES")
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(slack.httpx, "AsyncClient", factory)


async def _store(workspace_id: UUID) -> CredentialStore:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, slack.SLACK_SIGNING_SECRET_SLOT, SIGNING_SECRET)
    await store.put(workspace_id, slack.SLACK_BOT_TOKEN_SLOT, BOT_TOKEN)
    await store.put(workspace_id, slack.SLACK_BOT_USER_ID_SLOT, BOT_USER_ID)
    await store.put(workspace_id, slack.SLACK_TEAM_ID_SLOT, TEAM_ID)
    return store


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


def _sign(body: bytes, ts: int) -> dict[str, str]:
    base = b"v0:" + str(ts).encode() + b":" + body
    signature = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {"x-slack-request-timestamp": str(ts), "x-slack-signature": signature}


def _event_body(**event: object) -> bytes:
    return json.dumps({"team_id": TEAM_ID, "event": event}).encode()


async def _mount_transport(
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: UUID,
    tmp_path,
    transport: httpx.MockTransport,
    hub: InProcessHub | None = None,
):
    _patch_httpx(monkeypatch, transport)
    store = await _store(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_surfaces(
        app,
        (slack_manifest(),),
        workspace_id,
        store,
        blob,
        hub or InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://slack")
    return app, client, blob


async def _mount(
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: UUID,
    tmp_path,
    recorder: list[httpx.Request],
    users: dict[str, str] | None = None,
    hub: InProcessHub | None = None,
):
    return await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _mock_transport(recorder, users or {}), hub=hub
    )


def test_signature_verify_accepts_valid_and_rejects_tampered() -> None:
    body = b'{"type":"event_callback"}'
    now = int(time.time())
    headers = _sign(body, now)
    slack.verify_slack_signature(headers, body, SIGNING_SECRET, now=now)
    with pytest.raises(slack.SlackSignatureError):
        slack.verify_slack_signature(headers, body + b"x", SIGNING_SECRET, now=now)
    with pytest.raises(slack.SlackSignatureError):
        slack.verify_slack_signature(headers, body, SIGNING_SECRET, now=now + 10_000)


def test_block_kit_reply_body_renders_markdown_and_degrades() -> None:
    body = json.loads(slack.slack_reply_body("C5", "200.0", "hi **there**"))
    assert body["channel"] == "C5"
    assert body["thread_ts"] == "200.0"
    assert body["blocks"] == [{"type": "markdown", "text": "hi **there**"}]
    big = "x" * (slack.SLACK_MARKDOWN_TEXT_LIMIT + 1)
    degraded = json.loads(slack.slack_reply_body("C5", None, big))
    assert "blocks" not in degraded
    assert degraded["text"] == big


def test_thread_keying_and_addressing() -> None:
    assert slack.slack_thread_key("C1", "100.5", is_dm=False) == "C1:100.5"
    assert slack.slack_thread_key("D1", "100.5", is_dm=True) == "D1"
    assert slack.slack_message_addressed({"type": "app_mention"}, BOT_USER_ID, is_dm=False)
    assert slack.slack_message_addressed({"type": "message"}, BOT_USER_ID, is_dm=True)
    assert not slack.slack_message_addressed({"type": "message", "text": "hi"}, BOT_USER_ID, False)
    assert slack.slack_message_addressed(
        {"type": "message", "text": f"<@{BOT_USER_ID}> hi"}, BOT_USER_ID, False
    )


def test_ambient_digest_filters_and_bounds() -> None:
    messages = [
        {"user": "U2", "ts": "1700000060.000200", "text": "x" * 500},
        {"user": "U1", "ts": "1700000000.000100", "text": "kicking off the incident thread"},
        {"user": BOT_USER_ID, "ts": "1700000070.000250", "text": "my own reply"},
        {"user": "U3", "ts": "1700000080.000300", "text": f"<@{BOT_USER_ID}> already a turn"},
        {"user": "U4", "ts": "1700000090.000400", "text": "joined", "subtype": "channel_join"},
        {"bot_id": "B1", "ts": "1700000100.000500", "text": "workflow noise"},
        {"user": "U5", "ts": "1700000110.000600", "text": "   "},
        {"user": "U6", "ts": "not-a-ts", "text": "malformed timestamp"},
        {"user": "U7", "ts": "nan", "text": "unrenderable timestamp"},
        {"user": "U8", "ts": "1e300", "text": "overflowing timestamp"},
        {
            "user": "U9",
            "ts": "1700000055.000150",
            "text": "broadcast reply",
            "subtype": "thread_broadcast",
        },
    ]
    digest = slack._ambient_digest(messages, BOT_USER_ID, slack.AMBIENT_THREAD_HEADER)
    assert digest == (
        f"{slack.AMBIENT_THREAD_HEADER}\n"
        "[2023-11-14 22:13] <@U1>: kicking off the incident thread\n"
        "[2023-11-14 22:14] <@U9>: broadcast reply\n"
        f"[2023-11-14 22:14] <@U2>: {'x' * slack.AMBIENT_MESSAGE_CHAR_LIMIT}\n\n"
    )
    assert slack._ambient_digest([], BOT_USER_ID, slack.AMBIENT_THREAD_HEADER) == ""
    only_bot = [{"user": BOT_USER_ID, "ts": "1.0", "text": "hi"}]
    assert slack._ambient_digest(only_bot, BOT_USER_ID, slack.AMBIENT_THREAD_HEADER) == ""
    many = [
        {"user": f"U{i}", "ts": f"{1700000000 + i}.0", "text": f"message {i:03d} " + "y" * 380}
        for i in range(30)
    ]
    capped = slack._ambient_digest(many, BOT_USER_ID, slack.AMBIENT_THREAD_HEADER)
    assert len(capped) <= slack.AMBIENT_DIGEST_MAX_CHARS + len(slack.AMBIENT_THREAD_HEADER) + 3
    assert slack.AMBIENT_OMITTED_MARKER in capped
    assert "message 000" in capped
    assert "message 029" in capped
    assert "message 001" not in capped


def test_turn_context_composes_the_sender_line_and_drops_an_unknown_timezone() -> None:
    full = slack._turn_context(
        slack.SlackUser(name="Bee Jones", email="bee@example.com", timezone="America/New_York")
    )
    assert (full.sender, full.timezone) == ("Bee Jones (bee@example.com)", "America/New_York")
    degraded = slack._turn_context(
        slack.SlackUser(name="Bee Jones", email=None, timezone="Mars/Olympus_Mons")
    )
    assert (degraded.sender, degraded.timezone) == ("Bee Jones", None)
    assert slack._turn_context(None) == slack._turn_context(
        slack.SlackUser(name=None, email=None, timezone=None)
    )


def test_slack_app_setup_skill_parses_indexes_and_names_the_real_route_and_slots() -> None:
    registry = skill_registry((slack_manifest(),))
    index = dict(registry.index())
    assert "slack-app-setup" in index
    body = registry.named("slack-app-setup").instructions
    assert "/surface/slack" in body
    assert "/surface/slack/interactive" in body
    for slot in (
        slack.SLACK_BOT_TOKEN_SLOT,
        slack.SLACK_SIGNING_SECRET_SLOT,
        slack.SLACK_BOT_USER_ID_SLOT,
        slack.SLACK_TEAM_ID_SLOT,
    ):
        assert f"ufoctl credential set {slot}" in body


async def test_bad_signature_is_rejected(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, [])
    body = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="<@UBOT00000>")
    async with client:
        response = await client.post(
            "/surface/slack",
            content=body,
            headers={
                "x-slack-request-timestamp": str(int(time.time())),
                "x-slack-signature": "v0=bad",
            },
        )
    assert response.status_code == 401


async def test_url_verification_answers_the_challenge_and_marks_verified(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    _, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, [])
    body = json.dumps({"type": "url_verification", "challenge": "chal-1"}).encode()
    async with client:
        unsigned = await client.post("/surface/slack", content=body)
        assert unsigned.status_code == 401
        assert not await blob.exists(slack.url_verified_blob_key(workspace_id))
        answered = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert answered.status_code == 200
    assert answered.json() == {"challenge": "chal-1"}
    assert await blob.exists(slack.url_verified_blob_key(workspace_id))


async def test_handshake_before_the_secret_exists_echoes_and_events_stay_401(
    db: None, tmp_path, monkeypatch
) -> None:
    # Slack probes the Request URL the instant the app is created from the manifest — before the
    # owner can hold the secret Slack mints with the app. The challenge echoes unsigned (nothing
    # stored, no verified marker), so creation verifies clean; a real event is still a clean 401.
    workspace_id, _ = await _seed()
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))  # no slots set
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_surfaces(
        app,
        (slack_manifest(),),
        workspace_id,
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://slack")
    handshake = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
    event = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="hi")
    async with client:
        echoed = await client.post("/surface/slack", content=handshake)
        rejected = await client.post(
            "/surface/slack",
            content=event,
            headers={"x-slack-request-timestamp": "1", "x-slack-signature": "v0=x"},
        )
    assert echoed.status_code == 200
    assert echoed.json() == {"challenge": "c"}
    assert rejected.status_code == 401
    assert not await blob.exists(slack.url_verified_blob_key(workspace_id))


async def test_first_signed_event_marks_verified_and_a_rotated_secret_re_proves(
    db: None, tmp_path, monkeypatch
) -> None:
    # Any signature-verified request proves Slack reached this deploy with the stored secret — the
    # first real event flips setup to connected with no manual Request-URL re-save, and a rotated
    # secret's next signed event re-stamps the marker despite the per-process write cache.
    workspace_id, _ = await _seed()
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = await _store(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_surfaces(
        app,
        (slack_manifest(),),
        workspace_id,
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://slack")
    body = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="<@UBOT00000>")
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
        assert response.status_code == 200
        marker = json.loads(await blob.get(slack.url_verified_blob_key(workspace_id)))
        assert marker["fingerprint"] == slack.signing_secret_fingerprint(SIGNING_SECRET)
        rotated = "rotated-secret"
        await store.put(workspace_id, slack.SLACK_SIGNING_SECRET_SLOT, rotated)
        response = await client.post(
            "/surface/slack", content=body, headers=_sign_with(rotated, body)
        )
        assert response.status_code == 200
        marker = json.loads(await blob.get(slack.url_verified_blob_key(workspace_id)))
        assert marker["fingerprint"] == slack.signing_secret_fingerprint(rotated)
        # Rotating back to an earlier secret must re-stamp too — the process cache holds the last
        # written fingerprint, not every fingerprint ever written.
        await store.put(workspace_id, slack.SLACK_SIGNING_SECRET_SLOT, SIGNING_SECRET)
        response = await client.post(
            "/surface/slack", content=body, headers=_sign_with(SIGNING_SECRET, body)
        )
        assert response.status_code == 200
    marker = json.loads(await blob.get(slack.url_verified_blob_key(workspace_id)))
    assert marker["fingerprint"] == slack.signing_secret_fingerprint(SIGNING_SECRET)


def _sign_with(secret: str, body: bytes) -> dict[str, str]:
    ts = int(time.time())
    base = b"v0:" + str(ts).encode() + b":" + body
    return {
        "x-slack-request-timestamp": str(ts),
        "x-slack-signature": "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest(),
    }


async def test_marker_needs_the_configured_team_but_a_click_counts(
    db: None, tmp_path, monkeypatch
) -> None:
    # A signed event from another team is dropped by the team gate and must not read as connected —
    # a mixed-app config (secret from one app, token from another) stays pending, never green and
    # dead. A decoded answer click is team-gated too, so it proves the secret like an event does.
    workspace_id, _ = await _seed()
    _, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, [])
    foreign = json.dumps(
        {
            "team_id": "T0FOREIGN",
            "event": {"type": "app_mention", "user": "U1", "channel": "C1", "ts": "1.0"},
        }
    ).encode()
    click = _click_body()
    async with client:
        ignored = await client.post(
            "/surface/slack", content=foreign, headers=_sign(foreign, int(time.time()))
        )
        assert ignored.json() == {"ok": True, "ignored": True}
        assert not await blob.exists(slack.url_verified_blob_key(workspace_id))
        answered = await client.post(
            "/surface/slack/interactive", content=click, headers=_signed_form(click)
        )
        assert answered.status_code == 200
        await asyncio.gather(*slack._REWRITE_TASKS)
    marker = json.loads(await blob.get(slack.url_verified_blob_key(workspace_id)))
    assert marker["fingerprint"] == slack.signing_secret_fingerprint(SIGNING_SECRET)


async def test_one_mention_admits_exactly_one_turn(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, [])
    mention = "<@UBOT00000> hi"
    deliveries = [
        _event_body(type="app_mention", user="U1", channel="C1", ts="100.5", text=mention),
        _event_body(type="message", user="U1", channel="C1", ts="100.5", text=mention),
    ]
    home_opened = _event_body(type="app_home_opened", user="U1", channel="D9", tab="messages")
    async with client:
        for body in deliveries:
            response = await client.post(
                "/surface/slack", content=body, headers=_sign(body, int(time.time()))
            )
            assert response.status_code == 200
        opened = await client.post(
            "/surface/slack", content=home_opened, headers=_sign(home_opened, int(time.time()))
        )
        assert opened.json() == {"ok": True, "ignored": True}
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.idempotency_key).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).all()
        writeback = (await connection.execute(sa.select(tables.writeback.c.status))).scalar_one()
    assert len(turns) == 1
    assert turns[0].idempotency_key == "C1:100.5"
    assert writeback == WRITEBACK_PENDING


def _ambient_transport(
    recorder: list[httpx.Request], replies: object = (), history: object = ()
) -> httpx.MockTransport:
    """A transport whose `conversations.replies` / `conversations.history` answer with the given
    messages — or with `ok: false` when the fixture is None, the fetch-failure case."""

    def _messages(fixture: object) -> httpx.Response:
        if fixture is None:
            return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
        return httpx.Response(200, json={"ok": True, "messages": fixture})

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_CONVERSATIONS_REPLIES_URL:
            return _messages(replies)
        if url == slack.SLACK_CONVERSATIONS_HISTORY_URL:
            return _messages(history)
        if url == slack.SLACK_USERS_INFO_URL:
            return httpx.Response(200, json={"ok": True, "user": {"profile": {}}})
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})
        if url == slack.SLACK_ASSISTANT_STATUS_URL:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    return httpx.MockTransport(handler)


async def _turn_inbound(workspace_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()


def _fetches(recorder: list[httpx.Request], url: str) -> list[httpx.Request]:
    return [request for request in recorder if str(request.url).startswith(url)]


async def test_mid_thread_mention_prepends_unseen_thread_history(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    replies = [
        {"user": "U1", "ts": "1700000000.000100", "text": "we saw errors spike at noon"},
        {"user": BOT_USER_ID, "ts": "1700000060.000200", "text": "earlier bot reply"},
        {"user": "U2", "ts": "1700000120.000300", "text": "restarting did not help"},
        {"user": "U4", "ts": "1700000181.000500", "text": "reply racing the mention's ingest"},
    ]
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _ambient_transport(recorder, replies)
    )
    body = _event_body(
        type="app_mention",
        user="U3",
        channel="C7",
        ts="1700000180.000400",
        thread_ts="1700000000.000100",
        text="<@UBOT00000> summarize this thread",
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    fetches = _fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)
    assert len(fetches) == 1
    params = fetches[0].url.params
    assert params["channel"] == "C7"
    assert params["ts"] == "1700000000.000100"
    assert "latest" not in params
    assert params["limit"] == str(slack.AMBIENT_FETCH_LIMIT)
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_HISTORY_URL)
    assert await _turn_inbound(workspace_id) == (
        f"{slack.AMBIENT_THREAD_HEADER}\n"
        "[2023-11-14 22:13] <@U1>: we saw errors spike at noon\n"
        "[2023-11-14 22:15] <@U2>: restarting did not help\n"
        "[2023-11-14 22:16] <@U4>: reply racing the mention's ingest\n\n"
        "<@UBOT00000> summarize this thread"
    )


async def test_new_mention_prepends_recent_channel_history(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    history = [
        {"user": "U2", "ts": "1700000060.000200", "text": f"<@{BOT_USER_ID}> old ask"},
        {"user": "U1", "ts": "1700000000.000100", "text": "deploy going out at 3"},
        {"user": BOT_USER_ID, "ts": "1700000030.000150", "text": "old bot reply"},
    ]
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _ambient_transport(recorder, history=history)
    )
    body = _event_body(
        type="app_mention",
        user="U3",
        channel="C9",
        ts="1700000180.000400",
        text="<@UBOT00000> what's the plan?",
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    fetches = _fetches(recorder, slack.SLACK_CONVERSATIONS_HISTORY_URL)
    assert len(fetches) == 1
    params = fetches[0].url.params
    assert params["channel"] == "C9"
    assert params["latest"] == "1700000180.000400"
    assert params["inclusive"] == "false"
    assert params["limit"] == str(slack.AMBIENT_CHANNEL_FETCH_LIMIT)
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)
    assert await _turn_inbound(workspace_id) == (
        f"{slack.AMBIENT_CHANNEL_HEADER}\n"
        "[2023-11-14 22:13] <@U1>: deploy going out at 3\n\n"
        "<@UBOT00000> what's the plan?"
    )


async def test_thread_root_mention_in_a_quiet_channel_admits_the_plain_body(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    body = _event_body(
        type="app_mention", user="U1", channel="C1", ts="50.0", text="<@UBOT00000> hi"
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)
    assert len(_fetches(recorder, slack.SLACK_CONVERSATIONS_HISTORY_URL)) == 1
    assert await _turn_inbound(workspace_id) == "<@UBOT00000> hi"


async def test_dm_never_fetches_thread_context(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    dm = _event_body(
        type="message",
        channel_type="im",
        user="U8",
        channel="D3",
        ts="60.5",
        thread_ts="10.0",
        text="hello",
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=dm, headers=_sign(dm, int(time.time()))
        )
    assert response.status_code == 200
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_HISTORY_URL)
    assert await _turn_inbound(workspace_id) == "hello"


async def test_replies_fetch_failure_still_admits(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _ambient_transport(recorder, replies=None)
    )
    body = _event_body(
        type="app_mention",
        user="U1",
        channel="C7",
        ts="200.5",
        thread_ts="100.0",
        text="<@UBOT00000> ping",
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    assert len(_fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)) == 1
    assert await _turn_inbound(workspace_id) == "<@UBOT00000> ping"


async def test_participating_thread_admits_unmentioned_replies_on_the_transcript(
    db: None, tmp_path, monkeypatch
) -> None:
    """The participation gate end to end: the first mention makes the thread a conversation and
    carries the ambient digest; from then on every member reply — un-mentioned, broadcast, or a
    second mention — is its own turn with its plain body and no refetched digest, while replies in
    a foreign thread and top-level chatter stay ignored."""
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    replies = [{"user": "U1", "ts": "1700000000.000100", "text": "pre-mention chatter"}]
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _ambient_transport(recorder, replies=replies)
    )
    root = "1700000000.000100"
    admitted = [
        _event_body(
            type="app_mention",
            user="U1",
            channel="C1",
            ts="1700000180.000400",
            thread_ts=root,
            text="<@UBOT00000> take a look",
        ),
        _event_body(
            type="message",
            user="U2",
            channel="C1",
            ts="1700000240.000500",
            thread_ts=root,
            text="and it happens on retries too",
        ),
        _event_body(
            type="message",
            subtype="thread_broadcast",
            user="U3",
            channel="C1",
            ts="1700000300.000600",
            thread_ts=root,
            text="broadcasting the reply",
        ),
        _event_body(
            type="app_mention",
            user="U1",
            channel="C1",
            ts="1700000360.000700",
            thread_ts=root,
            text="<@UBOT00000> anything yet?",
        ),
    ]
    ignored = [
        _event_body(
            type="message",
            user="U2",
            channel="C1",
            ts="1700000420.000800",
            thread_ts="1699990000.000900",
            text="reply in a thread the agent never joined",
        ),
        _event_body(
            type="message",
            user="U2",
            channel="C1",
            ts="1700000480.000900",
            text="top-level passing message",
        ),
    ]
    async with client:
        for body in admitted:
            response = await client.post(
                "/surface/slack", content=body, headers=_sign(body, int(time.time()))
            )
            assert response.json() == {"ok": True}
        for body in ignored:
            response = await client.post(
                "/surface/slack", content=body, headers=_sign(body, int(time.time()))
            )
            assert response.json() == {"ok": True, "ignored": True}
    async with workspace_tx() as connection:
        queue_keys = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.queue_key).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.idempotency_key)
                .where(tables.turn.c.workspace_id == workspace_id)
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert queue_keys == [f"C1:{root}"]
    assert [turn.idempotency_key for turn in turns] == [
        "C1:1700000180.000400",
        "C1:1700000240.000500",
        "C1:1700000300.000600",
        "C1:1700000360.000700",
    ]
    assert turns[0].inbound == (
        f"{slack.AMBIENT_THREAD_HEADER}\n"
        "[2023-11-14 22:13] <@U1>: pre-mention chatter\n\n"
        "<@UBOT00000> take a look"
    )
    assert turns[1].inbound == "and it happens on retries too"
    assert turns[2].inbound == "broadcasting the reply"
    assert turns[3].inbound == "<@UBOT00000> anything yet?"
    assert len(_fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)) == 1
    assert not _fetches(recorder, slack.SLACK_CONVERSATIONS_HISTORY_URL)


async def test_a_bare_conversation_row_is_not_participation(
    db: None, tmp_path, monkeypatch
) -> None:
    """The half-state a conversation-starting ingest passes through — row created, first turn not
    yet admitted: an un-mentioned reply is still ignored, never admitted ahead of the starting
    turn, and a mention landing on the bare row still fetches its ambient backfill."""
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    root = "1700000000.000100"
    replies = [{"user": "U1", "ts": root, "text": "the thread root"}]
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _ambient_transport(recorder, replies=replies)
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=uuid4(),
                workspace_id=workspace_id,
                surface=slack.SURFACE_SLACK,
                queue_key=f"C1:{root}",
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    reply = _event_body(
        type="message",
        user="U2",
        channel="C1",
        ts="1700000240.000500",
        thread_ts=root,
        text="reply racing the starting mention",
    )
    mention = _event_body(
        type="app_mention",
        user="U1",
        channel="C1",
        ts="1700000300.000600",
        thread_ts=root,
        text="<@UBOT00000> hello",
    )
    async with client:
        ignored = await client.post(
            "/surface/slack", content=reply, headers=_sign(reply, int(time.time()))
        )
        assert ignored.json() == {"ok": True, "ignored": True}
        admitted = await client.post(
            "/surface/slack", content=mention, headers=_sign(mention, int(time.time()))
        )
        assert admitted.json() == {"ok": True}
    async with workspace_tx() as connection:
        turns = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(turns) == 1
    assert turns[0].startswith(slack.AMBIENT_THREAD_HEADER)
    assert len(_fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)) == 1


async def test_dm_links_member_by_email_and_status_anchors_to_the_message(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, recorder, users={"UBEE": "bee@example.com"}
    )
    dm = _event_body(
        type="message", channel_type="im", user="UBEE", channel="D9", ts="7.0", text="hey"
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=dm, headers=_sign(dm, int(time.time()))
        )
    assert response.status_code == 200
    deadline = time.monotonic() + 5
    while not _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL):
        assert time.monotonic() < deadline, "status never reached Slack"
        await asyncio.sleep(0.01)
    status = json.loads(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)[0].content)
    assert status == {
        "channel_id": "D9",
        "thread_ts": "7.0",
        "status": slack.STATUS_THINKING_TEXT,
    }
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == slack.SURFACE_SLACK,
                    tables.surface_identity.c.external_id == "UBEE",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.queue_key == "D9"
                )
            )
        ).one()
        turn_context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert linked.member_id == member_id
    assert conversation.member_id == member_id
    assert turn_context == {
        "sender": "Bee Jones (bee@example.com)",
        "timezone": "America/New_York",
    }


async def test_unlinked_dm_fails_loud_when_the_sender_read_is_unavailable(
    db: None, tmp_path, monkeypatch
) -> None:
    """Sender context is best-effort, but an unlinked DM cannot resolve its member without the
    users.info read — ingest raises instead of silently admitting the member's DM as nobody."""

    def slack_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "internal_error"})

    workspace_id, _ = await _seed(member_email="bee@example.com")
    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(slack_down)
    )
    dm = _event_body(
        type="message", channel_type="im", user="UBEE", channel="D9", ts="7.0", text="hey"
    )
    async with client:
        with pytest.raises(slack.SlackApiError, match="cannot resolve the DM member UBEE"):
            await client.post("/surface/slack", content=dm, headers=_sign(dm, int(time.time())))
    async with workspace_tx() as connection:
        admitted = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert admitted == 0


async def test_inbound_file_streams_into_the_workspace(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    _, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, [])
    file = {
        "id": "F9",
        "name": "data.csv",
        "url_private_download": "https://files.slack.com/files-pri/T-F9/data.csv",
        "mimetype": "text/csv",
    }
    body = _event_body(
        type="app_mention",
        user="U1",
        channel="C1",
        ts="5.0",
        text="<@UBOT00000> see file",
        files=[file],
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.queue_key == "C1:5.0"
                )
            )
        ).scalar_one()
    stored = await blob.get(workspace_key(conversation_id, f"{slack.SLACK_INBOX_DIR}/data.csv"))
    assert stored == b"INBOUND-BYTES"


async def test_file_share_subtype_is_a_member_message_whose_file_lands(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    _, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, [])
    file = {
        "id": "F7",
        "name": "notes.txt",
        "url_private_download": "https://files.slack.com/files-pri/T-F7/notes.txt",
        "mimetype": "text/plain",
    }
    shared = _event_body(
        type="message",
        channel_type="im",
        subtype="file_share",
        user="U1",
        channel="D9",
        ts="9.0",
        text="see attached",
        files=[file],
    )
    edited = _event_body(
        type="message",
        channel_type="im",
        subtype="message_changed",
        user="U1",
        channel="D9",
        ts="9.1",
        text="edited",
    )
    async with client:
        admitted = await client.post(
            "/surface/slack", content=shared, headers=_sign(shared, int(time.time()))
        )
        ignored = await client.post(
            "/surface/slack", content=edited, headers=_sign(edited, int(time.time()))
        )
    assert admitted.status_code == 200
    assert ignored.json() == {"ok": True, "ignored": True}
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(tables.conversation.c.queue_key == "D9")
            )
        ).scalar_one()
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.conversation_id == conversation_id
                )
            )
        ).scalar_one()
    stored = await blob.get(workspace_key(conversation_id, f"{slack.SLACK_INBOX_DIR}/notes.txt"))
    assert stored == b"INBOUND-BYTES"
    assert f"{slack.SLACK_INBOX_DIR}/notes.txt" in inbound


async def _seed_done_turn(
    workspace_id: UUID,
    queue_key: str,
    text: str,
    blob,
    artifact: bool,
    *,
    artifact_name: str = "report.pdf",
    artifact_key: str = "artifacts/a/report.pdf",
    artifact_size: int = 11,
    artifact_media_type: str = "application/pdf",
    question: AskUserInput | None = None,
) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
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
                surface=slack.SURFACE_SLACK,
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
                status="done",
                inbound="ask",
                terminal=TerminalFrame(status="done", text=text, question=question).model_dump(
                    mode="json"
                ),
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
        if artifact:
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=artifact_key,
                    workspace_id=workspace_id,
                    filename=artifact_name,
                    subject=None,
                    media_type=artifact_media_type,
                    size_bytes=artifact_size,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return turn_id


async def test_writeback_posts_block_kit_reply_and_streams_the_attachment(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    await blob.put("artifacts/a/report.pdf", b"PDF-CONTENT")
    turn_id = await _seed_done_turn(workspace_id, "C5:200.0", "hi **there**", blob, artifact=True)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    reply = json.loads(posts[0].content)
    assert reply["channel"] == "C5"
    assert reply["thread_ts"] == "200.0"
    assert reply["blocks"] == [{"type": "markdown", "text": "hi **there**"}]

    reserve = [r for r in recorder if str(r.url) == slack.SLACK_FILES_GET_UPLOAD_URL]
    uploads = [r for r in recorder if str(r.url) == UPLOAD_URL]
    completes = [r for r in recorder if str(r.url) == slack.SLACK_FILES_COMPLETE_UPLOAD]
    assert len(reserve) == 1 and len(uploads) == 1 and len(completes) == 1
    assert uploads[0].content == b"PDF-CONTENT"
    complete_body = json.loads(completes[0].content)
    assert complete_body["channel_id"] == "C5"
    assert complete_body["thread_ts"] == "200.0"
    assert complete_body["files"] == [{"id": "F1", "title": "report.pdf"}]

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.writeback.c.status, tables.writeback.c.reply_ref).where(
                    tables.writeback.c.turn_id == turn_id
                )
            )
        ).one()
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref == "C5:999.100"


async def test_writeback_streams_dm_attachment_without_threading_under_the_bot_reply(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    await blob.put("artifacts/a/report.pdf", b"PDF-CONTENT")
    await _seed_done_turn(workspace_id, "D5", "here", blob, artifact=True)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    reply = json.loads(posts[0].content)
    assert reply["channel"] == "D5"
    assert "thread_ts" not in reply

    completes = [r for r in recorder if str(r.url) == slack.SLACK_FILES_COMPLETE_UPLOAD]
    assert len(completes) == 1
    complete_body = json.loads(completes[0].content)
    assert complete_body["channel_id"] == "D5"
    assert "thread_ts" not in complete_body
    assert complete_body["files"] == [{"id": "F1", "title": "report.pdf"}]


async def test_oversize_artifact_is_delivered_as_a_download_link(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    await blob.put("artifacts/big/huge.bin", b"OVERSIZE")
    turn_id = await _seed_done_turn(
        workspace_id,
        "C5:200.0",
        "here you go",
        blob,
        artifact=True,
        artifact_name="huge.bin",
        artifact_key="artifacts/big/huge.bin",
        artifact_size=slack.SLACK_UPLOAD_MAX_BYTES + 1,
        artifact_media_type="application/octet-stream",
    )

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    reply = json.loads(posts[0].content)
    assert slack.SLACK_OVERSIZE_HEADING in reply["text"]
    match = re.search(r"\[huge\.bin\]\((https://[^)]+)\)", reply["text"])
    assert match is not None
    url = match.group(1)
    assert url.startswith(f"{PUBLIC_BASE_URL}/artifacts/download?token=")
    token = url.split("token=", 1)[1]
    claims = verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC))
    assert claims.blob_key == "artifacts/big/huge.bin"
    assert claims.filename == "huge.bin"

    assert [r for r in recorder if str(r.url) == slack.SLACK_FILES_GET_UPLOAD_URL] == []

    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.writeback.c.status).where(tables.writeback.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert status == WRITEBACK_DELIVERED


async def test_invalid_blocks_reposts_once_as_plain_text(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if str(request.url).split("?")[0] != slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(404, json={"ok": False, "error": "not_mocked"})
        prior = [
            r for r in recorder if str(r.url).split("?")[0] == slack.SLACK_CHAT_POST_MESSAGE_URL
        ]
        if len(prior) == 1:
            return httpx.Response(200, json={"ok": False, "error": "invalid_blocks"})
        return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.200"})

    app, _, blob = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(handler)
    )
    turn_id = await _seed_done_turn(workspace_id, "C5:200.0", "hi **there**", blob, artifact=False)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url).split("?")[0] == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 2
    first = json.loads(posts[0].content)
    second = json.loads(posts[1].content)
    assert first["blocks"] == [{"type": "markdown", "text": "hi **there**"}]
    assert "blocks" not in second
    assert second["text"] == "hi **there**"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.writeback.c.status, tables.writeback.c.reply_ref).where(
                    tables.writeback.c.turn_id == turn_id
                )
            )
        ).one()
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref == "C5:999.200"


async def test_inbound_oversize_file_is_skipped_and_reported(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    monkeypatch.setattr(slack, "SLACK_INBOUND_FILE_MAX_BYTES", 8)
    recorder: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url.endswith("/small.txt"):
            return httpx.Response(200, content=b"small")
        if url.endswith("/big.bin"):
            return httpx.Response(200, content=b"BIG-CONTENT-OVER-THE-CAP")
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    _, client, blob = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(handler)
    )
    files = [
        {
            "id": "F1",
            "name": "small.txt",
            "url_private_download": "https://files.slack.com/files-pri/T-F1/small.txt",
            "mimetype": "text/plain",
        },
        {
            "id": "F2",
            "name": "big.bin",
            "url_private_download": "https://files.slack.com/files-pri/T-F2/big.bin",
            "mimetype": "application/octet-stream",
        },
    ]
    body = _event_body(
        type="app_mention",
        user="U1",
        channel="C1",
        ts="6.0",
        text="<@UBOT00000> files",
        files=files,
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200

    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.queue_key == "C1:6.0"
                )
            )
        ).scalar_one()
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.conversation_id == conversation_id
                )
            )
        ).scalar_one()

    assert (
        await blob.get(workspace_key(conversation_id, f"{slack.SLACK_INBOX_DIR}/small.txt"))
        == b"small"
    )
    with pytest.raises(BlobNotFound):
        await blob.get(workspace_key(conversation_id, f"{slack.SLACK_INBOX_DIR}/big.bin"))
    assert f"{slack.SLACK_INBOX_DIR}/small.txt" in inbound
    assert "Skipped files" in inbound
    assert "big.bin" in inbound


def _requests_to(recorder: list[httpx.Request], url: str) -> list[httpx.Request]:
    return [r for r in recorder if str(r.url).split("?")[0] == url]


async def test_status_follows_the_turn_and_clears_at_terminal(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    monkeypatch.setattr(slack, "STATUS_UPDATE_MIN_SECONDS", 0.0)
    recorder: list[httpx.Request] = []
    hub = InProcessHub()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder, hub=hub)
    mention = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text="<@UBOT00000> hi"
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=mention, headers=_sign(mention, int(time.time()))
        )
    assert response.status_code == 200
    async with workspace_tx() as connection:
        turn_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    task = slack._STATUS_TASKS[turn_id]

    await hub.publish(turn_id, ToolCall(tool="bash", preview="{}", description="Reading the repo"))
    deadline = time.monotonic() + 5
    while len(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)) < 2:
        assert time.monotonic() < deadline, "status update never reached Slack"
        await asyncio.sleep(0.01)
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done", text="hi")))
    await task

    statuses = [
        json.loads(r.content) for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ]
    assert statuses[0] == {
        "channel_id": "C1",
        "thread_ts": "100.5",
        "status": slack.STATUS_THINKING_TEXT,
    }
    assert statuses[1]["status"] == "Reading the repo"
    assert statuses[-1]["status"] == slack.STATUS_CLEAR_TEXT
    assert not _requests_to(recorder, slack.SLACK_CHAT_POST_MESSAGE_URL)
    assert turn_id not in slack._STATUS_TASKS


async def test_status_re_stamps_before_slack_drops_it(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    monkeypatch.setattr(slack, "STATUS_REFRESH_SECONDS", 0.05)
    recorder: list[httpx.Request] = []
    hub = InProcessHub()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder, hub=hub)
    mention = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text="<@UBOT00000> hi"
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=mention, headers=_sign(mention, int(time.time()))
        )
    assert response.status_code == 200
    deadline = time.monotonic() + 5
    while len(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)) < 3:
        assert time.monotonic() < deadline, "quiet stretch never re-stamped the status"
        await asyncio.sleep(0.01)
    statuses = [
        json.loads(r.content)["status"]
        for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)[:3]
    ]
    assert statuses == [slack.STATUS_THINKING_TEXT] * 3


async def test_newest_turn_owns_the_thread_status(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    monkeypatch.setattr(slack, "STATUS_UPDATE_MIN_SECONDS", 0.0)
    recorder: list[httpx.Request] = []
    hub = InProcessHub()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder, hub=hub)
    first = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text="<@UBOT00000> one"
    )
    second = _event_body(
        type="app_mention",
        user="U1",
        channel="C1",
        ts="101.0",
        thread_ts="100.5",
        text="<@UBOT00000> two",
    )
    async with client:
        for body in (first, second):
            response = await client.post(
                "/surface/slack", content=body, headers=_sign(body, int(time.time()))
            )
            assert response.status_code == 200
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.idempotency_key).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).all()
    turns = {row.idempotency_key: row.id for row in rows}

    first_task = slack._STATUS_TASKS[turns["C1:100.5"]]
    await hub.publish(turns["C1:100.5"], Terminal(frame=TerminalFrame(status="done", text="one")))
    await first_task
    statuses = [
        json.loads(r.content) for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ]
    assert all(s["status"] != slack.STATUS_CLEAR_TEXT for s in statuses)

    second_task = slack._STATUS_TASKS[turns["C1:101.0"]]
    await hub.publish(
        turns["C1:101.0"], ToolCall(tool="bash", preview="{}", description="Checking the calendar")
    )
    deadline = time.monotonic() + 5
    while not any(
        json.loads(r.content)["status"] == "Checking the calendar"
        for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ):
        assert time.monotonic() < deadline, "the surviving turn never wrote its status"
        await asyncio.sleep(0.01)
    await hub.publish(turns["C1:101.0"], Terminal(frame=TerminalFrame(status="done", text="two")))
    await second_task
    final = json.loads(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)[-1].content)
    assert final == {"channel_id": "C1", "thread_ts": "100.5", "status": slack.STATUS_CLEAR_TEXT}


ASK_QUESTION = AskUserInput(
    title="Need a decision",
    questions=(
        AskQuestion(
            question="Ship it?",
            options=(QuestionOption(label="Ship"), QuestionOption(label="Hold")),
        ),
    ),
)


def test_only_a_single_choice_question_renders_buttons() -> None:
    single = ASK_QUESTION.questions[0]
    actions = slack.slack_answer_actions(ASK_QUESTION)
    assert actions is not None and actions["type"] == "actions"
    assert [b["text"]["text"] for b in actions["elements"]] == ["Ship", "Hold"]
    assert slack.slack_answer_actions(None) is None
    two = AskUserInput(title="t", questions=(single, single))
    assert slack.slack_answer_actions(two) is None
    multi = AskUserInput(title="t", questions=(single.model_copy(update={"multi_select": True}),))
    assert slack.slack_answer_actions(multi) is None
    free = AskUserInput(title="t", questions=(single.model_copy(update={"free_text_only": True}),))
    assert slack.slack_answer_actions(free) is None
    prose = AskUserInput(title="t", questions=(AskQuestion(question="Ship it?"),))
    assert slack.slack_answer_actions(prose) is None
    attach = AskUserInput(
        title="t", questions=(single.model_copy(update={"allow_attachments": True}),)
    )
    assert slack.slack_answer_actions(attach) is None
    crowded = AskUserInput(
        title="t",
        questions=(
            AskQuestion(
                question="q",
                options=tuple(
                    QuestionOption(label=f"o{i}") for i in range(slack.MAX_ANSWER_BUTTONS + 1)
                ),
            ),
        ),
    )
    assert slack.slack_answer_actions(crowded) is None


async def test_question_writeback_posts_answer_buttons(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    await _seed_done_turn(
        workspace_id,
        "C5:200.0",
        "Ship it? (Ship / Hold)",
        blob,
        artifact=False,
        question=ASK_QUESTION,
    )

    await app.state.writeback_poller.drain()

    reply = json.loads(_requests_to(recorder, slack.SLACK_CHAT_POST_MESSAGE_URL)[0].content)
    assert reply["blocks"][0] == {"type": "markdown", "text": "Ship it? (Ship / Hold)"}
    actions = reply["blocks"][1]
    assert actions["type"] == "actions"
    assert [b["text"]["text"] for b in actions["elements"]] == ["Ship", "Hold"]
    assert [b["action_id"] for b in actions["elements"]] == ["ask:0", "ask:1"]
    assert [b["value"] for b in actions["elements"]] == ["Ship", "Hold"]


def _click_body(action_id: str = "ask:0", value: str = "Ship", user: str = "U9") -> bytes:
    payload = {
        "type": "block_actions",
        "team": {"id": TEAM_ID},
        "user": {"id": user},
        "channel": {"id": "C5"},
        "message": {"ts": "999.100", "thread_ts": "200.0", "text": "Ship it? (Ship / Hold)"},
        "actions": [{"action_id": action_id, "value": value}],
        "response_url": RESPONSE_URL,
    }
    return urlencode({"payload": json.dumps(payload)}).encode()


def _signed_form(body: bytes) -> dict[str, str]:
    return {
        **_sign(body, int(time.time())),
        "content-type": "application/x-www-form-urlencoded",
    }


async def test_first_click_wins_and_alone_rewrites_the_message(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    winner = _click_body()
    async with client:
        unsigned = await client.post("/surface/slack/interactive", content=winner)
        assert unsigned.status_code == 401
        first = await client.post(
            "/surface/slack/interactive", content=winner, headers=_signed_form(winner)
        )
        assert first.status_code == 200
        await asyncio.gather(*slack._REWRITE_TASKS)
        loser = _click_body(value="Hold", user="U8")
        second = await client.post(
            "/surface/slack/interactive", content=loser, headers=_signed_form(loser)
        )
        assert second.status_code == 200
        await asyncio.gather(*slack._REWRITE_TASKS)
        foreign = _click_body(action_id="other:0")
        ignored = await client.post(
            "/surface/slack/interactive", content=foreign, headers=_signed_form(foreign)
        )
        assert ignored.json() == {"ok": True, "ignored": True}

    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.idempotency_key).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).all()
        queue_key = (
            await connection.execute(
                sa.select(tables.conversation.c.queue_key).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert len(turns) == 1
    assert turns[0].inbound == "[Answered by <@U9> via button] Ship"
    assert turns[0].idempotency_key == "C5:200.0:999.100:answer"
    assert queue_key == "C5:200.0"

    rewrites = _requests_to(recorder, RESPONSE_URL)
    assert len(rewrites) == 1
    rewrite = json.loads(rewrites[0].content)
    assert rewrite["replace_original"] is True
    assert rewrite["blocks"][0] == {"type": "markdown", "text": "Ship it? (Ship / Hold)"}
    answered = rewrite["blocks"][1]
    assert answered["type"] == "context"
    assert "Answered by <@U9>" in answered["elements"][0]["text"]
    assert "Ship" in answered["elements"][0]["text"]
