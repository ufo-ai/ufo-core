"""The memory explorer surface end to end through the shared-fleet mount: the operator `identify`
gate (domain, `?ws=` re-scoping, forged/missing bearers), the shared session-cookie bind, and the
one read route — a plain enumeration of the workspace's memory_item rows, shared and per-member,
live and superseded, indexed and still due, newest first. The same seam an operator hits; no runtime
engine, no index backend — the explorer only reads the extension's own table under the resolver's
ambient workspace binding."""

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_memory import store as memory_store
from ufo_ext_memory import surface as memory_surface
from ufo_ext_memory.manifest import manifest as memory_manifest
from ufo_ext_memory.store import memory_item
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.hub import InProcessHub
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.sdk.surfaces import OPERATOR_EMAIL_DOMAIN
from ufo.serve import _mount_shared_surfaces
from ufo.subjects import SHARED_SUBJECT, member_subject

SECRET = "memory-token-secret"
BASE_TIME = datetime(2026, 7, 1, tzinfo=UTC)


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the memory explorer admits nothing")


def _mint(secret: str, workspace_id: UUID, email: str) -> str:
    exp = int(datetime.now(tz=UTC).timestamp()) + 3600
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"ws": str(workspace_id), "email": email, "exp": exp}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


@pytest.fixture
async def explorer(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (memory_manifest(),),
        None,
        blob,
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        _StubDbos(),
        "",
        None,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://fleet") as client:
        yield client


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_memory(
    workspace_id: UUID,
    subject: str,
    body: str,
    *,
    minute: int,
    item_class: str = "fact",
    memory_kind: str = "fact",
    confidence: int = 5,
    source_ref: str | None = None,
    page_origin: tuple[UUID, int, UUID] | None = None,
    embedding_digest: str | None = None,
    embedding_claimed_at: datetime | None = None,
    superseded_by: UUID | None = None,
) -> UUID:
    memory_id = uuid4()
    when = BASE_TIME + timedelta(minutes=minute)
    page_id, page_revision, source_id = page_origin or (None, None, None)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=memory_id,
                workspace_id=workspace_id,
                subject=subject,
                body=body,
                item_class=item_class,
                memory_kind=memory_kind,
                confidence=confidence,
                source_ref=source_ref,
                created_from_page_id=page_id,
                created_from_page_revision=page_revision,
                source_id=source_id,
                embedding_digest=embedding_digest,
                embedding_claimed_at=embedding_claimed_at,
                superseded_by=superseded_by,
                created_at=when,
                updated_at=when,
            )
        )
    return memory_id


async def test_identify_gates_on_the_operator_domain(explorer) -> None:
    workspace_id = await _seed_workspace()
    operator = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    member = _mint(SECRET, workspace_id, "member@acme.com")
    forged = _mint("wrong-secret", workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    granted = await explorer.get("/surface/memory/api/memories", headers=_auth(operator))
    assert granted.status_code == 200
    assert granted.json() == []
    assert (
        await explorer.get("/surface/memory/api/memories", headers=_auth(member))
    ).status_code == 401
    assert (
        await explorer.get("/surface/memory/api/memories", headers=_auth(forged))
    ).status_code == 401
    assert (await explorer.get("/surface/memory/api/memories")).status_code == 401


async def test_query_token_is_never_accepted(explorer) -> None:
    workspace_id = await _seed_workspace()
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    read = await explorer.get("/surface/memory/api/memories", params={"token": token})
    assert read.status_code == 401


async def test_memories_list_shared_and_member_newest_first_with_state(explorer) -> None:
    workspace_id = await _seed_workspace()
    member_id = uuid4()
    replacement = await _seed_memory(
        workspace_id, SHARED_SUBJECT, "consolidated summary", minute=40, item_class="semantic"
    )
    await _seed_memory(
        workspace_id,
        SHARED_SUBJECT,
        "the team ships on Fridays",
        minute=10,
        memory_kind="decision",
        superseded_by=replacement,
    )
    await _seed_memory(
        workspace_id,
        member_subject(member_id),
        "I prefer concise summaries",
        minute=30,
        memory_kind="preference",
        confidence=8,
        source_ref="slack:C1/p1",
        embedding_digest="sha256:abc",
    )
    await _seed_memory(
        workspace_id, member_subject(member_id), "still due for embedding", minute=20
    )
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    listed = await explorer.get("/surface/memory/api/memories", headers=_auth(token))
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["body"] for row in rows] == [
        "consolidated summary",
        "I prefer concise summaries",
        "still due for embedding",
        "the team ships on Fridays",
    ]
    by_body = {row["body"]: row for row in rows}
    assert all(row["age_days"] > 0 for row in rows)

    # a superseded decision fact: decays on the 120d half-life, capped by confidence/10 = 0.5
    decision = by_body["the team ships on Fridays"]
    assert decision["superseded_by"] == str(replacement)
    assert decision["subject"] == SHARED_SUBJECT
    assert decision["half_life_days"] == 120.0
    assert 0.0 < decision["decay_factor"] < 0.5

    # a semantic summary never decays — no half-life, full weight
    summary = by_body["consolidated summary"]
    assert summary["item_class"] == "semantic"
    assert summary["half_life_days"] is None
    assert summary["decay_factor"] == 1.0

    # a due fact carries no embedding digest and no indexer lease
    due = by_body["still due for embedding"]
    assert due["embedding_digest"] is None
    assert due["embedding_claimed_at"] is None
    assert due["half_life_days"] == 365.0

    # a member preference fact: 180d half-life, capped by confidence/10 = 0.8, already indexed
    pref = by_body["I prefer concise summaries"]
    assert pref["subject"] == member_subject(member_id)
    assert pref["confidence"] == 8
    assert pref["memory_kind"] == "preference"
    assert pref["source_ref"] == "slack:C1/p1"
    assert pref["embedding_digest"] == "sha256:abc"
    assert pref["half_life_days"] == 180.0
    assert 0.0 < pref["decay_factor"] < 0.8


async def test_page_derived_memory_carries_its_whole_origin(explorer) -> None:
    """A derived row's origin is the page, its revision, and the source that synced it — an
    operator reading two identical claims apart needs all three, so every one reaches the JSON."""
    workspace_id = await _seed_workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_memory(
        workspace_id,
        SHARED_SUBJECT,
        "the renewal closes September 30",
        minute=7,
        page_origin=(page_id, 4, source_id),
    )
    await _seed_memory(workspace_id, SHARED_SUBJECT, "a member typed this one", minute=8)
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    rows = {
        row["body"]: row
        for row in (await explorer.get("/surface/memory/api/memories", headers=_auth(token))).json()
    }
    derived = rows["the renewal closes September 30"]
    assert derived["created_from_page_id"] == str(page_id)
    assert derived["created_from_page_revision"] == 4
    assert derived["source_id"] == str(source_id)
    typed = rows["a member typed this one"]
    assert typed["created_from_page_id"] is None
    assert typed["source_id"] is None


async def test_indexing_lease_state_round_trips(explorer) -> None:
    """A row the indexer has claimed but not yet embedded — a digest still NULL with
    `embedding_claimed_at` set — is the 'indexing' state the explorer renders distinctly from
    'due'; it must survive the read to the JSON."""
    workspace_id = await _seed_workspace()
    await _seed_memory(
        workspace_id,
        SHARED_SUBJECT,
        "mid-index",
        minute=5,
        embedding_claimed_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    (row,) = (await explorer.get("/surface/memory/api/memories", headers=_auth(token))).json()
    assert row["embedding_digest"] is None
    assert row["embedding_claimed_at"] is not None


async def test_inventory_caps_at_the_newest_limit(explorer, monkeypatch) -> None:
    monkeypatch.setattr(memory_store, "MEMORY_INVENTORY_LIMIT", 2)
    workspace_id = await _seed_workspace()
    await _seed_memory(workspace_id, SHARED_SUBJECT, "oldest", minute=1)
    await _seed_memory(workspace_id, SHARED_SUBJECT, "middle", minute=2)
    await _seed_memory(workspace_id, SHARED_SUBJECT, "newest", minute=3)
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    listed = await explorer.get("/surface/memory/api/memories", headers=_auth(token))
    assert [row["body"] for row in listed.json()] == ["newest", "middle"]


async def test_ws_param_rescopes_to_another_workspace(explorer) -> None:
    operator_workspace = await _seed_workspace()
    target = await _seed_workspace()
    await _seed_memory(target, SHARED_SUBJECT, "target-only memory", minute=5)
    token = _mint(SECRET, operator_workspace, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    own = await explorer.get("/surface/memory/api/memories", headers=_auth(token))
    assert own.json() == []
    scoped = await explorer.get(
        "/surface/memory/api/memories", params={"ws": str(target)}, headers=_auth(token)
    )
    assert [row["body"] for row in scoped.json()] == ["target-only memory"]


async def test_posted_token_binds_the_shared_cookie_and_reads(explorer) -> None:
    workspace_id = await _seed_workspace()
    await _seed_memory(workspace_id, SHARED_SUBJECT, "cookie-bound read", minute=1)
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    bind = await explorer.post("/surface/memory", data={"token": token}, follow_redirects=False)
    assert bind.status_code == 303
    assert "token=" not in bind.headers["location"]
    cookie = bind.headers["set-cookie"]
    assert cookie.startswith("ufo_debug=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "Domain" not in cookie
    assert "SameSite=lax" in cookie
    listed = await explorer.get("/surface/memory/api/memories")
    assert [row["body"] for row in listed.json()] == ["cookie-bound read"]


async def test_page_serves_and_fails_loud_when_missing(explorer, monkeypatch) -> None:
    workspace_id = await _seed_workspace()
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    page = await explorer.get("/surface/memory", headers=_auth(token))
    assert page.status_code == 200
    assert "memory explorer" in page.text
    monkeypatch.setattr(memory_surface, "APP_HTML", None)
    with pytest.raises(RuntimeError, match=r"static/memory\.html"):
        await explorer.get("/surface/memory", headers=_auth(token))
