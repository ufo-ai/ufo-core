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
import logging
import re
import time
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_slack.surface as slack
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest
from ufo_ext_slack.manifest import manifest as slack_manifest

import ufo.surfaces.hub_tail as hub_tail
from ufo.artifact_token import verify_artifact_token
from ufo.blob import BlobNotFound, FilesystemBlobStore
from ufo.credentials import (
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import current_workspace, workspace_tx
from ufo.ext.loader import turn_tools
from ufo.ext.surface import (
    OPERATOR_EMAIL_DOMAIN,
    WRITEBACK_DELIVERED,
    workspace_key,
)
from ufo.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.hub import InProcessHub, Parked, Terminal, TextDelta, ToolCall
from ufo.schema import tables
from ufo.schema.records import (
    WRITEBACK_PENDING,
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    QuestionOption,
    TerminalFrame,
)
from ufo.serve import _mount_shared_surfaces
from ufo.workspace import ws

TEAM_ID = "T0000001"
BOT_USER_ID = "UBOT00000"
SIGNING_SECRET = "signing-secret"
CLIENT_ID = "112233.445566"
CLIENT_SECRET = "client-secret"
BOT_TOKEN = "xoxb-test"
UPLOAD_URL = "https://files.slack.com/upload/session-1"
RESPONSE_URL = "https://hooks.slack.com/actions/T0000001/123/abc"
ARTIFACT_SECRET = "artifact-token-secret"
PUBLIC_BASE_URL = "https://ufo.example.test"
OPERATOR_OWNER_EMAIL = f"owner@{OPERATOR_EMAIL_DOMAIN}"

ASK_QUESTION = AskUserInput(
    title="Need a decision",
    questions=(
        AskQuestion(
            question="Ship it?",
            options=(QuestionOption(label="Ship"), QuestionOption(label="Hold")),
        ),
    ),
)

REAL_ASYNC_CLIENT = httpx.AsyncClient


EVENTS_PATH = "/surface/slack"
INTERACTIVE_PATH = f"{EVENTS_PATH}/interactive"


@pytest.fixture(autouse=True)
def _deploy_secrets():
    """The deploy Slack app's env-sourced secrets — inbound requests are verified and the OAuth
    exchange runs against these, so every surface test runs with them set. Uses its own
    MonkeyPatch rather than the shared `monkeypatch` fixture: depending on `monkeypatch` here would
    pull it into the autouse setup phase ahead of `_settle_status_tasks`, flipping teardown order so
    the shared fixture restores `httpx.AsyncClient` to the Slack fallback last — leaking it into the
    next test in the xdist worker."""
    patch = pytest.MonkeyPatch()
    patch.setenv(slack.SLACK_SIGNING_SECRET_ENV, SIGNING_SECRET)
    patch.setenv(slack.SLACK_CLIENT_ID_ENV, CLIENT_ID)
    patch.setenv(slack.SLACK_CLIENT_SECRET_ENV, CLIENT_SECRET)
    try:
        yield
    finally:
        patch.undo()


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
        await asyncio.gather(*slack._IDENTITY_TASKS.values(), return_exceptions=True)
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
        slack._IDENTITY_TASKS.clear()
    finally:
        patch.undo()


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _mock_transport(
    recorder: list[httpx.Request],
    users: dict[str, str],
    unconfirmed: AbstractSet[str] = frozenset(),
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_USERS_INFO_URL:
            user_id = str(request.url.params.get("user"))
            email = users.get(user_id)
            user: dict[str, object] = {"profile": {"email": email} if email else {}}
            if email:
                user |= {
                    "real_name": "Bee Jones",
                    "tz": "America/New_York",
                    "is_email_confirmed": user_id not in unconfirmed,
                }
            return httpx.Response(200, json={"ok": True, "user": user})
        if url == slack.SLACK_OAUTH_ACCESS_URL:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": BOT_TOKEN,
                    "team": {"id": TEAM_ID, "name": "acme"},
                    "bot_user_id": BOT_USER_ID,
                },
            )
        if url == slack.SLACK_AUTH_TEST_URL:
            return httpx.Response(
                200, json={"ok": True, "team_id": TEAM_ID, "user_id": BOT_USER_ID}
            )
        if url in (
            slack.SLACK_CONVERSATIONS_REPLIES_URL,
            slack.SLACK_CONVERSATIONS_HISTORY_URL,
        ):
            return httpx.Response(200, json={"ok": True, "messages": []})
        if url == slack.SLACK_CONVERSATIONS_INFO_URL:
            channel_id = str(request.url.params.get("channel"))
            return httpx.Response(
                200, json={"ok": True, "channel": {"id": channel_id, "is_ext_shared": False}}
            )
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})
        if url == slack.SLACK_CHAT_POST_EPHEMERAL_URL:
            return httpx.Response(200, json={"ok": True})
        if url == slack.SLACK_ASSISTANT_STATUS_URL:
            return httpx.Response(200, json={"ok": True})
        if url == slack.SLACK_CHAT_UPDATE_URL:
            return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})
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
    await store.put(workspace_id, slack.SLACK_BOT_TOKEN_SLOT, BOT_TOKEN)
    return store


async def _register_slack(
    store: CredentialStore, workspace_id: UUID, team_id: str = TEAM_ID
) -> None:
    _, contexts = turn_tools((slack_manifest(),), store)
    with ws(workspace_id):
        await contexts["slack_connect"].installations.bind(
            slack.SURFACE_SLACK, slack.slack_installation_id(team_id)
        )


async def _write_identity(
    blob: FilesystemBlobStore,
    workspace_id: UUID,
    bot_token: str = BOT_TOKEN,
    team_id: str = TEAM_ID,
    bot_user_id: str = BOT_USER_ID,
) -> None:
    identity = slack.SlackIdentity(
        bot_token_fingerprint=slack.bot_token_fingerprint(bot_token),
        team_id=team_id,
        bot_user_id=bot_user_id,
    )
    await blob.put(slack.identity_blob_key(workspace_id), identity.model_dump_json().encode())


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
    identity: bool = True,
    public_base_url: str | None = PUBLIC_BASE_URL,
):
    _patch_httpx(monkeypatch, transport)
    store = await _store(workspace_id)
    await _register_slack(store, workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    if identity:
        await _write_identity(blob, workspace_id)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        hub or InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        public_base_url,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://slack")
    return app, client, blob


async def test_first_signed_event_proves_identity_and_retry_admits(
    db: None, tmp_path, monkeypatch
) -> None:
    """A manifest-app workspace whose identity was never derived proves it off the first signed
    event (`auth.test`) and returns 503 so Slack retries; the retry finds the identity and admits.
    Verification here uses the deploy env signing secret when the workspace holds no slot."""
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    _, client, blob = await _mount_transport(
        monkeypatch,
        workspace_id,
        tmp_path,
        _mock_transport(recorder, {}),
        identity=False,
    )
    body = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text=f"<@{BOT_USER_ID}> hi"
    )
    async with client:
        first = await client.post(EVENTS_PATH, content=body, headers=_sign(body, int(time.time())))
        assert first.status_code == 503
        await asyncio.gather(*slack._IDENTITY_TASKS.values())
        response = await client.post(
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    assert await slack.read_identity(blob, workspace_id, BOT_TOKEN) == slack.SlackIdentity(
        bot_token_fingerprint=slack.bot_token_fingerprint(BOT_TOKEN),
        team_id=TEAM_ID,
        bot_user_id=BOT_USER_ID,
    )
    assert len(_fetches(recorder, slack.SLACK_AUTH_TEST_URL)) == 1
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one() == 1


async def test_manifest_workspace_verifies_with_its_own_signing_slot(
    db: None, tmp_path, monkeypatch
) -> None:
    """A bring-your-own-app workspace holds its own `slack_signing_secret` slot; its events verify
    against that slot, not the deploy env secret — the slot wins the slot-else-env resolution."""
    workspace_id, _ = await _seed()
    own_secret = "byo-app-signing-secret"
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _mock_transport(recorder, {}))
    store = await _store(workspace_id)
    await store.put(workspace_id, slack.SLACK_SIGNING_SECRET_SLOT, own_secret)
    await _register_slack(store, workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    await _write_identity(blob, workspace_id)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    body = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text=f"<@{BOT_USER_ID}> hi"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        with_slot = await client.post(
            EVENTS_PATH, content=body, headers=_sign_with(own_secret, body)
        )
        with_env = await client.post(
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
        )
    assert with_slot.status_code == 200
    assert with_env.status_code == 401


async def _mount(
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: UUID,
    tmp_path,
    recorder: list[httpx.Request],
    users: dict[str, str] | None = None,
    hub: InProcessHub | None = None,
    unconfirmed: AbstractSet[str] = frozenset(),
):
    return await _mount_transport(
        monkeypatch,
        workspace_id,
        tmp_path,
        _mock_transport(recorder, users or {}, unconfirmed),
        hub=hub,
    )


FOOTER_LABEL = "$0.001234 (1,234 tokens, 42% cached) · claude-opus-4-8-[high]"


async def _debug_footer(workspace_id: UUID, queue_key: str, turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key == queue_key,
                )
            )
        ).scalar_one()
    return (
        f"{FOOTER_LABEL} · "
        f"<{PUBLIC_BASE_URL}/surface/debug?ws={workspace_id}&c={conversation_id}&t={turn_id}"
        f"|debug>"
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


async def test_raw_request_body_is_cached_exactly() -> None:
    messages = [
        {"type": "http.request", "body": b'{"team_', "more_body": True},
        {"type": "http.request", "body": b'id":"T1"}', "more_body": False},
    ]
    reads = 0

    async def receive() -> dict[str, object]:
        nonlocal reads
        message = messages[reads]
        reads += 1
        return message

    request = StarletteRequest(
        {"type": "http", "method": "POST", "headers": [], "state": {}}, receive
    )
    expected = b'{"team_id":"T1"}'

    assert await slack._slack_request_body(request) == expected
    assert await slack._slack_request_body(request) == expected
    assert request.state.slack_raw_body == expected
    assert reads == 2


async def test_raw_request_body_stops_on_the_first_chunk_past_one_mib() -> None:
    messages = [
        {
            "type": "http.request",
            "body": b"a" * slack.MAX_SLACK_EVENT_BYTES,
            "more_body": True,
        },
        {"type": "http.request", "body": b"b", "more_body": True},
        {"type": "http.request", "body": b"never-read", "more_body": False},
    ]
    reads = 0

    async def receive() -> dict[str, object]:
        nonlocal reads
        message = messages[reads]
        reads += 1
        return message

    request = StarletteRequest(
        {"type": "http", "method": "POST", "headers": [], "state": {}}, receive
    )

    with pytest.raises(slack.SlackBodyTooLarge):
        await slack._slack_request_body(request)
    with pytest.raises(slack.SlackBodyTooLarge):
        await slack._slack_request_body(request)
    assert reads == 2


def test_block_kit_reply_body_renders_markdown_and_degrades() -> None:
    metadata = "$0.001234 (1,234 tokens, 42% cached) · claude-opus-4-8-[high]"
    body = json.loads(slack.slack_reply_body("C5", "200.0", "hi **there**", metadata))
    assert body["channel"] == "C5"
    assert body["thread_ts"] == "200.0"
    assert body["blocks"] == [
        {"type": "markdown", "text": "hi **there**"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": metadata}]},
    ]
    without_footer = json.loads(slack.slack_reply_body("C5", "200.0", "hi", None))
    assert without_footer["blocks"] == [{"type": "markdown", "text": "hi"}]
    big = "x" * (slack.SLACK_MARKDOWN_TEXT_LIMIT + 1)
    degraded = json.loads(slack.slack_reply_body("C5", None, big, metadata))
    assert "blocks" not in degraded
    assert degraded["text"] == f"{big}\n\n{metadata}"
    connect = slack.slack_connect_blocks(ConnectRequest(provider="github"), uuid4())
    assert connect is not None
    preserved = json.loads(slack.slack_reply_body("C5", None, big, metadata, actions=connect))
    assert "".join(block["text"] for block in preserved["blocks"][:-2]) == big
    assert preserved["blocks"][-2] == connect[0]
    with pytest.raises(ValueError, match="metadata is too large"):
        slack.slack_reply_body("C5", None, "hi", "x" * (slack.SLACK_CONTEXT_TEXT_LIMIT + 1))


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


async def test_bad_signature_is_rejected(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, [])
    body = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="<@UBOT00000>")
    async with client:
        response = await client.post(
            EVENTS_PATH,
            content=body,
            headers={
                "x-slack-request-timestamp": str(int(time.time())),
                "x-slack-signature": "v0=bad",
            },
        )
    assert response.status_code == 401


async def test_dedicated_surface_accepts_unqualified_event_and_interactive_routes(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, [])
    event = _event_body(type="app_home_opened", user="U1", channel="D1")
    interactive = urlencode(
        {"payload": json.dumps({"type": "view_submission", "team": {"id": TEAM_ID}})}
    ).encode()

    async with client:
        event_response = await client.post(
            "/surface/slack", content=event, headers=_sign(event, int(time.time()))
        )
        interactive_response = await client.post(
            "/surface/slack/interactive",
            content=interactive,
            headers=_sign(interactive, int(time.time())),
        )

    assert event_response.json() == {"ok": True, "ignored": True}
    assert interactive_response.json() == {"ok": True, "ignored": True}


async def test_shared_handshake_echoes_without_binding_a_workspace(
    db: None, tmp_path, monkeypatch
) -> None:
    """On the shared fleet the url_verification handshake echoes its challenge and binds no
    workspace — it carries no team, so it is answered before any team lookup or verification. An
    event for an unregistered team is rejected."""
    await _seed()
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    handshake = json.dumps({"type": "url_verification", "challenge": "shared-c"}).encode()
    event = _event_body(type="app_mention", user="U1", channel="C1", ts="1.0", text="hi")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        echoed = await client.post(EVENTS_PATH, content=handshake)
        unknown_team = await client.post(
            EVENTS_PATH, content=event, headers=_sign(event, int(time.time()))
        )
    assert echoed.json() == {"challenge": "shared-c"}
    assert unknown_team.status_code == 401
    assert current_workspace.get() is None
    async with workspace_tx() as connection:
        bindings = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_installation)
            )
        ).scalar_one()
    assert bindings == 0


def _sign_with(secret: str, body: bytes) -> dict[str, str]:
    ts = int(time.time())
    base = b"v0:" + str(ts).encode() + b":" + body
    return {
        "x-slack-request-timestamp": str(ts),
        "x-slack-signature": "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest(),
    }


def _install_state(store: CredentialStore, workspace_id: UUID, member_id: UUID) -> str:
    """A Fernet-sealed install handoff the surface's callback opens — the same seal the owner's
    `slack_connect` mints, carrying the workspace, owner, bot-token slot, and install marker."""
    return seal_credential_request(
        store.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=(slack.SLACK_BOT_TOKEN_SLOT,),
            payload=slack.SLACK_INSTALL_PAYLOAD,
        ),
    )


async def test_oauth_callback_installs_the_workspace(db: None, tmp_path, monkeypatch) -> None:
    """The "Add to Slack" callback exchanges the code for the workspace's bot token and lands all
    three pieces the events path needs: the token in the store, the team→workspace binding, and the
    identity record — presenting the deploy app's client id/secret and the redirect to Slack."""
    workspace_id, member_id = await _seed(member_email="owner@acme.com")
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _mock_transport(recorder, {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    sealed = _install_state(store, workspace_id, member_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"code": "the-code", "state": sealed},
        )
    assert response.status_code == 200
    assert "installed" in response.text
    assert await store.get(workspace_id, slack.SLACK_BOT_TOKEN_SLOT) == BOT_TOKEN
    assert await slack.read_identity(blob, workspace_id, BOT_TOKEN) == slack.SlackIdentity(
        bot_token_fingerprint=slack.bot_token_fingerprint(BOT_TOKEN),
        team_id=TEAM_ID,
        bot_user_id=BOT_USER_ID,
    )
    async with workspace_tx() as connection:
        binding = (
            await connection.execute(
                sa.select(tables.surface_installation.c.installation_id).where(
                    tables.surface_installation.c.workspace_id == workspace_id,
                    tables.surface_installation.c.surface == slack.SURFACE_SLACK,
                )
            )
        ).scalar_one()
    assert binding == slack.slack_installation_id(TEAM_ID)
    exchange = _fetches(recorder, slack.SLACK_OAUTH_ACCESS_URL)[0]
    form = {key: value[0] for key, value in parse_qs(exchange.content.decode()).items()}
    assert form["client_id"] == CLIENT_ID
    assert form["client_secret"] == CLIENT_SECRET
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == slack.slack_oauth_redirect_uri(PUBLIC_BASE_URL)


async def test_oauth_callback_declined_carries_no_workspace_and_reflects_no_error_param(
    db: None, tmp_path, monkeypatch
) -> None:
    """A declined authorization (an `error` query param, no sealed `state`) names no workspace, so
    the shared fleet rejects it at the identify boundary before any handler — a clean 401 that
    reflects no attacker-controllable markup."""
    await _seed()
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"error": "<script>alert(1)</script>"},
        )
    assert response.status_code == 401
    assert "<script>" not in response.text


async def test_oauth_callback_refuses_a_team_bound_elsewhere(
    db: None, tmp_path, monkeypatch
) -> None:
    """Installing a Slack team already connected to another workspace is refused (409) before any
    token is stored — the fleet-wide team↔workspace uniqueness holds through the callback's bind."""
    other_workspace, _ = await _seed()
    workspace_id, member_id = await _seed(member_email="owner@acme.com")
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await _register_slack(store, other_workspace, TEAM_ID)
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    sealed = _install_state(store, workspace_id, member_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"code": "the-code", "state": sealed},
        )
    assert response.status_code == 409
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, slack.SLACK_BOT_TOKEN_SLOT)


async def test_oauth_callback_refuses_a_tampered_state(db: None, tmp_path, monkeypatch) -> None:
    """A callback whose state does not open — tampered or expired — names no workspace, so the
    shared fleet rejects it at the identify boundary (401) and installs nothing."""
    await _seed()
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"code": "c", "state": "not-a-real-seal"},
        )
    assert response.status_code == 401
    async with workspace_tx() as connection:
        bindings = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_installation)
            )
        ).scalar_one()
    assert bindings == 0


async def test_oauth_callback_reports_a_rejected_code(db: None, tmp_path, monkeypatch) -> None:
    """An `oauth.v2.access` that returns `ok:false` (a reused or expired code) stores nothing and
    shows a retry rather than a broken install."""
    workspace_id, member_id = await _seed(member_email="owner@acme.com")

    def rejecting(request: httpx.Request) -> httpx.Response:
        if str(request.url).split("?")[0] == slack.SLACK_OAUTH_ACCESS_URL:
            return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    _patch_httpx(monkeypatch, httpx.MockTransport(rejecting))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    sealed = _install_state(store, workspace_id, member_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://slack") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"code": "stale", "state": sealed},
        )
    assert response.status_code == 502
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, slack.SLACK_BOT_TOKEN_SLOT)


async def test_shared_oauth_callback_binds_the_sealed_workspace(
    db: None, tmp_path, monkeypatch
) -> None:
    """On the shared fleet the callback resolves its workspace from the sealed state alone — no
    team, no signature — and installs into exactly that workspace."""
    workspace_id, member_id = await _seed(member_email="owner@acme.com")
    _patch_httpx(monkeypatch, _mock_transport([], {}))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    sealed = _install_state(store, workspace_id, member_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        response = await client.get(
            f"{EVENTS_PATH}/{slack.SLACK_OAUTH_CALLBACK_PATH}",
            params={"code": "the-code", "state": sealed},
        )
    assert response.status_code == 200
    assert await store.get(workspace_id, slack.SLACK_BOT_TOKEN_SLOT) == BOT_TOKEN
    assert current_workspace.get() is None
    async with workspace_tx() as connection:
        binding = (
            await connection.execute(
                sa.select(tables.surface_installation.c.workspace_id).where(
                    tables.surface_installation.c.installation_id
                    == slack.slack_installation_id(TEAM_ID)
                )
            )
        ).scalar_one()
    assert binding == workspace_id


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
                EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
            )
            assert response.status_code == 200
        opened = await client.post(
            EVENTS_PATH,
            content=home_opened,
            headers=_sign(home_opened, int(time.time())),
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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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
        response = await client.post(EVENTS_PATH, content=dm, headers=_sign(dm, int(time.time())))
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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
        )
    assert response.status_code == 200
    assert len(_fetches(recorder, slack.SLACK_CONVERSATIONS_REPLIES_URL)) == 1
    assert await _turn_inbound(workspace_id) == "<@UBOT00000> ping"


async def test_participating_thread_admits_unmentioned_replies_on_the_transcript(
    db: None, tmp_path, monkeypatch
) -> None:
    """The participation gate end to end: the first mention makes the thread a conversation and
    carries the ambient digest; from then on every member reply — un-mentioned, broadcast, or a
    second mention — is admitted with its plain body and no refetched digest (each landing on the
    still-live starting turn's inbound queue here, since no worker claims it), while replies in a
    foreign thread and top-level chatter stay ignored."""
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
                EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
            )
            assert response.json() == {"ok": True}
        for body in ignored:
            response = await client.post(
                EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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
        queued = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.body,
                    tables.inbound_message.c.idempotency_key,
                )
                .where(tables.inbound_message.c.workspace_id == workspace_id)
                .order_by(tables.inbound_message.c.seq)
            )
        ).all()
    assert queue_keys == [f"C1:{root}"]
    [turn] = turns
    assert turn.idempotency_key == "C1:1700000180.000400"
    assert turn.inbound == (
        f"{slack.AMBIENT_THREAD_HEADER}\n"
        "[2023-11-14 22:13] <@U1>: pre-mention chatter\n\n"
        "<@UBOT00000> take a look"
    )
    assert [(row.body, row.idempotency_key) for row in queued] == [
        ("and it happens on retries too", "C1:1700000240.000500"),
        ("broadcasting the reply", "C1:1700000300.000600"),
        ("<@UBOT00000> anything yet?", "C1:1700000360.000700"),
    ]
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
            EVENTS_PATH, content=reply, headers=_sign(reply, int(time.time()))
        )
        assert ignored.json() == {"ok": True, "ignored": True}
        admitted = await client.post(
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
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
        response = await client.post(EVENTS_PATH, content=dm, headers=_sign(dm, int(time.time())))
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
        "loading_messages": [slack.STATUS_THINKING_TEXT],
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


async def test_channel_persists_the_speaker_without_claiming_the_conversation(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, [], users={"UBEE": "bee@example.com"}
    )
    mention = _event_body(
        type="app_mention",
        user="UBEE",
        channel="C9",
        ts="10.0",
        text=f"<@{BOT_USER_ID}> connect my calendar",
    )
    async with client:
        response = await client.post(
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
        )
    assert response.status_code == 200
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.speaker_member_id,
                    tables.conversation.c.member_id,
                ).select_from(tables.turn.join(tables.conversation))
            )
        ).one()
    assert row.speaker_member_id == member_id
    assert row.member_id is None


async def test_shared_channel_foreign_team_message_is_guest_skipped(
    db: None, tmp_path, monkeypatch
) -> None:
    """A Slack Connect shared channel carries external-org members the app never serves. The
    top-level `team_id` is the bound, receiving workspace (Slack authorizes the installed app), so
    only the author's own team — `source_team`/`user_team`, present on shared-channel events —
    marks them foreign. A foreign author is a bystander: no users.info read, no member resolution,
    no turn, no error. A same-team author in the very same channel still admits their turn."""
    workspace_id, _ = await _seed(member_email="owner@example.com")
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    foreign = _event_body(
        type="app_mention",
        user="UEXT",
        channel="C1",
        ts="100.0",
        text=f"<@{BOT_USER_ID}> hi from another org",
        source_team="T0FOREIGN",
        user_team="T0FOREIGN",
    )
    same_team = _event_body(
        type="app_mention",
        user="U1",
        channel="C1",
        ts="200.0",
        text=f"<@{BOT_USER_ID}> hi from home",
        source_team=TEAM_ID,
        user_team=TEAM_ID,
    )
    async with client:
        skipped = await client.post(
            EVENTS_PATH, content=foreign, headers=_sign(foreign, int(time.time()))
        )
        assert skipped.json() == {"ok": True, "ignored": True}
        admitted = await client.post(
            EVENTS_PATH, content=same_team, headers=_sign(same_team, int(time.time()))
        )
        assert admitted.json() == {"ok": True}
    assert not _fetches(recorder, f"{slack.SLACK_USERS_INFO_URL}?user=UEXT")
    async with workspace_tx() as connection:
        keys = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.idempotency_key).where(
                        tables.turn.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert keys == ["C1:200.0"]


async def test_first_time_same_domain_dm_speaker_joins_as_a_member(
    db: None, tmp_path, monkeypatch
) -> None:
    """Only the owner onboards through the CLI: a teammate whose Slack-confirmed email shares the
    workspace's domain becomes a member on their first DM — the member row, the linked identity,
    and the conversation they own (their memory subject), all from one inbound event."""
    workspace_id, owner_id = await _seed(member_email="owner@example.com")
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, [], users={"UNEW": "New.Joiner@Example.com"}
    )
    dm = _event_body(
        type="message", channel_type="im", user="UNEW", channel="D7", ts="8.0", text="hi"
    )
    async with client:
        response = await client.post(EVENTS_PATH, content=dm, headers=_sign(dm, int(time.time())))
    assert response.status_code == 200
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "new.joiner@example.com",
                )
            )
        ).scalar_one()
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == slack.SURFACE_SLACK,
                    tables.surface_identity.c.external_id == "UNEW",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.queue_key == "D7"
                )
            )
        ).one()
    assert member_id != owner_id
    assert linked.member_id == member_id
    assert conversation.member_id == member_id


@pytest.mark.parametrize(
    ("email", "unconfirmed"),
    [("gigi@elsewhere.com", frozenset()), ("mallory@example.com", frozenset({"UOUT"}))],
    ids=["foreign-domain", "unconfirmed-email"],
)
async def test_dm_without_a_confirmed_same_domain_email_stays_unlinked(
    db: None, tmp_path, monkeypatch, email: str, unconfirmed: frozenset[str]
) -> None:
    """Neither a foreign-domain email nor one Slack has not confirmed grants membership: the DM is
    admitted as a shared, memberless conversation and no member row appears."""
    workspace_id, _ = await _seed(member_email="owner@example.com")
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, [], users={"UOUT": email}, unconfirmed=unconfirmed
    )
    dm = _event_body(
        type="message", channel_type="im", user="UOUT", channel="D8", ts="9.0", text="hey"
    )
    async with client:
        response = await client.post(EVENTS_PATH, content=dm, headers=_sign(dm, int(time.time())))
    assert response.status_code == 200
    async with workspace_tx() as connection:
        members = (
            (
                await connection.execute(
                    sa.select(tables.member.c.email).where(
                        tables.member.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.queue_key == "D8"
                )
            )
        ).one()
        admitted = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert members == ["owner@example.com"]
    assert conversation.member_id is None
    assert admitted == 1


async def test_confirmed_email_claims_the_dm_that_began_unconfirmed(
    db: None, tmp_path, monkeypatch
) -> None:
    """An unconfirmed email is a retryable state, not a verdict: the first DM lands memberless,
    and the DM after Slack confirms the address joins the speaker as a member, claims that same
    conversation — and its memory subject — as theirs, and lands on the live turn's inbound queue
    carrying them as its speaker."""
    workspace_id, _ = await _seed(member_email="owner@example.com")
    unconfirmed = {"UNEW"}
    _, client, _ = await _mount(
        monkeypatch,
        workspace_id,
        tmp_path,
        [],
        users={"UNEW": "new.joiner@example.com"},
        unconfirmed=unconfirmed,
    )
    first = _event_body(
        type="message", channel_type="im", user="UNEW", channel="D7", ts="8.0", text="hi"
    )
    second = _event_body(
        type="message", channel_type="im", user="UNEW", channel="D7", ts="9.0", text="me again"
    )
    async with client:
        await client.post(EVENTS_PATH, content=first, headers=_sign(first, int(time.time())))
        unconfirmed.clear()
        await client.post(EVENTS_PATH, content=second, headers=_sign(second, int(time.time())))
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "new.joiner@example.com",
                )
            )
        ).scalar_one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.queue_key == "D7"
                )
            )
        ).one()
        turn_speakers = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.speaker_member_id).order_by(tables.turn.c.seq)
                )
            )
            .scalars()
            .all()
        )
        queued = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.body,
                    tables.inbound_message.c.speaker_member_id,
                )
            )
        ).one()
    assert conversation.member_id == member_id
    assert turn_speakers == [None]
    assert (queued.body, queued.speaker_member_id) == ("me again", member_id)


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
            await client.post(EVENTS_PATH, content=dm, headers=_sign(dm, int(time.time())))
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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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
            EVENTS_PATH, content=shared, headers=_sign(shared, int(time.time()))
        )
        ignored = await client.post(
            EVENTS_PATH, content=edited, headers=_sign(edited, int(time.time()))
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
    connect_request: ConnectRequest | None = None,
    speaker_member_id: UUID | None = None,
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
                speaker_member_id=speaker_member_id,
                terminal=TerminalFrame(
                    status="done",
                    text=text,
                    tokens=1_234,
                    cost_micro_usd=1_234,
                    cache_percent=42,
                    model="claude-opus-4-8",
                    reasoning="high",
                    question=question,
                    connect_request=connect_request,
                ).model_dump(mode="json"),
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


def _shared_slack_transport(
    recorder: list[httpx.Request], tokens: dict[str, str]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        label = tokens.get(request.headers.get("authorization", ""))
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_USERS_INFO_URL and label is not None:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "user": {
                        "real_name": f"Member {label}",
                        "tz": "America/New_York",
                        "is_email_confirmed": True,
                        "profile": {"email": "shared@example.com"},
                    },
                },
            )
        if label is not None and url in (
            slack.SLACK_CONVERSATIONS_REPLIES_URL,
            slack.SLACK_CONVERSATIONS_HISTORY_URL,
        ):
            return httpx.Response(200, json={"ok": True, "messages": []})
        if url == slack.SLACK_ASSISTANT_STATUS_URL and label is not None:
            return httpx.Response(200, json={"ok": True})
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL and label is not None:
            return httpx.Response(200, json={"ok": True, "channel": f"C{label}", "ts": label})
        if url == slack.SLACK_FILES_GET_UPLOAD_URL and label is not None:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "upload_url": f"https://files.slack.com/upload/{label}",
                    "file_id": f"F{label}",
                },
            )
        if url in {"https://files.slack.com/upload/A", "https://files.slack.com/upload/B"}:
            return httpx.Response(200, text="OK")
        if url == slack.SLACK_FILES_COMPLETE_UPLOAD and label is not None:
            return httpx.Response(200, json={"ok": True, "files": [{"id": f"F{label}"}]})
        return httpx.Response(403, json={"ok": False, "error": "wrong_workspace_credential"})

    return httpx.MockTransport(handler)


async def test_shared_slack_rejects_an_unknown_installation_without_binding(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed(member_email="shared@example.com")
    store = await _store(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    await _write_identity(blob, workspace_id)
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _shared_slack_transport(recorder, {f"Bearer {BOT_TOKEN}": "ONE"}))
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    body = _event_body(
        type="message",
        user="USAME",
        channel="DSAME",
        channel_type="im",
        ts="1.0",
        text="unknown installation",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        response = await client.post(
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
        )

    assert response.status_code == 401
    assert response.text == "unauthorized"
    assert current_workspace.get() is None
    async with workspace_tx() as connection:
        turn_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
        registration_count = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_installation)
            )
        ).scalar_one()
    assert turn_count == 0
    assert registration_count == 0


async def test_shared_slack_routes_two_installations_without_crossing_state(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_a, member_a = await _seed(member_email="shared@example.com")
    workspace_b, member_b = await _seed(member_email="shared@example.com")
    token_a, token_b = "xoxb-a", "xoxb-b"
    team_a, team_b = "TA000001", "TB000001"
    bot_a, bot_b = "UA000001", "UB000001"
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    for workspace_id, bot_token in ((workspace_a, token_a), (workspace_b, token_b)):
        await store.put(workspace_id, slack.SLACK_BOT_TOKEN_SLOT, bot_token)
    blob = FilesystemBlobStore(root=tmp_path)
    await _write_identity(blob, workspace_a, token_a, team_a, bot_a)
    await _write_identity(blob, workspace_b, token_b, team_b, bot_b)
    await _register_slack(store, workspace_a, team_a)
    await _register_slack(store, workspace_b, team_b)
    recorder: list[httpx.Request] = []
    _patch_httpx(
        monkeypatch,
        _shared_slack_transport(
            recorder,
            {f"Bearer {token_a}": "A", f"Bearer {token_b}": "B"},
        ),
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    body_a = json.dumps(
        {
            "team_id": team_a,
            "event": {
                "type": "message",
                "user": "USAME",
                "channel": "DSAME",
                "channel_type": "im",
                "ts": "1.0",
                "text": "message-a",
            },
        }
    ).encode()
    body_b = json.dumps(
        {
            "team_id": team_b,
            "event": {
                "type": "message",
                "user": "USAME",
                "channel": "DSAME",
                "channel_type": "im",
                "ts": "1.0",
                "text": "message-b",
            },
        }
    ).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        accepted_a = await client.post(
            EVENTS_PATH, content=body_a, headers=_sign(body_a, int(time.time()))
        )
        bad_signature = await client.post(
            EVENTS_PATH, content=body_a, headers=_sign_with("not-the-deploy-secret", body_a)
        )
        accepted_b = await client.post(
            EVENTS_PATH, content=body_b, headers=_sign(body_b, int(time.time()))
        )
    assert accepted_a.status_code == 200
    assert bad_signature.status_code == 401
    assert bad_signature.text == "unauthorized"
    assert accepted_b.status_code == 200
    assert current_workspace.get() is None
    deadline = time.monotonic() + 5
    while len(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)) < 2:
        assert time.monotonic() < deadline, "both workspace statuses never reached Slack"
        await asyncio.sleep(0.01)
    statuses = _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    assert {request.headers["authorization"] for request in statuses} >= {
        f"Bearer {token_a}",
        f"Bearer {token_b}",
    }
    assert {key[0] for key in slack._THREAD_WRITERS} == {workspace_a, workspace_b}
    async with workspace_tx() as connection:
        routed = (
            await connection.execute(
                sa.select(tables.turn.c.workspace_id, tables.turn.c.inbound).where(
                    tables.turn.c.workspace_id.in_((workspace_a, workspace_b))
                )
            )
        ).all()
        identities = (
            await connection.execute(
                sa.select(
                    tables.surface_identity.c.workspace_id,
                    tables.surface_identity.c.member_id,
                    tables.surface_identity.c.external_id,
                ).where(
                    tables.surface_identity.c.workspace_id.in_((workspace_a, workspace_b)),
                    tables.surface_identity.c.surface == slack.SURFACE_SLACK,
                )
            )
        ).all()
        bindings = (
            await connection.execute(
                sa.select(
                    tables.surface_installation.c.workspace_id,
                    tables.surface_installation.c.installation_id,
                ).where(tables.surface_installation.c.surface == slack.SURFACE_SLACK)
            )
        ).all()
    assert len(routed) == 2
    assert {
        (row.workspace_id, "message-a" in row.inbound, "message-b" in row.inbound) for row in routed
    } == {
        (workspace_a, True, False),
        (workspace_b, False, True),
    }
    assert {tuple(row) for row in identities} == {
        (workspace_a, member_a, "USAME"),
        (workspace_b, member_b, "USAME"),
    }
    assert {tuple(row) for row in bindings} == {
        (workspace_a, slack.slack_installation_id(team_a)),
        (workspace_b, slack.slack_installation_id(team_b)),
    }

    await blob.put("artifacts/a/a.txt", b"A-FILE")
    await blob.put("artifacts/b/b.txt", b"B-FILE")
    turn_a = await _seed_done_turn(
        workspace_a,
        "CDELIVER:10.0",
        "reply-a",
        blob,
        artifact=True,
        artifact_name="a.txt",
        artifact_key="artifacts/a/a.txt",
        artifact_size=6,
        artifact_media_type="text/plain",
    )
    turn_b = await _seed_done_turn(
        workspace_b,
        "CDELIVER:10.0",
        "reply-b",
        blob,
        artifact=True,
        artifact_name="b.txt",
        artifact_key="artifacts/b/b.txt",
        artifact_size=6,
        artifact_media_type="text/plain",
    )
    await app.state.writeback_poller.drain()

    posts = _requests_to(recorder, slack.SLACK_CHAT_POST_MESSAGE_URL)
    posted = {json.loads(request.content)["text"]: request for request in posts}
    assert posted["reply-a"].headers["authorization"] == f"Bearer {token_a}"
    assert posted["reply-b"].headers["authorization"] == f"Bearer {token_b}"
    uploads = {
        str(request.url): request.content
        for request in recorder
        if str(request.url).startswith("https://files.slack.com/upload/")
    }
    assert uploads == {
        "https://files.slack.com/upload/A": b"A-FILE",
        "https://files.slack.com/upload/B": b"B-FILE",
    }
    completes = _requests_to(recorder, slack.SLACK_FILES_COMPLETE_UPLOAD)
    assert {
        (request.headers["authorization"], json.loads(request.content)["files"][0]["id"])
        for request in completes
    } == {(f"Bearer {token_a}", "FA"), (f"Bearer {token_b}", "FB")}
    async with workspace_tx() as connection:
        delivered = (
            await connection.execute(
                sa.select(tables.writeback.c.turn_id, tables.writeback.c.status).where(
                    tables.writeback.c.turn_id.in_((turn_a, turn_b))
                )
            )
        ).all()
    assert {tuple(row) for row in delivered} == {
        (turn_a, WRITEBACK_DELIVERED),
        (turn_b, WRITEBACK_DELIVERED),
    }


async def test_writeback_posts_block_kit_reply_and_streams_the_attachment(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
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
    assert reply["blocks"] == [
        {"type": "markdown", "text": "hi **there**"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": await _debug_footer(workspace_id, "C5:200.0", turn_id),
                }
            ],
        },
    ]

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


async def test_footer_stays_plain_without_a_public_base_url(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount_transport(
        monkeypatch,
        workspace_id,
        tmp_path,
        _mock_transport(recorder, {}, frozenset()),
        public_base_url=None,
    )
    await _seed_done_turn(workspace_id, "C5:200.0", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    footer = json.loads(posts[0].content)["blocks"][-1]
    assert footer == {"type": "context", "elements": [{"type": "mrkdwn", "text": FOOTER_LABEL}]}


@pytest.mark.parametrize("member_email", ["owner@customer.example", None])
async def test_footer_is_absent_outside_the_operator_workspace(
    db: None, tmp_path, monkeypatch, member_email: str | None
) -> None:
    workspace_id, _ = await _seed(member_email=member_email)
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    await _seed_done_turn(workspace_id, "C5:200.0", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    assert json.loads(posts[0].content)["blocks"] == [{"type": "markdown", "text": "hi"}]


@pytest.mark.parametrize(
    "info_response",
    [
        httpx.Response(200, json={"ok": True, "channel": {"id": "C5", "is_ext_shared": True}}),
        httpx.Response(
            200, json={"ok": True, "channel": {"id": "C5", "is_pending_ext_shared": True}}
        ),
        httpx.Response(200, json={"ok": True, "channel": {"id": "C5", "is_org_shared": True}}),
        httpx.Response(200, json={"ok": True, "channel": {"id": "C5", "is_shared": True}}),
        httpx.Response(200, json={"ok": False, "error": "channel_not_found"}),
    ],
    ids=["ext_shared", "pending_ext_shared", "org_shared", "is_shared", "info_unavailable"],
)
async def test_footer_is_absent_on_a_shared_channel_in_the_operator_workspace(
    db: None, tmp_path, monkeypatch, info_response: httpx.Response
) -> None:
    """The operator workspace withholds the accounting footer and debugger link on a Slack Connect
    or org-shared thread, where an outside guest would otherwise see the turn's cost — and fails
    closed, dropping the footer when conversations.info cannot prove the channel internal."""
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
    recorder: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_CONVERSATIONS_INFO_URL:
            return info_response
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(200, json={"ok": True, "channel": "C5", "ts": "999.100"})
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    app, _, blob = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(handler)
    )
    await _seed_done_turn(workspace_id, "C5:200.0", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    assert json.loads(posts[0].content)["blocks"] == [{"type": "markdown", "text": "hi"}]
    info = [r for r in recorder if str(r.url).split("?")[0] == slack.SLACK_CONVERSATIONS_INFO_URL]
    assert len(info) == 1


async def test_footer_renders_in_a_dm_settled_as_internal(db: None, tmp_path, monkeypatch) -> None:
    """A DM carries none of the shared flags, so conversations.info settles it as internal and the
    operator footer renders — the same read path every channel takes."""
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
    recorder: list[httpx.Request] = []
    app, _, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    turn_id = await _seed_done_turn(workspace_id, "D5", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url) == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 1
    footer = json.loads(posts[0].content)["blocks"][-1]
    assert footer == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": await _debug_footer(workspace_id, "D5", turn_id)}],
    }


def test_oauth_bot_scopes_match_the_byo_manifest_scopes() -> None:
    """The one-click OAuth scope list and the bring-your-own-app manifest must request the same bot
    scopes, or a token minted by one path lacks a scope the code assumes — e.g. `im:read`, which
    `_channel_is_externally_shared` needs to run `conversations.info` on a DM. Guards the two lists
    against drifting apart."""
    from ufo_ext_slack.tools import SLACK_APP_MANIFEST_TEMPLATE

    manifest_scopes = set(
        re.findall(r"^\s*-\s*([a-z_]+:[a-z._]+)\s*$", SLACK_APP_MANIFEST_TEMPLATE, re.M)
    )
    assert set(slack.SLACK_BOT_SCOPES) == manifest_scopes
    assert "im:read" in slack.SLACK_BOT_SCOPES


async def test_writeback_persists_slack_retry_after(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, _ = await _seed()

    def rate_limited(request: httpx.Request) -> httpx.Response:
        if str(request.url) == slack.SLACK_CHAT_POST_MESSAGE_URL:
            return httpx.Response(
                429,
                headers={"Retry-After": "23"},
                json={"ok": False, "error": "ratelimited"},
            )
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    app, _, blob = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(rate_limited)
    )
    turn_id = await _seed_done_turn(workspace_id, "C429:200.0", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    async with workspace_tx() as connection:
        writeback = (
            await connection.execute(
                sa.select(
                    tables.writeback.c.status,
                    tables.writeback.c.claim_expires_at,
                    tables.writeback.c.last_error,
                ).where(tables.writeback.c.turn_id == turn_id)
            )
        ).one()
    assert writeback.status == WRITEBACK_PENDING
    assert writeback.last_error == "chat.postMessage HTTP 429: ratelimited; retry_after_seconds=23"
    due = writeback.claim_expires_at.replace(tzinfo=UTC)
    assert 22 <= (due - datetime.now(UTC)).total_seconds() <= 23


async def test_writeback_ignores_an_oversize_slack_retry_after(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Retry-After too long to be a real delay (int() of 4300+ decimal digits raises on
    Python 3.12) is ignored: the row retries on the fixed backoff instead of erroring the post."""
    workspace_id, _ = await _seed()

    def rate_limited(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == slack.SLACK_CHAT_POST_MESSAGE_URL
        return httpx.Response(
            429,
            headers={"Retry-After": "9" * 5_000},
            json={"ok": False, "error": "ratelimited"},
        )

    app, _, blob = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(rate_limited)
    )
    turn_id = await _seed_done_turn(workspace_id, "C429:300.0", "hi", blob, artifact=False)

    await app.state.writeback_poller.drain()

    async with workspace_tx() as connection:
        writeback = (
            await connection.execute(
                sa.select(
                    tables.writeback.c.status,
                    tables.writeback.c.last_error,
                ).where(tables.writeback.c.turn_id == turn_id)
            )
        ).one()
    assert writeback.status == WRITEBACK_PENDING
    assert writeback.last_error == "chat.postMessage HTTP 429: ratelimited"


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


@pytest.mark.parametrize(
    ("question", "connect_request"),
    [
        (None, None),
        (None, ConnectRequest(provider="google_calendar")),
        (ASK_QUESTION, None),
    ],
)
async def test_invalid_blocks_reposts_once(
    db: None,
    tmp_path,
    monkeypatch,
    question: AskUserInput | None,
    connect_request: ConnectRequest | None,
) -> None:
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
    recorder: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_CONVERSATIONS_INFO_URL:
            return httpx.Response(
                200, json={"ok": True, "channel": {"id": "C5", "is_ext_shared": False}}
            )
        if url != slack.SLACK_CHAT_POST_MESSAGE_URL:
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
    turn_id = await _seed_done_turn(
        workspace_id,
        "C5:200.0",
        "hi **there**",
        blob,
        artifact=False,
        question=question,
        connect_request=connect_request,
    )

    await app.state.writeback_poller.drain()

    posts = [r for r in recorder if str(r.url).split("?")[0] == slack.SLACK_CHAT_POST_MESSAGE_URL]
    assert len(posts) == 2
    first = json.loads(posts[0].content)
    second = json.loads(posts[1].content)
    assert first["blocks"][-1]["type"] == "context"
    if question is None and connect_request is None:
        assert "blocks" not in second
        footer = await _debug_footer(workspace_id, "C5:200.0", turn_id)
        assert second["text"] == f"hi **there**\n\n{footer}"
    else:
        assert second["blocks"][0] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "hi **there**"},
        }
        if connect_request is not None:
            assert second["blocks"][-2]["elements"][0]["action_id"] == slack.CONNECT_ACTION_ID
        else:
            assert second["blocks"][1]["text"]["text"] == "*Need a decision*"
            assert [b["action_id"] for b in second["blocks"][-2]["elements"]] == [
                "ask:0:0",
                "ask:0:1",
            ]

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
            EVENTS_PATH, content=body, headers=_sign(body, int(time.time()))
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


async def test_status_follows_the_turn_pins_the_text_and_clears_at_terminal(
    db: None, tmp_path, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="ufo")
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
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
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
    await hub.publish(turn_id, TextDelta(text="Here is"))
    while len(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)) < 3:
        assert time.monotonic() < deadline, "generating status never reached Slack"
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
        "loading_messages": [slack.STATUS_THINKING_TEXT],
    }
    working = slack.STATUS_WORKING_TEXT.format(tool="bash")
    assert statuses[1]["status"] == working
    assert statuses[1]["loading_messages"] == [working]
    assert statuses[2]["status"] == slack.STATUS_GENERATING_TEXT
    assert statuses[-1] == {
        "channel_id": "C1",
        "thread_ts": "100.5",
        "status": slack.STATUS_CLEAR_TEXT,
    }
    assert not _requests_to(recorder, slack.SLACK_CHAT_POST_MESSAGE_URL)
    assert turn_id not in slack._STATUS_TASKS
    written = [
        r.ufo["status_text"] for r in caplog.records if r.message == "slack.thread_status.write"
    ]
    assert working in written
    assert slack.STATUS_GENERATING_TEXT in written
    assert written[-1] == slack.STATUS_CLEAR_TEXT


async def test_a_failed_status_write_lands_in_the_event_log(
    db: None, tmp_path, monkeypatch, caplog
) -> None:
    """Slack rejecting a status write must be observable — the failure event carries the Slack
    error, so a rejected `assistant.threads.setStatus` (wrong thread kind, missing feature) shows
    up in the log pipeline instead of dying in a best-effort task."""
    caplog.set_level(logging.INFO, logger="ufo")
    workspace_id, _ = await _seed()
    recorder: list[httpx.Request] = []
    inner = _mock_transport(recorder, {})

    def rejecting(request: httpx.Request) -> httpx.Response:
        if str(request.url).split("?")[0] == slack.SLACK_ASSISTANT_STATUS_URL:
            recorder.append(request)
            return httpx.Response(200, json={"ok": False, "error": "feature_not_enabled"})
        return inner.handler(request)

    _, client, _ = await _mount_transport(
        monkeypatch, workspace_id, tmp_path, httpx.MockTransport(rejecting), hub=InProcessHub()
    )
    mention = _event_body(
        type="app_mention", user="U1", channel="C1", ts="100.5", text="<@UBOT00000> hi"
    )
    async with client:
        response = await client.post(
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
        )
    assert response.status_code == 200
    async with workspace_tx() as connection:
        turn_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    task = slack._STATUS_TASKS.get(turn_id)
    if task is not None:
        await task
    failures = [r for r in caplog.records if r.message == "slack.thread_status.failed"]
    assert failures and "feature_not_enabled" in failures[0].ufo["error"]
    assert not any(record.message == "slack.thread_status.write" for record in caplog.records)


async def test_a_parked_turn_clears_the_status(db: None, tmp_path, monkeypatch) -> None:
    """A parked turn has no reply coming, so the status task clears the thread status itself. The
    tool-call publish waits for its status write first — a Parked published before any subscriber
    attaches drops the hub ring, and this turn never parks durably, so the poll would not end the
    tail."""
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
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
        )
    assert response.status_code == 200
    async with workspace_tx() as connection:
        turn_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    task = slack._STATUS_TASKS[turn_id]
    await hub.publish(turn_id, ToolCall(tool="bash", preview="{}"))
    deadline = time.monotonic() + 5
    while len(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)) < 2:
        assert time.monotonic() < deadline, "the status task never attached to the tail"
        await asyncio.sleep(0.01)
    await hub.publish(turn_id, Parked(message="spend cap reached"))
    await task
    statuses = [
        json.loads(r.content) for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ]
    assert statuses[-1]["status"] == slack.STATUS_CLEAR_TEXT


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
            EVENTS_PATH, content=mention, headers=_sign(mention, int(time.time()))
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
    """The thread has one status writer — the newest turn: an outrun first turn's late writes are
    skipped once the follow-up turn takes the thread over, while the owning turn's parked clear
    lands. The durable terminal poll is slowed so the first turn's tail ends only on the hub
    Terminal this test publishes, after the writer has moved."""
    workspace_id, _ = await _seed()
    monkeypatch.setattr(slack, "STATUS_UPDATE_MIN_SECONDS", 0.0)
    monkeypatch.setattr(hub_tail, "TERMINAL_POLL_SECONDS", 60.0)
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
    first_terminal = TerminalFrame(status="done", text="one")
    async with client:
        response = await client.post(
            EVENTS_PATH, content=first, headers=_sign(first, int(time.time()))
        )
        assert response.status_code == 200
        async with workspace_tx() as connection:
            first_id = (
                await connection.execute(
                    sa.select(tables.turn.c.id).where(tables.turn.c.workspace_id == workspace_id)
                )
            ).scalar_one()
        first_task = slack._STATUS_TASKS[first_id]
        deadline = time.monotonic() + 5
        while not any(
            json.loads(r.content)["status"] == slack.STATUS_WORKING_TEXT.format(tool="primer")
            for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
        ):
            await hub.publish(
                first_id, ToolCall(tool="primer", preview="{}", description="Priming the tail")
            )
            assert time.monotonic() < deadline, "the first turn's tail never started draining"
            await asyncio.sleep(0.01)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(status="done", terminal=first_terminal.model_dump(mode="json"))
                .where(tables.turn.c.id == first_id)
            )
        response = await client.post(
            EVENTS_PATH, content=second, headers=_sign(second, int(time.time()))
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

    await hub.publish(turns["C1:100.5"], Terminal(frame=first_terminal))
    await first_task
    statuses = [
        json.loads(r.content) for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ]
    assert all(s["status"] != slack.STATUS_CLEAR_TEXT for s in statuses)

    second_task = slack._STATUS_TASKS[turns["C1:101.0"]]
    await hub.publish(
        turns["C1:101.0"],
        ToolCall(tool="calendar", preview="{}", description="Checking the calendar"),
    )
    deadline = time.monotonic() + 5
    while not any(
        json.loads(r.content)["status"] == slack.STATUS_WORKING_TEXT.format(tool="calendar")
        for r in _requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)
    ):
        assert time.monotonic() < deadline, "the surviving turn never wrote its status"
        await asyncio.sleep(0.01)
    await hub.publish(turns["C1:101.0"], Parked(message="spend cap reached"))
    await second_task
    final = json.loads(_requests_to(recorder, slack.SLACK_ASSISTANT_STATUS_URL)[-1].content)
    assert final == {"channel_id": "C1", "thread_ts": "100.5", "status": slack.STATUS_CLEAR_TEXT}


def test_ask_blocks_render_title_every_question_and_choice_buttons() -> None:
    assert slack.slack_ask_blocks(None) is None
    single = ASK_QUESTION.questions[0]

    lone = slack.slack_ask_blocks(ASK_QUESTION)
    assert lone is not None
    assert [block["type"] for block in lone] == ["section", "section", "actions"]
    assert lone[0]["text"]["text"] == "*Need a decision*"
    assert lone[1]["text"]["text"] == "Ship it?"
    buttons = lone[2]["elements"]
    assert [b["text"]["text"] for b in buttons] == ["Ship", "Hold"]
    assert [b["action_id"] for b in buttons] == ["ask:0:0", "ask:0:1"]
    assert [b["value"] for b in buttons] == ["Ship", "Hold"]

    pair = slack.slack_ask_blocks(
        AskUserInput(
            title="t",
            questions=(
                single.model_copy(
                    update={
                        "header": "Release",
                        "options": (
                            QuestionOption(label="Ship", description="cut it now"),
                            QuestionOption(label="Hold"),
                        ),
                    }
                ),
                AskQuestion(
                    question="Name the tag?",
                    options=(QuestionOption(label="v1", description="the usual"),),
                    free_text_only=True,
                ),
            ),
        )
    )
    assert pair is not None
    assert [block["type"] for block in pair] == ["section", "section", "actions", "section"]
    assert pair[1]["text"]["text"] == "*Release* — Ship it?\n• Ship — cut it now"
    assert [b["action_id"] for b in pair[2]["elements"]] == ["ask:0:0", "ask:0:1"]
    assert [b["value"] for b in pair[2]["elements"]] == ["Ship · Ship it?", "Hold · Ship it?"]
    assert pair[3]["text"]["text"] == "Name the tag?\n• v1 — the usual"

    multi = slack.slack_ask_blocks(
        AskUserInput(title="t", questions=(single.model_copy(update={"multi_select": True}),))
    )
    assert multi is not None
    assert [block["type"] for block in multi] == ["section", "section"]
    assert (
        multi[1]["text"]["text"]
        == "Ship it?\n• Ship\n• Hold\n_Select all that apply — answer by replying in this thread._"
    )

    for richer in (
        AskUserInput(title="t", questions=(AskQuestion(question="Ship it?"),)),
        AskUserInput(title="t", questions=(single.model_copy(update={"allow_attachments": True}),)),
        AskUserInput(
            title="t",
            questions=(
                AskQuestion(
                    question="q",
                    options=tuple(
                        QuestionOption(label=f"o{i}") for i in range(slack.MAX_ANSWER_BUTTONS + 1)
                    ),
                ),
            ),
        ),
    ):
        rendered = slack.slack_ask_blocks(richer)
        assert rendered is not None
        assert [block["type"] for block in rendered] == ["section", "section"]


async def test_question_writeback_posts_answer_buttons(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed(member_email=OPERATOR_OWNER_EMAIL)
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
    assert reply["blocks"][1]["text"]["text"] == "*Need a decision*"
    assert reply["blocks"][2]["text"]["text"] == "Ship it?"
    actions = reply["blocks"][3]
    assert actions["type"] == "actions"
    assert [b["text"]["text"] for b in actions["elements"]] == ["Ship", "Hold"]
    assert [b["action_id"] for b in actions["elements"]] == ["ask:0:0", "ask:0:1"]
    assert [b["value"] for b in actions["elements"]] == ["Ship", "Hold"]
    assert reply["blocks"][-1]["type"] == "context"


@dataclass(frozen=True)
class _ConnectProvider:
    provider: str = "google_calendar"
    host: str = "calendar.example.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://oauth.example.test/authorize?state={state}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id="calendar-account")


async def test_connect_writeback_keeps_oauth_private_and_checks_the_requester(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    assert member_id is not None
    recorder: list[httpx.Request] = []
    app, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    flow = ConnectFlow(
        providers={"google_calendar": _ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface=slack.SURFACE_SLACK,
                external_id="U9",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    turn_id = await _seed_done_turn(
        workspace_id,
        "C5:200.0",
        "Use the private connection control.",
        blob,
        artifact=False,
        connect_request=ConnectRequest(provider="google_calendar"),
        speaker_member_id=member_id,
    )
    try:
        await app.state.writeback_poller.drain()
        public = json.loads(_requests_to(recorder, slack.SLACK_CHAT_POST_MESSAGE_URL)[0].content)
        assert "oauth.example.test" not in json.dumps(public)
        assert public["blocks"][1]["elements"][0] == {
            "type": "button",
            "text": {"type": "plain_text", "text": "Connect google_calendar"},
            "action_id": slack.CONNECT_ACTION_ID,
            "value": str(turn_id),
        }
        wrong = _click_body(action_id=slack.CONNECT_ACTION_ID, value=str(turn_id), user="U8")
        right = _click_body(action_id=slack.CONNECT_ACTION_ID, value=str(turn_id), user="U9")
        async with client:
            await client.post(INTERACTIVE_PATH, content=wrong, headers=_signed_form(wrong))
            await asyncio.gather(*slack._REWRITE_TASKS)
            await client.post(INTERACTIVE_PATH, content=right, headers=_signed_form(right))
            await asyncio.gather(*slack._REWRITE_TASKS)
            await client.post(INTERACTIVE_PATH, content=right, headers=_signed_form(right))
            await asyncio.gather(*slack._REWRITE_TASKS)
    finally:
        install_connect_flow(None)
    private = [
        json.loads(request.content)
        for request in _requests_to(recorder, slack.SLACK_CHAT_POST_EPHEMERAL_URL)
    ]
    assert private[0]["text"] == "This connection request is not available to you."
    assert "https://oauth.example.test/authorize" in private[1]["text"]
    assert private[2]["text"] == private[1]["text"]
    assert [(p["channel"], p["thread_ts"]) for p in private] == [("C5", "200.0")] * 3
    assert [p["user"] for p in private] == ["U8", "U9", "U9"]


async def test_dm_connect_click_posts_the_link_unthreaded(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    assert member_id is not None
    recorder: list[httpx.Request] = []
    _, client, blob = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    flow = ConnectFlow(
        providers={"google_calendar": _ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface=slack.SURFACE_SLACK,
                external_id="U9",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    turn_id = await _seed_done_turn(
        workspace_id,
        "D5",
        "Use the private connection control.",
        blob,
        artifact=False,
        connect_request=ConnectRequest(provider="google_calendar"),
        speaker_member_id=member_id,
    )
    click = _click_body(
        action_id=slack.CONNECT_ACTION_ID, value=str(turn_id), user="U9", channel="D5", thread=None
    )
    try:
        async with client:
            await client.post(INTERACTIVE_PATH, content=click, headers=_signed_form(click))
            await asyncio.gather(*slack._REWRITE_TASKS)
    finally:
        install_connect_flow(None)
    private = [
        json.loads(request.content)
        for request in _requests_to(recorder, slack.SLACK_CHAT_POST_EPHEMERAL_URL)
    ]
    assert len(private) == 1
    assert "https://oauth.example.test/authorize" in private[0]["text"]
    assert private[0]["channel"] == "D5"
    assert private[0]["user"] == "U9"
    assert "thread_ts" not in private[0]


CLICK_MESSAGE_BLOCKS: list[dict[str, object]] = [
    {"type": "markdown", "text": "Ship it? (Ship / Hold)", "block_id": "b-md"},
    {
        "type": "actions",
        "block_id": "b-ask-0",
        "elements": [{"type": "button", "action_id": "ask:0:0", "value": "Ship"}],
    },
]


def _click_body(
    action_id: str = "ask:0:0",
    value: str = "Ship",
    user: str = "U9",
    channel: str = "C5",
    thread: str | None = "200.0",
    blocks: list[dict[str, object]] | None = None,
    block_id: str = "b-ask-0",
) -> bytes:
    message: dict[str, object] = {
        "ts": "999.100",
        "text": "Ship it? (Ship / Hold)",
        "blocks": CLICK_MESSAGE_BLOCKS if blocks is None else blocks,
    }
    if thread is not None:
        message["thread_ts"] = thread
    payload = {
        "type": "block_actions",
        "team": {"id": TEAM_ID},
        "user": {"id": user},
        "channel": {"id": channel},
        "message": message,
        "actions": [{"action_id": action_id, "value": value, "block_id": block_id}],
        "response_url": RESPONSE_URL,
    }
    return urlencode({"payload": json.dumps(payload)}).encode()


def _signed_form(body: bytes) -> dict[str, str]:
    return {
        **_sign(body, int(time.time())),
        "content-type": "application/x-www-form-urlencoded",
    }


async def _seed_answer_conversation(workspace_id: UUID, queue_key: str = "C5:200.0") -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
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
    return conversation_id


async def test_dm_answer_click_claims_the_conversation_for_its_resolved_member(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    assert member_id is not None
    conversation_id = await _seed_answer_conversation(workspace_id, "D5")
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, recorder, users={"U9": "bee@example.com"}
    )
    click = _click_body(channel="D5", thread=None)

    async with client:
        response = await client.post(INTERACTIVE_PATH, content=click, headers=_signed_form(click))

    assert response.status_code == 200
    async with workspace_tx() as connection:
        conversation_member, turn_speaker = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.member_id,
                    tables.turn.c.speaker_member_id,
                )
                .select_from(
                    tables.conversation.join(
                        tables.turn,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                )
                .where(tables.conversation.c.id == conversation_id)
            )
        ).one()
    assert conversation_member == member_id
    assert turn_speaker == member_id
    assert len(_fetches(recorder, slack.SLACK_USERS_INFO_URL)) == 1


async def test_shared_interactive_routes_by_registered_team(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    conversation_id = await _seed_answer_conversation(workspace_id)
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _mock_transport(recorder, {"U9": "bee@example.com"}))
    store = await _store(workspace_id)
    await _register_slack(store, workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    await _write_identity(blob, workspace_id)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (slack_manifest(),),
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        PUBLIC_BASE_URL,
    )
    click = _click_body()
    unknown = urlencode(
        {"payload": json.dumps({"type": "view_submission", "team": {"id": "TUNKNOWN"}})}
    ).encode()
    org_install = urlencode(
        {
            "payload": json.dumps(
                {
                    "type": "view_submission",
                    "team": None,
                    "api_app_id": "A0000001",
                    "enterprise": {"id": "E0000001"},
                }
            )
        }
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        accepted = await client.post(INTERACTIVE_PATH, content=click, headers=_signed_form(click))
        unknown_response = await client.post(
            INTERACTIVE_PATH, content=unknown, headers=_signed_form(unknown)
        )
        org_response = await client.post(
            INTERACTIVE_PATH, content=org_install, headers=_signed_form(org_install)
        )

    assert accepted.status_code == 200
    assert unknown_response.status_code == 401
    assert org_response.status_code == 401
    assert current_workspace.get() is None
    async with workspace_tx() as connection:
        routed = (
            await connection.execute(
                sa.select(
                    tables.turn.c.workspace_id,
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.conversation.c.id.label("conversation_id"),
                    tables.conversation.c.member_id,
                )
                .select_from(
                    tables.turn.join(
                        tables.conversation,
                        tables.conversation.c.id == tables.turn.c.conversation_id,
                    )
                )
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).one()
    assert routed.workspace_id == workspace_id
    assert routed.inbound == "[Answered by <@U9> via button] Ship"
    assert routed.speaker_member_id == member_id
    assert routed.conversation_id == conversation_id
    assert routed.member_id is None


async def test_interactive_before_install_is_refused(db: None, tmp_path, monkeypatch) -> None:
    """An interactivity click for a workspace with no installed identity is refused (503) and admits
    no turn — the install writes identity, the click never derives it."""
    workspace_id, _ = await _seed()
    await _seed_answer_conversation(workspace_id)
    recorder: list[httpx.Request] = []
    _, client, blob = await _mount_transport(
        monkeypatch,
        workspace_id,
        tmp_path,
        _mock_transport(recorder, {}),
        identity=False,
    )
    click = _click_body()
    async with client:
        response = await client.post(INTERACTIVE_PATH, content=click, headers=_signed_form(click))
    assert response.status_code == 503
    assert await slack.read_identity(blob, workspace_id, BOT_TOKEN) is None
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one() == 0


async def test_first_click_wins_and_alone_rewrites_the_message(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id, _ = await _seed()
    await _seed_answer_conversation(workspace_id)
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    winner = _click_body()
    async with client:
        unsigned = await client.post(INTERACTIVE_PATH, content=winner)
        assert unsigned.status_code == 401
        first = await client.post(INTERACTIVE_PATH, content=winner, headers=_signed_form(winner))
        assert first.status_code == 200
        await asyncio.gather(*slack._REWRITE_TASKS)
        loser = _click_body(value="Hold", user="U8")
        second = await client.post(INTERACTIVE_PATH, content=loser, headers=_signed_form(loser))
        assert second.status_code == 200
        await asyncio.gather(*slack._REWRITE_TASKS)
        foreign = _click_body(action_id="other:0")
        ignored = await client.post(
            INTERACTIVE_PATH, content=foreign, headers=_signed_form(foreign)
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
    assert turns[0].idempotency_key == "C5:200.0:999.100:answer:0"
    assert queue_key == "C5:200.0"

    rewrites = _requests_to(recorder, slack.SLACK_CHAT_UPDATE_URL)
    assert len(rewrites) == 1
    rewrite = json.loads(rewrites[0].content)
    assert rewrite["channel"] == "C5"
    assert rewrite["ts"] == "999.100"
    assert rewrite["blocks"][0] == {
        "type": "markdown",
        "text": "Ship it? (Ship / Hold)",
        "block_id": "b-md",
    }
    answered = rewrite["blocks"][1]
    assert answered["type"] == "context"
    assert "Answered by <@U9>" in answered["elements"][0]["text"]
    assert "Ship" in answered["elements"][0]["text"]


async def test_each_question_row_takes_its_own_answer(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    await _seed_answer_conversation(workspace_id)
    recorder: list[httpx.Request] = []
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    two_rows: list[dict[str, object]] = [
        {"type": "markdown", "text": "Two questions", "block_id": "b-md"},
        {
            "type": "actions",
            "block_id": "b-ask-0",
            "elements": [{"type": "button", "action_id": "ask:0:0", "value": "Ship"}],
        },
        {
            "type": "actions",
            "block_id": "b-ask-1",
            "elements": [{"type": "button", "action_id": "ask:1:0", "value": "v2 · Tag?"}],
        },
    ]
    second = _click_body(
        action_id="ask:1:0", value="v2 · Tag?", blocks=two_rows, block_id="b-ask-1"
    )
    first = _click_body(action_id="ask:0:0", value="Ship", blocks=two_rows, block_id="b-ask-0")
    async with client:
        await client.post(INTERACTIVE_PATH, content=second, headers=_signed_form(second))
        await asyncio.gather(*slack._REWRITE_TASKS)
        await client.post(INTERACTIVE_PATH, content=first, headers=_signed_form(first))
        await asyncio.gather(*slack._REWRITE_TASKS)

    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.idempotency_key).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).all()
        arrivals = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.body, tables.inbound_message.c.idempotency_key
                ).where(tables.inbound_message.c.workspace_id == workspace_id)
            )
        ).all()
    assert [(turn.idempotency_key, turn.inbound) for turn in turns] == [
        ("C5:200.0:999.100:answer:1", "[Answered by <@U9> via button] v2 · Tag?")
    ]
    assert [(arrival.idempotency_key, arrival.body) for arrival in arrivals] == [
        ("C5:200.0:999.100:answer:0", "[Answered by <@U9> via button] Ship")
    ]

    rewrites = [
        json.loads(request.content)
        for request in _requests_to(recorder, slack.SLACK_CHAT_UPDATE_URL)
    ]
    assert len(rewrites) == 2
    assert [block["type"] for block in rewrites[0]["blocks"]] == ["markdown", "actions", "context"]
    assert rewrites[0]["blocks"][1]["block_id"] == "b-ask-0"
    assert "v2 · Tag?" in rewrites[0]["blocks"][2]["elements"][0]["text"]
    assert [block["type"] for block in rewrites[1]["blocks"]] == ["markdown", "context", "actions"]
    assert "Ship" in rewrites[1]["blocks"][1]["elements"][0]["text"]
