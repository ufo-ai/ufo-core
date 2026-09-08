"""The debug surface end to end through the shared-fleet mount: the `identify` gate (operator
domain, `?ws=` re-scoping, forged/missing bearers), the cookie bind, the fleet index that spans
every workspace, and every read route against real rows, blobs, and a real conversation sandbox —
the same seam the operator hits, no runtime engine needed because the surface admits nothing and a
completed turn tails from its durable terminal row. The files tab reads the live sandbox workspace
through the carrier, so its proof seeds files by writing through the same `ConversationSandbox`."""

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import lz4.frame
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_debugger import surface as debugger_surface
from ufo_ext_debugger.manifest import manifest as debugger_manifest
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import (
    ToolUseBlock,
)
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.engine import DispatchResult, StreamResult
from ufo.runtime.hub import InProcessHub
from ufo.runtime.turns.transcript import (
    transcript_key,
)
from ufo.schema import tables
from ufo.schema.records import SUBAGENT_SURFACE, TerminalFrame, Usage
from ufo.sdk.surfaces import OPERATOR_EMAIL_DOMAIN
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

SECRET = "debug-token-secret"


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the debug surface admits nothing")

    async def list_workflow_steps_async(self, workflow_id: str) -> list[dict[str, object]]:
        return [
            {
                "function_name": "TurnEngine._stream_once",
                "started_at_epoch_ms": 1_777_215_600_123,
                "completed_at_epoch_ms": 1_777_215_606_577,
                "output": StreamResult(
                    tool_calls=(ToolUseBlock(id="call-1", name="bash", input={"command": "pwd"}),),
                    usages=(Usage(input_tokens=120, output_tokens=340),),
                ),
            },
            {
                "function_name": "TurnEngine._dispatch_step",
                "started_at_epoch_ms": 1_777_215_606_600,
                "completed_at_epoch_ms": 1_777_215_726_705,
                "output": DispatchResult(tool_use_id="call-1", text="/workspace", is_error=False),
            },
        ]


def _mint(secret: str, workspace_id: UUID, email: str, ttl_seconds: int = 3600) -> str:
    exp = int(datetime.now(tz=UTC).timestamp()) + ttl_seconds
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"ws": str(workspace_id), "email": email, "exp": exp}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


@pytest.fixture
async def debug(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, FilesystemBlobStore, ConversationSandbox]]:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=tmp_path / "workspaces",
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (debugger_manifest(),),
        None,
        blob,
        sandboxes,
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
        yield client, blob, sandboxes


async def _seed_workspace(domain: str | None = None, members: int = 1) -> tuple[UUID, UUID]:
    """A workspace as sign-up mints one: an id of its own, addressed by the domain its members are
    seated at rather than by any derivation of it, or an anonymous row with nobody in it."""
    workspace_id = uuid4()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        if domain is not None:
            await connection.execute(
                sa.insert(tables.member).values(
                    [
                        {
                            "id": uuid4(),
                            "workspace_id": workspace_id,
                            "email": f"person{index}@{domain}",
                            "created_at": sa.func.now(),
                            "updated_at": sa.func.now(),
                        }
                        for index in range(members)
                    ]
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
    return workspace_id, agent_id


async def _seed_conversation(
    workspace_id: UUID,
    *,
    queue_key: str = "C042:1721.5",
    surface: str = "slack",
    title: str | None = None,
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=sa.select(tables.agent.c.id)
                .where(tables.agent.c.workspace_id == workspace_id)
                .order_by(tables.agent.c.created_at, tables.agent.c.id)
                .limit(1)
                .scalar_subquery(),
                surface=surface,
                queue_key=queue_key,
                title=title,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _seed_turn(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    seq: int,
    *,
    status: str = "done",
    parent_turn_id: UUID | None = None,
    at: datetime | None = None,
) -> UUID:
    turn_id = uuid4()
    terminal = (
        TerminalFrame(
            status=status, text="answer", tokens=9, cost_micro_usd=77, model="claude-opus-4-8"
        ).model_dump(mode="json")
        if status in ("done", "failed", "cancelled")
        else None
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound=f"ask {seq}",
                parent_turn_id=parent_turn_id,
                subagent_profile="research" if parent_turn_id is not None else None,
                terminal=terminal,
                created_at=at or sa.func.now(),
                updated_at=at or sa.func.now(),
            )
        )
    return turn_id


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_identify_gates_on_the_operator_domain(debug) -> None:
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    operator = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    member = _mint(SECRET, workspace_id, "member@acme.com")
    forged = _mint("wrong-secret", workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    granted = await client.get("/surface/debug/api/workspace", headers=_auth(operator))
    assert granted.status_code == 200
    assert granted.json()["workspace_id"] == str(workspace_id)
    denied_member = await client.get("/surface/debug/api/workspace", headers=_auth(member))
    denied_forged = await client.get("/surface/debug/api/workspace", headers=_auth(forged))
    assert denied_member.status_code == 401
    assert denied_forged.status_code == 401
    assert (await client.get("/surface/debug/api/workspace")).status_code == 401


async def test_fleet_indexes_every_workspace_and_the_newest_threads_across_them(debug) -> None:
    """The landing index: every workspace the deploy serves, newest activity first and an idle one
    last, each named by the domain that addresses it — and the recent threads across all of them,
    each carrying the workspace a click must re-scope to."""
    client, _, _ = debug
    acme, acme_agent = await _seed_workspace("acme.com")
    beta, beta_agent = await _seed_workspace("beta.io", members=2)
    idle, _ = await _seed_workspace()
    older, older_json = datetime(2026, 8, 20, 9, 30, tzinfo=UTC), "2026-08-20T09:30:00Z"
    newer, newer_json = datetime(2026, 8, 21, 17, 5, tzinfo=UTC), "2026-08-21T17:05:00Z"
    acme_conversation = await _seed_conversation(acme, queue_key="C001:1.0", title="quarter close")
    await _seed_turn(acme, acme_conversation, acme_agent, 1, at=older)
    await _seed_turn(acme, acme_conversation, acme_agent, 2, at=older)
    beta_conversation = await _seed_conversation(beta, queue_key="C002:2.0", surface="web")
    await _seed_turn(beta, beta_conversation, beta_agent, 1, at=newer)
    token = _mint(SECRET, acme, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    listing = await client.get("/surface/debug/api/fleet", headers=_auth(token))
    assert listing.status_code == 200
    body = listing.json()

    assert [entry["workspace_id"] for entry in body["workspaces"]] == [
        str(beta),
        str(acme),
        str(idle),
    ]
    assert body["workspaces"][0] == {
        "workspace_id": str(beta),
        "domain": "beta.io",
        "members": 2,
        "conversations": 1,
        "last_turn_at": newer_json,
    }
    assert body["workspaces"][2] == {
        "workspace_id": str(idle),
        "domain": None,
        "members": 0,
        "conversations": 0,
        "last_turn_at": None,
    }
    assert body["threads"] == [
        {
            "workspace_id": str(beta),
            "domain": "beta.io",
            "conversation_id": str(beta_conversation),
            "surface": "web",
            "queue_key": "C002:2.0",
            "title": None,
            "turn_count": 1,
            "last_turn_at": newer_json,
        },
        {
            "workspace_id": str(acme),
            "domain": "acme.com",
            "conversation_id": str(acme_conversation),
            "surface": "slack",
            "queue_key": "C001:1.0",
            "title": "quarter close",
            "turn_count": 2,
            "last_turn_at": older_json,
        },
    ]


async def test_fleet_indexes_rooted_turns_only(debug) -> None:
    """A subagent runs in a conversation of its own, so a busy workspace's fan-out would otherwise
    fill the index and hide every other workspace's threads behind it. The directory orders, counts
    and lists on rooted turns alone — the fan-out is reachable through the parent turn, never as a
    thread of its own."""
    client, _, _ = debug
    acme, agent = await _seed_workspace("acme.com")
    rooted_at = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
    fanned_at = datetime(2026, 8, 21, 17, 5, tzinfo=UTC)
    conversation = await _seed_conversation(acme, queue_key="C001:1.0")
    parent = await _seed_turn(acme, conversation, agent, 1, at=rooted_at)
    fanned = await _seed_conversation(acme, queue_key=str(parent), surface=SUBAGENT_SURFACE)
    await _seed_turn(acme, fanned, agent, 1, at=fanned_at, parent_turn_id=parent)
    token = _mint(SECRET, acme, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    body = (await client.get("/surface/debug/api/fleet", headers=_auth(token))).json()

    assert [thread["conversation_id"] for thread in body["threads"]] == [str(conversation)]
    assert body["workspaces"] == [
        {
            "workspace_id": str(acme),
            "domain": "acme.com",
            "members": 1,
            "conversations": 1,
            "last_turn_at": "2026-08-20T09:30:00Z",
        }
    ]


async def test_fleet_is_closed_to_everyone_but_an_operator(debug) -> None:
    """The index reads across every workspace, so it answers only what `?ws=` already would: a
    verified bearer whose email domain is the operator's, and nothing else."""
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace("acme.com")
    member = _mint(SECRET, workspace_id, "member@acme.com")
    forged = _mint("wrong-secret", workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    assert (await client.get("/surface/debug/api/fleet", headers=_auth(member))).status_code == 401
    assert (await client.get("/surface/debug/api/fleet", headers=_auth(forged))).status_code == 401
    assert (await client.get("/surface/debug/api/fleet")).status_code == 401


async def test_ws_param_rescopes_to_any_workspace_by_uuid_or_domain(debug) -> None:
    """A domain addresses the workspace its members are seated at — the one the fleet index prints
    it beside — and not a second workspace id derived from the same text. A workspace holds an id
    of its own, so the two are the same only by accident, and an operator who clicks a row or types
    a domain must reach the rows they were looking at."""
    client, _, _ = debug
    operator_workspace, _ = await _seed_workspace()
    target_workspace, target_agent = await _seed_workspace("acme.com")
    conversation_id = await _seed_conversation(target_workspace)
    await _seed_turn(target_workspace, conversation_id, target_agent, 1)
    token = _mint(SECRET, operator_workspace, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    by_uuid = await client.get(
        "/surface/debug/api/conversations",
        params={"ws": str(target_workspace)},
        headers=_auth(token),
    )
    assert by_uuid.status_code == 200
    assert [entry["id"] for entry in by_uuid.json()] == [str(conversation_id)]

    by_domain = await client.get(
        "/surface/debug/api/conversations", params={"ws": "acme.com"}, headers=_auth(token)
    )
    assert by_domain.status_code == 200
    assert [entry["id"] for entry in by_domain.json()] == [str(conversation_id)]

    scope = await client.get(
        "/surface/debug/api/workspace", params={"ws": "acme.com"}, headers=_auth(token)
    )
    assert scope.json()["workspace_id"] == str(target_workspace)
    assert scope.json()["workspace_id"] != str(uuid5(NAMESPACE_DNS, "acme.com"))


async def test_a_posted_token_recovers_a_session_behind_a_stale_cookie(debug) -> None:
    """A held `ufo_debug` cookie whose bearer no longer resolves — signed under a secret this
    deploy does not hold, or expired — must not shadow the live bearer the sign-in card posts: the
    cookie is httponly, so an operator can neither read nor delete it, and signing in again is the
    only way back."""
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    operator = f"alex@{OPERATOR_EMAIL_DOMAIN}"
    fresh = _mint(SECRET, workspace_id, operator)
    forged = _mint("a-secret-this-deploy-does-not-hold", workspace_id, operator)
    expired = _mint(SECRET, workspace_id, operator, ttl_seconds=-1)

    for stale in (forged, expired):
        client.cookies.set("ufo_debug", stale)
        opened = await client.post("/surface/debug", data={"token": fresh}, follow_redirects=False)
        assert opened.status_code == 303
        assert opened.headers["set-cookie"].startswith(f"ufo_debug={fresh}")
        assert (await client.get("/surface/debug/api/workspace")).status_code == 200


async def test_query_tokens_are_never_accepted(debug) -> None:
    """The bearer never rides a URL: a `?token=` query neither authenticates a read nor binds a
    session, so access logs and histories cannot capture a working credential."""
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    read = await client.get("/surface/debug/api/workspace", params={"token": token})
    assert read.status_code == 401
    bind = await client.get("/surface/debug", params={"token": token}, follow_redirects=False)
    assert bind.status_code == 303
    assert bind.headers["location"] == "/login?debug=1"
    assert "set-cookie" not in bind.headers
    tokenless_post = await client.post("/surface/debug", follow_redirects=False)
    assert tokenless_post.status_code == 401


async def test_app_page_serves_the_built_app_and_fails_loud_unbuilt(debug, monkeypatch) -> None:
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    monkeypatch.setattr(debugger_surface, "APP_HTML", "<!doctype html><title>debug</title>")
    page = await client.get("/surface/debug", headers=_auth(token))
    assert page.status_code == 200
    assert page.text.startswith("<!doctype html>")
    monkeypatch.setattr(debugger_surface, "APP_HTML", None)
    with pytest.raises(RuntimeError, match="not built"):
        await client.get("/surface/debug", headers=_auth(token))


async def test_workspace_meta_carries_the_slack_team(debug, monkeypatch) -> None:
    client, _, _ = debug
    monkeypatch.setenv("DD_SITE", "us5.datadoghq.com")
    workspace_id, agent_id = await _seed_workspace()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                routes_ingress=True,
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T042",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    meta = await client.get("/surface/debug/api/workspace", headers=_auth(token))
    assert meta.json() == {
        "workspace_id": str(workspace_id),
        "slack_team": "T042",
        "datadog_site": "us5.datadoghq.com",
    }


async def test_transcript_shows_a_stored_tool_description_after_its_arg_is_removed(debug) -> None:
    client, blob, _sandboxes = debug
    workspace_id, _ = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace_id)
    await blob.put(
        transcript_key(conversation_id),
        lz4.frame.compress(
            b'{"seq":1,"messages":['
            b'{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"bash",'
            b'"input":{"command":"ls","user_description":"Listing files"}}]},'
            b'{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1",'
            b'"content":"README.md"}]}]}'
        ),
    )
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    response = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/transcript", headers=_auth(token)
    )

    assert response.status_code == 200
    call = response.json()["messages"][0]["content"][0]
    assert call["input"] == {"command": "ls", "user_description": "Listing files"}
