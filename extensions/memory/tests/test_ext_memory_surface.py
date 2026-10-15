"""The memory explorer surface end to end through the shared-fleet mount: the operator `identify`
gate (the installed rule's grant, `?ws=` under each reach, forged/missing bearers), the shared
session-cookie bind, and the one read route — a plain enumeration of the workspace's memory_item
rows, shared and per-member, live and superseded, indexed and still due, newest first. The same
seam an operator hits; no runtime engine, no index backend — the explorer only reads the
extension's own table under the resolver's ambient workspace binding."""

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_memory import store as memory_store
from ufo_ext_memory import surface as memory_surface
from ufo_ext_memory.manifest import manifest as memory_manifest
from ufo_ext_memory.store import body_digest, memory_item
from ufo_ext_sample.manifest import manifest as sample_manifest
from ufo_ext_sample.operator import OPERATOR_DOMAIN, OPERATOR_RULE, OPERATOR_SIGN_IN
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore
from ufo.config import DEFAULT_OPERATOR_RULE, DebuggerConfig
from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.ext.operator import OperatorSetup, install_operator, select_operator_rule
from ufo.runtime.hub import InProcessHub
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.schema import tables
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

SECRET = "memory-token-secret"
ADMIN = "admin@acme.com"
MEMBER = "member@acme.com"
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


def _install(rule: str) -> None:
    install_operator(
        OperatorSetup(
            rule=select_operator_rule(rule, (memory_manifest(), sample_manifest())),
            links=DebuggerConfig(),
        )
    )


@pytest.fixture
def operator_rule() -> Iterator[None]:
    _install(DEFAULT_OPERATOR_RULE)
    yield
    install_operator(None)


@pytest.fixture
async def explorer(
    db: None, operator_rule: None, tmp_path, monkeypatch: pytest.MonkeyPatch
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
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://fleet") as client:
        yield client


async def _seed_workspace(admin: str = ADMIN) -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                [
                    {
                        "id": uuid4(),
                        "workspace_id": workspace_id,
                        "email": email,
                        "is_admin": email == admin,
                        "created_at": BASE_TIME + timedelta(minutes=seniority),
                        "updated_at": BASE_TIME + timedelta(minutes=seniority),
                    }
                    for seniority, email in enumerate((admin, MEMBER))
                ]
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
                body_digest=body_digest(body),
                item_class=item_class,
                memory_kind=memory_kind,
                confidence=confidence,
                source_ref=source_ref,
                created_from_page_uid=page_id,
                created_from_page_revision=page_revision,
                source_uid=source_id,
                embedding_digest=embedding_digest,
                embedding_claimed_at=embedding_claimed_at,
                superseded_by=superseded_by,
                created_at=when,
                updated_at=when,
            )
        )
    return memory_id


async def test_identify_admits_a_seated_admin_of_the_claimed_workspace_alone(explorer) -> None:
    workspace_id = await _seed_workspace()
    other_workspace = await _seed_workspace()
    admin = _mint(SECRET, workspace_id, ADMIN)
    member = _mint(SECRET, workspace_id, MEMBER)
    stranger = _mint(SECRET, workspace_id, "admin@elsewhere.com")
    forged = _mint("wrong-secret", workspace_id, ADMIN)

    granted = await explorer.get("/surface/memory/api/memories", headers=_auth(admin))
    assert granted.status_code == 200
    assert granted.json() == []
    for denied in (member, stranger, forged):
        assert (
            await explorer.get("/surface/memory/api/memories", headers=_auth(denied))
        ).status_code == 401
    assert (await explorer.get("/surface/memory/api/memories")).status_code == 401

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.workspace_id == other_workspace)
            .values(seated_at=None)
        )
    revoked = _mint(SECRET, other_workspace, ADMIN)
    assert (
        await explorer.get("/surface/memory/api/memories", headers=_auth(revoked))
    ).status_code == 401


async def test_an_api_read_without_a_granted_credential_names_the_rules_sign_in(
    explorer,
) -> None:
    unruled = await explorer.get("/surface/memory/api/memories")
    _install(OPERATOR_RULE)
    ruled = await explorer.get("/surface/memory/api/memories")

    assert (unruled.status_code, unruled.json()) == (401, {"sign_in": None})
    assert (ruled.status_code, ruled.json()) == (401, {"sign_in": OPERATOR_SIGN_IN})


async def test_query_token_is_never_accepted(explorer) -> None:
    workspace_id = await _seed_workspace()
    token = _mint(SECRET, workspace_id, ADMIN)
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
    token = _mint(SECRET, workspace_id, ADMIN)

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

    decision = by_body["the team ships on Fridays"]
    assert decision["superseded_by"] == str(replacement)
    assert decision["subject"] == SHARED_SUBJECT
    assert decision["half_life_days"] == 120.0
    assert 0.0 < decision["decay_factor"] < 0.5

    summary = by_body["consolidated summary"]
    assert summary["item_class"] == "semantic"
    assert summary["half_life_days"] == 365.0
    assert 0.0 < summary["decay_factor"] < 1.0

    due = by_body["still due for embedding"]
    assert due["embedding_digest"] is None
    assert due["embedding_claimed_at"] is None
    assert due["half_life_days"] == 365.0

    pref = by_body["I prefer concise summaries"]
    assert pref["subject"] == member_subject(member_id)
    assert pref["confidence"] == 8
    assert pref["memory_kind"] == "preference"
    assert pref["source_ref"] == "slack:C1/p1"
    assert pref["embedding_digest"] == "sha256:abc"
    assert pref["half_life_days"] == 180.0
    assert 0.0 < pref["decay_factor"] < 0.8


async def test_inventory_caps_at_the_newest_limit(explorer, monkeypatch) -> None:
    monkeypatch.setattr(memory_store, "MEMORY_INVENTORY_LIMIT", 2)
    workspace_id = await _seed_workspace()
    await _seed_memory(workspace_id, SHARED_SUBJECT, "oldest", minute=1)
    await _seed_memory(workspace_id, SHARED_SUBJECT, "middle", minute=2)
    await _seed_memory(workspace_id, SHARED_SUBJECT, "newest", minute=3)
    token = _mint(SECRET, workspace_id, ADMIN)

    listed = await explorer.get("/surface/memory/api/memories", headers=_auth(token))
    assert [row["body"] for row in listed.json()] == ["newest", "middle"]


async def test_an_own_session_reads_its_workspace_and_no_other(explorer) -> None:
    own_workspace = await _seed_workspace()
    target = await _seed_workspace()
    await _seed_memory(target, SHARED_SUBJECT, "target-only memory", minute=5)
    token = _mint(SECRET, own_workspace, ADMIN)

    for named in (str(target), "acme.com"):
        refused = await explorer.get(
            "/surface/memory/api/memories", params={"ws": named}, headers=_auth(token)
        )
        assert refused.status_code == 403
    own = await explorer.get(
        "/surface/memory/api/memories", params={"ws": str(own_workspace)}, headers=_auth(token)
    )
    assert own.status_code == 200
    assert own.json() == []


async def test_a_fleet_grant_rescopes_to_another_workspace(explorer) -> None:
    _install(OPERATOR_RULE)
    operator_admin = f"admin@{OPERATOR_DOMAIN}"
    operator_workspace = await _seed_workspace(operator_admin)
    target = await _seed_workspace()
    await _seed_memory(target, SHARED_SUBJECT, "target-only memory", minute=5)
    token = _mint(SECRET, operator_workspace, operator_admin)

    own = await explorer.get("/surface/memory/api/memories", headers=_auth(token))
    assert own.json() == []
    scoped = await explorer.get(
        "/surface/memory/api/memories", params={"ws": str(target)}, headers=_auth(token)
    )
    assert [row["body"] for row in scoped.json()] == ["target-only memory"]
    bounced = await explorer.get("/surface/memory", follow_redirects=False)
    assert bounced.status_code == 303
    assert bounced.headers["location"] == OPERATOR_SIGN_IN


async def test_page_serves_and_fails_loud_when_missing(explorer, monkeypatch) -> None:
    workspace_id = await _seed_workspace()
    token = _mint(SECRET, workspace_id, ADMIN)
    page = await explorer.get("/surface/memory", headers=_auth(token))
    assert page.status_code == 200
    assert "Memory explorer" in page.text
    monkeypatch.setattr(memory_surface, "APP_HTML", None)
    with pytest.raises(RuntimeError, match=r"static/memory\.html"):
        await explorer.get("/surface/memory", headers=_auth(token))
