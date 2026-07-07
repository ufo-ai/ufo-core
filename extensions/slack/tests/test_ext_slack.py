"""The Slack extension end to end on the surface seam: signature verification and Block Kit
rendering as pure functions, then the real handlers through mounted ingest and the core writeback
poller — inbound files streamed into the workspace, the reply posted as a Block Kit message, and a
shared file streamed to Slack's chunked external-upload API. Slack's HTTP is a MockTransport (a
dependency stand-in); every assertion reads the durable rows and blobs core wrote, or the exact
requests the handlers emitted."""

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import selfhost_ext_slack.surface as slack
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from selfhost_ext_slack.manifest import manifest as slack_manifest

from selfhost.artifact_token import verify_artifact_token
from selfhost.blob import BlobNotFound, FilesystemBlobStore
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.loader import skill_registry
from selfhost.ext.surface import WRITEBACK_DELIVERED, WRITEBACK_PENDING, workspace_key
from selfhost.hub import InProcessHub
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame
from selfhost.serve import _mount_surfaces

TEAM_ID = "T0000001"
BOT_USER_ID = "UBOT00000"
SIGNING_SECRET = "signing-secret"
BOT_TOKEN = "xoxb-test"
UPLOAD_URL = "https://files.slack.com/upload/session-1"
ARTIFACT_SECRET = "artifact-token-secret"
PUBLIC_BASE_URL = "https://selfhost.example.test"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _mock_transport(recorder: list[httpx.Request], users: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        url = str(request.url).split("?")[0]
        if url == slack.SLACK_USERS_INFO_URL:
            email = users.get(str(request.url.params.get("user")))
            profile = {"email": email} if email else {}
            return httpx.Response(200, json={"ok": True, "user": {"profile": profile}})
        if url == slack.SLACK_CHAT_POST_MESSAGE_URL:
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
    real = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=transport, **kwargs)

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
    monkeypatch: pytest.MonkeyPatch, workspace_id: UUID, tmp_path, transport: httpx.MockTransport
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
        InProcessHub(),
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
):
    return await _mount_transport(
        monkeypatch, workspace_id, tmp_path, _mock_transport(recorder, users or {})
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


def test_thread_keying_and_gating() -> None:
    assert slack.slack_thread_key("C1", "100.5", is_dm=False) == "C1:100.5"
    assert slack.slack_thread_key("D1", "100.5", is_dm=True) == "D1"
    assert slack.slack_message_gated({"type": "app_mention"}, BOT_USER_ID, is_dm=False)
    assert slack.slack_message_gated({"type": "message"}, BOT_USER_ID, is_dm=True)
    assert not slack.slack_message_gated({"type": "message", "text": "hi"}, BOT_USER_ID, False)
    assert slack.slack_message_gated(
        {"type": "message", "text": f"<@{BOT_USER_ID}> hi"}, BOT_USER_ID, False
    )


def test_slack_app_setup_skill_parses_indexes_and_names_the_real_route_and_slots() -> None:
    registry = skill_registry((slack_manifest(),))
    index = dict(registry.index())
    assert "slack-app-setup" in index
    body = registry.named("slack-app-setup").instructions
    assert "/surface/slack" in body
    for slot in (
        slack.SLACK_BOT_TOKEN_SLOT,
        slack.SLACK_SIGNING_SECRET_SLOT,
        slack.SLACK_BOT_USER_ID_SLOT,
        slack.SLACK_TEAM_ID_SLOT,
    ):
        assert f"selfhost credential set {slot}" in body


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


async def test_one_mention_admits_exactly_one_turn(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, _ = await _seed()
    _, client, _ = await _mount(monkeypatch, workspace_id, tmp_path, [])
    mention = "<@UBOT00000> hi"
    deliveries = [
        _event_body(type="app_mention", user="U1", channel="C1", ts="100.5", text=mention),
        _event_body(type="message", user="U1", channel="C1", ts="100.5", text=mention),
    ]
    async with client:
        for body in deliveries:
            response = await client.post(
                "/surface/slack", content=body, headers=_sign(body, int(time.time()))
            )
            assert response.status_code == 200
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


async def test_dm_links_member_by_email(db: None, tmp_path, monkeypatch) -> None:
    workspace_id, member_id = await _seed(member_email="bee@example.com")
    _, client, _ = await _mount(
        monkeypatch, workspace_id, tmp_path, [], users={"UBEE": "bee@example.com"}
    )
    dm = _event_body(
        type="message", channel_type="im", user="UBEE", channel="D9", ts="7.0", text="hey"
    )
    async with client:
        response = await client.post(
            "/surface/slack", content=dm, headers=_sign(dm, int(time.time()))
        )
    assert response.status_code == 200
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
    assert linked.member_id == member_id
    assert conversation.member_id == member_id


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
                terminal=TerminalFrame(status="done", text=text).model_dump(mode="json"),
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
    assert complete_body["thread_ts"] == "999.100"
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
