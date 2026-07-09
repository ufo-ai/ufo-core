"""The setup extension end to end on the surface seam: bearer auth against the gateway token
contract, the Slack-connect state machine over real credential rows and the slack surface's
verification marker, credential saves that derive the ids from `auth.test` over a MockTransport,
and the portal page's self-containment. Every assertion reads the durable credential rows and blobs
core wrote, or the exact requests the handlers emitted."""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_setup.surface as setup
import yaml
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_setup.manifest import manifest as setup_manifest
from ufo_ext_slack.manifest import manifest as slack_manifest
from ufo_ext_slack.surface import signing_secret_fingerprint, url_verified_blob_key

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.hub import InProcessHub
from ufo.schema import tables
from ufo.serve import _mount_surfaces

SECRET = "token-secret"
PUBLIC_BASE_URL = "https://acme-1a2b.example.test"
ARTIFACT_SECRET = "artifact-token-secret"
TEAM_ID = "T0AAAAAAA"
BOT_USER_ID = "U0BBBBBBB"
OWNER_EMAIL = "owner@example.com"
MEMBER_EMAIL = "teammate@example.com"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _mint(secret: str, workspace_id: UUID, email: str, exp: int) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"ws": str(workspace_id), "email": email, "exp": exp}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _future() -> int:
    return int(datetime.now(tz=UTC).timestamp()) + 3600


def _auth_test_transport(
    recorder: list[httpx.Request],
    *,
    ok: bool = True,
    error: str = "",
    team_id: str = TEAM_ID,
    user_id: str = BOT_USER_ID,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if str(request.url) == setup.SLACK_AUTH_TEST_URL:
            if ok:
                body = {
                    "ok": True,
                    "team_id": team_id,
                    "user_id": user_id,
                    "team": "acme",
                    "user": "ufo",
                }
            else:
                body = {"ok": False, "error": error}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(setup.httpx, "AsyncClient", factory)


async def _seed(*, extra_members: tuple[str, ...] = ()) -> UUID:
    """A workspace whose owner (the earliest member) is OWNER_EMAIL, plus any later joiners — so the
    setup surface's owner gate has a real owner to check against, as a provisioned tenant does."""
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for offset, email in enumerate((OWNER_EMAIL, *extra_members)):
            created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=offset)
            await connection.execute(
                sa.insert(tables.member).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    email=email,
                    created_at=created,
                    updated_at=created,
                )
            )
    return workspace_id


async def _mount(
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: UUID,
    tmp_path,
    recorder: list[httpx.Request] | None = None,
    transport: httpx.MockTransport | None = None,
    base_url: str | None = PUBLIC_BASE_URL,
):
    monkeypatch.setenv(setup.UFO_TOKEN_SECRET_ENV, SECRET)
    _patch_httpx(
        monkeypatch, transport or _auth_test_transport(recorder if recorder is not None else [])
    )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_surfaces(
        app,
        (setup_manifest(),),
        workspace_id,
        store,
        blob,
        InProcessHub(),
        StubDbos(),
        ARTIFACT_SECRET,
        base_url,
    )
    # https base so the secure setup cookie is stored and re-sent by the client's jar.
    client = AsyncClient(transport=ASGITransport(app=app), base_url="https://setup")
    return client, store, blob


async def _sign_in(client: AsyncClient, workspace_id: UUID, email: str = OWNER_EMAIL) -> None:
    token = _mint(SECRET, workspace_id, email, _future())
    page = await client.get(f"/surface/setup?token={token}")
    assert page.status_code == 200
    assert setup.SETUP_COOKIE in page.cookies


async def test_valid_token_binds_the_cookie_and_authenticates_the_apis(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        assert (await client.get("/surface/setup/status")).status_code == 401
        await _sign_in(client, workspace_id)
        status = await client.get("/surface/setup/status")
    assert status.status_code == 200
    assert status.json()["slack"]["state"] == "not_configured"


async def test_foreign_expired_and_missing_tokens_are_rejected(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    foreign = _mint(SECRET, uuid4(), "owner@example.com", _future())
    expired = _mint(SECRET, workspace_id, "owner@example.com", _future() - 7200)
    async with client:
        page = await client.get(f"/surface/setup?token={foreign}")
        assert page.status_code == 200
        assert setup.SETUP_COOKIE not in page.cookies
        assert (await client.get(f"/surface/setup/status?token={foreign}")).status_code == 401
        assert (await client.get(f"/surface/setup/status?token={expired}")).status_code == 401
        assert (
            await client.post("/surface/setup/slack/test", params={"token": "junk"})
        ).status_code == 401


async def test_manifest_yaml_prefills_the_events_url_and_matches_the_slack_skill(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.get("/surface/setup/slack/manifest?name=acme bot")
        bad_name = await client.get("/surface/setup/slack/manifest?name=<script>")
    assert response.status_code == 200
    assert bad_name.status_code == 400
    served = yaml.safe_load(response.text)
    assert served["display_information"]["name"] == "acme bot"
    assert served["settings"]["event_subscriptions"]["request_url"] == (
        f"{PUBLIC_BASE_URL}/surface/slack"
    )
    skill_body = skill_registry((slack_manifest(),)).named("slack-app-setup").instructions
    block = re.search(r"```yaml\n(.*?)```", skill_body, re.DOTALL)
    assert block is not None
    skill_yaml = yaml.safe_load(
        block.group(1)
        .replace("<bot display name>", "acme bot")
        .replace("<public_base_url>/surface/slack", f"{PUBLIC_BASE_URL}/surface/slack")
    )
    assert served == skill_yaml


async def test_manifest_yaml_409s_without_a_public_base_url(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path, base_url=None)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.get("/surface/setup/slack/manifest")
        status = await client.get("/surface/setup/status")
    assert response.status_code == 409
    assert status.json()["base_url_set"] is False
    assert status.json()["events_url"] is None


async def test_save_derives_the_ids_from_auth_test_and_stores_all_four_slots(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    recorder: list[httpx.Request] = []
    client, store, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-abc123", "slack_signing_secret": "shhh"},
        )
    assert response.status_code == 200
    assert response.json()["slack"]["state"] == "pending"
    assert len(recorder) == 1
    assert recorder[0].headers["authorization"] == "Bearer xoxb-abc123"
    assert await store.get(workspace_id, "slack_bot_token") == "xoxb-abc123"
    assert await store.get(workspace_id, "slack_signing_secret") == "shhh"
    assert await store.get(workspace_id, "slack_team_id") == TEAM_ID
    assert await store.get(workspace_id, "slack_bot_user_id") == BOT_USER_ID


async def test_save_with_explicit_ids_never_calls_slack(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed()
    recorder: list[httpx.Request] = []
    client, store, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={
                "slack_bot_token": "xoxb-abc123",
                "slack_signing_secret": "shhh",
                "slack_team_id": TEAM_ID,
                "slack_bot_user_id": BOT_USER_ID,
            },
        )
    assert response.status_code == 200
    assert recorder == []
    assert await store.get(workspace_id, "slack_team_id") == TEAM_ID


async def test_invalid_values_are_rejected_before_any_write_or_network(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    recorder: list[httpx.Request] = []
    client, _store, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    async with client:
        await _sign_in(client, workspace_id)
        bad_prefix = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxp-user-token", "slack_signing_secret": "shhh"},
        )
        bad_team = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-ok", "slack_team_id": "team-1"},
        )
        blank = await client.post("/surface/setup/slack/credentials", json={})
    assert bad_prefix.status_code == 400
    assert bad_prefix.json()["field"] == "slack_bot_token"
    assert bad_team.status_code == 400
    assert bad_team.json()["field"] == "slack_team_id"
    assert blank.status_code == 400
    assert recorder == []
    async with workspace_tx() as connection:
        stored = (await connection.execute(sa.select(tables.credential.c.slot))).all()
    assert stored == []


async def test_rejected_token_surfaces_the_auth_test_diagnosis_and_writes_nothing(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    recorder: list[httpx.Request] = []
    client, _store, _ = await _mount(
        monkeypatch,
        workspace_id,
        tmp_path,
        transport=_auth_test_transport(recorder, ok=False, error="invalid_auth"),
    )
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-revoked", "slack_signing_secret": "shhh"},
        )
    assert response.status_code == 400
    assert response.json()["field"] == "slack_bot_token"
    assert "invalid_auth" in response.json()["error"]
    async with workspace_tx() as connection:
        stored = (await connection.execute(sa.select(tables.credential.c.slot))).all()
    assert stored == []


async def test_blank_fields_keep_the_stored_values(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed()
    client, store, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    for slot, value in (
        ("slack_bot_token", "xoxb-old"),
        ("slack_signing_secret", "old-secret"),
        ("slack_team_id", TEAM_ID),
        ("slack_bot_user_id", BOT_USER_ID),
    ):
        await store.put(workspace_id, slot, value)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials", json={"slack_signing_secret": "new-secret"}
        )
    assert response.status_code == 200
    assert await store.get(workspace_id, "slack_bot_token") == "xoxb-old"
    assert await store.get(workspace_id, "slack_signing_secret") == "new-secret"


async def test_status_walks_not_configured_pending_connected(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, store, blob = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        await _sign_in(client, workspace_id)
        fresh = (await client.get("/surface/setup/status")).json()
        assert fresh["slack"]["state"] == "not_configured"
        for slot in setup.SLACK_SLOTS:
            assert slot in fresh["slack"]["message"]
            await store.put(workspace_id, slot, "value")
        stored = (await client.get("/surface/setup/status")).json()
        assert stored["slack"]["state"] == "pending"
        assert stored["slack"]["verified_at"] is None
        # Another tenant's marker in the same (shared) bucket must never read as this workspace's.
        moment = 1_700_000_000.5
        marker = {"fingerprint": signing_secret_fingerprint("value"), "at": moment}
        await blob.put(url_verified_blob_key(uuid4()), json.dumps(marker).encode())
        foreign = (await client.get("/surface/setup/status")).json()
        assert foreign["slack"]["state"] == "pending"
        # A marker written by the currently-stored signing secret ("value") reads as connected.
        await blob.put(url_verified_blob_key(workspace_id), json.dumps(marker).encode())
        verified = (await client.get("/surface/setup/status")).json()
        assert verified["slack"]["state"] == "connected"
        assert verified["slack"]["verified_at"] == pytest.approx(moment)
        assert verified["events_url"] == f"{PUBLIC_BASE_URL}/surface/slack"
        # Rotating the signing secret orphans that marker's fingerprint → pending, not stale-green.
        await store.put(workspace_id, "slack_signing_secret", "rotated")
        rotated = (await client.get("/surface/setup/status")).json()
    assert rotated["slack"]["state"] == "pending"


async def test_test_endpoint_diagnoses_each_failure_precisely(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, store, blob = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        await _sign_in(client, workspace_id)
        missing = (await client.post("/surface/setup/slack/test")).json()
        assert missing["ok"] is False
        assert "slack_bot_token" in missing["diagnosis"]
        await store.put(workspace_id, "slack_bot_token", "xoxb-abc")
        await store.put(workspace_id, "slack_team_id", "T0OTHER")
        mismatch = (await client.post("/surface/setup/slack/test")).json()
        assert mismatch["ok"] is False
        assert TEAM_ID in mismatch["diagnosis"]
        assert "T0OTHER" in mismatch["diagnosis"]
        await store.put(workspace_id, "slack_team_id", TEAM_ID)
        pending = (await client.post("/surface/setup/slack/test")).json()
        assert pending["ok"] is True
        assert "first event" in pending["diagnosis"]
        # A marker from a since-rotated secret keeps Test at waiting — never a stale "Connected".
        await store.put(workspace_id, "slack_signing_secret", "current")
        stale = {"fingerprint": signing_secret_fingerprint("rotated-out"), "at": 1.0}
        await blob.put(url_verified_blob_key(workspace_id), json.dumps(stale).encode())
        rotated = (await client.post("/surface/setup/slack/test")).json()
        assert rotated["ok"] is True
        assert "first event" in rotated["diagnosis"]
        live = {"fingerprint": signing_secret_fingerprint("current"), "at": 2.0}
        await blob.put(url_verified_blob_key(workspace_id), json.dumps(live).encode())
        connected = (await client.post("/surface/setup/slack/test")).json()
    assert connected["ok"] is True
    assert "Connected" in connected["diagnosis"]


async def test_test_endpoint_maps_a_rejected_token(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed()
    client, store, _ = await _mount(
        monkeypatch,
        workspace_id,
        tmp_path,
        transport=_auth_test_transport([], ok=False, error="token_revoked"),
    )
    await store.put(workspace_id, "slack_bot_token", "xoxb-dead")
    async with client:
        await _sign_in(client, workspace_id)
        response = (await client.post("/surface/setup/slack/test")).json()
    assert response["ok"] is False
    assert "token_revoked" in response["diagnosis"]


async def test_a_non_owner_member_cannot_connect_slack(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed(extra_members=(MEMBER_EMAIL,))
    recorder: list[httpx.Request] = []
    client, _store, _ = await _mount(monkeypatch, workspace_id, tmp_path, recorder)
    async with client:
        await _sign_in(client, workspace_id, email=MEMBER_EMAIL)
        status = (await client.get("/surface/setup/status")).json()
        assert status["owner"] is False
        creds = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-abc123", "slack_signing_secret": "shhh"},
        )
        manifest = await client.get("/surface/setup/slack/manifest")
        test = await client.post("/surface/setup/slack/test")
    assert creds.status_code == 403
    assert manifest.status_code == 403
    assert test.status_code == 403
    assert recorder == []  # never reached auth.test
    async with workspace_tx() as connection:
        assert (await connection.execute(sa.select(tables.credential.c.slot))).all() == []


async def test_owner_status_reports_owner_true(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed(extra_members=(MEMBER_EMAIL,))
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        await _sign_in(client, workspace_id)
        status = (await client.get("/surface/setup/status")).json()
    assert status["owner"] is True


async def test_missing_token_secret_fails_loud(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed()
    client, _, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    token = _mint(SECRET, workspace_id, OWNER_EMAIL, _future())
    monkeypatch.delenv(setup.UFO_TOKEN_SECRET_ENV, raising=False)
    async with client:
        with pytest.raises(RuntimeError, match=setup.UFO_TOKEN_SECRET_ENV):
            await client.get(f"/surface/setup/status?token={token}")


async def test_empty_derived_ids_are_rejected_and_nothing_is_written(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _store, _ = await _mount(
        monkeypatch,
        workspace_id,
        tmp_path,
        transport=_auth_test_transport([], ok=True, team_id="", user_id=""),
    )
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-abc123", "slack_signing_secret": "shhh"},
        )
    assert response.status_code == 400
    assert response.json()["field"] == "slack_bot_token"
    async with workspace_tx() as connection:
        assert (await connection.execute(sa.select(tables.credential.c.slot))).all() == []


async def test_w_prefixed_bot_user_ids_are_accepted(db: None, tmp_path, monkeypatch) -> None:
    workspace_id = await _seed()
    client, store, _ = await _mount(
        monkeypatch,
        workspace_id,
        tmp_path,
        transport=_auth_test_transport([], user_id="W0CCCCCCC"),
    )
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={"slack_bot_token": "xoxb-abc123", "slack_signing_secret": "shhh"},
        )
    assert response.status_code == 200
    assert await store.get(workspace_id, "slack_bot_user_id") == "W0CCCCCCC"


async def test_an_explicit_id_disagreeing_with_auth_test_is_rejected(
    db: None, tmp_path, monkeypatch
) -> None:
    workspace_id = await _seed()
    client, _store, _ = await _mount(monkeypatch, workspace_id, tmp_path)
    async with client:
        await _sign_in(client, workspace_id)
        response = await client.post(
            "/surface/setup/slack/credentials",
            json={
                "slack_bot_token": "xoxb-abc123",
                "slack_signing_secret": "shhh",
                "slack_team_id": "T0TYPO0000",
            },
        )
    assert response.status_code == 400
    assert response.json()["field"] == "slack_team_id"
    assert TEAM_ID in response.json()["error"]
    async with workspace_tx() as connection:
        assert (await connection.execute(sa.select(tables.credential.c.slot))).all() == []


def test_setup_page_is_self_contained_and_mirrors_the_server_validation() -> None:
    page = setup.SETUP_PAGE
    assert page.startswith("<!doctype html>")
    assert "<script src=" not in page
    assert "<link " not in page
    assert "//cdn" not in page
    # The one external reference is a navigation anchor, never a fetched asset.
    assert page.count("https://") == page.count('href="https://api.slack.com/apps"')
    assert "const BASE = '/surface/setup'" in page
    for pattern in (setup.BOT_TOKEN_PATTERN, setup.TEAM_ID_PATTERN, setup.BOT_USER_ID_PATTERN):
        assert pattern in page
