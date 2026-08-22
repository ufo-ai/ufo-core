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
from ufo.hub import InProcessHub
from ufo.models.interface import (
    Message,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import SUBAGENT_SURFACE, TerminalFrame
from ufo.sdk.surfaces import OPERATOR_EMAIL_DOMAIN
from ufo.serve import _mount_shared_surfaces
from ufo.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
    encode,
    transcript_key,
)
from ufo.workspace import ws

SECRET = "debug-token-secret"


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the debug surface admits nothing")


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


async def test_a_domain_nobody_is_seated_at_scopes_to_the_id_it_provisions(debug) -> None:
    """A workspace provisioned for a domain is created under `uuid5(NAMESPACE_DNS, domain)` and
    carries no member until someone onboards. The domain must still reach it in that window, so a
    domain no seating claims falls back to the id its provisioning uses."""
    client, _, _ = debug
    operator_workspace, _ = await _seed_workspace()
    token = _mint(SECRET, operator_workspace, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    scope = await client.get(
        "/surface/debug/api/workspace", params={"ws": "nobody.example"}, headers=_auth(token)
    )
    assert scope.status_code == 200
    assert scope.json()["workspace_id"] == str(uuid5(NAMESPACE_DNS, "nobody.example"))


async def test_posted_token_binds_the_cookie_and_redirects(debug) -> None:
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    response = await client.post(
        "/surface/debug",
        params={"ws": "acme.com"},
        data={"token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "token=" not in response.headers["location"]
    assert "ws=acme.com" in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("ufo_debug=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Domain" not in cookie
    listed = await client.get("/surface/debug/api/conversations")
    assert listed.status_code == 200


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


async def test_an_unresolved_page_get_lands_on_the_sign_in_page(debug) -> None:
    """The Slack turn's `debug` link is a plain GET carrying no bearer: an operator whose cookie
    outlived its bearer reaches the card that mints a new one instead of a bare `unauthorized`,
    which is the only recoverable answer for a cookie they cannot read or delete. The bounce carries
    `?debug=1`, the ask that sends the minted bearer back to this surface: without it the sign-in
    page posts the session to the member portal, so the click that wanted the debugger would land
    there and never come back. The API routes still reject, so the page's fetches fail loudly."""
    client, _, _ = debug
    workspace_id, _ = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace_id)
    operator = f"alex@{OPERATOR_EMAIL_DOMAIN}"

    linked = await client.get(
        "/surface/debug",
        params={"ws": str(workspace_id), "c": str(conversation_id)},
        follow_redirects=False,
    )
    assert linked.status_code == 303
    assert linked.headers["location"] == "/login?debug=1"

    client.cookies.set("ufo_debug", _mint(SECRET, workspace_id, operator, ttl_seconds=-1))
    expired = await client.get("/surface/debug", follow_redirects=False)
    assert expired.status_code == 303
    assert expired.headers["location"] == "/login?debug=1"
    assert (await client.get("/surface/debug/api/conversations")).status_code == 401


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


async def test_workspace_meta_carries_the_slack_team(debug) -> None:
    client, _, _ = debug
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
    assert meta.json() == {"workspace_id": str(workspace_id), "slack_team": "T042"}


async def test_turns_and_detail_read_terminal_ledger_and_children(debug) -> None:
    client, _, _ = debug
    workspace_id, agent_id = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace_id)
    parent = await _seed_turn(workspace_id, conversation_id, agent_id, 1)
    child_conversation = await _seed_conversation(workspace_id, queue_key="subagent:1")
    child = await _seed_turn(workspace_id, child_conversation, agent_id, 1, parent_turn_id=parent)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=parent,
                dimension="tokens",
                amount=1234,
                prompt_tokens=1234,
                input_tokens=1234,
                priced_micro_usd=77,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    turns = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/turns", headers=_auth(token)
    )
    assert turns.status_code == 200
    (listed,) = turns.json()
    assert listed["id"] == str(parent)
    assert listed["terminal"]["cost_micro_usd"] == 77

    detail = await client.get(f"/surface/debug/api/turns/{parent}", headers=_auth(token))
    assert detail.status_code == 200
    body = detail.json()
    assert body["turn"]["id"] == str(parent)
    assert [entry["dimension"] for entry in body["ledger"]] == ["tokens"]
    assert [turn["id"] for turn in body["children"]] == [str(child)]
    assert body["children"][0]["subagent_profile"] == "research"
    missing = await client.get(f"/surface/debug/api/turns/{uuid4()}", headers=_auth(token))
    assert missing.status_code == 404
    malformed = await client.get("/surface/debug/api/turns/not-a-uuid", headers=_auth(token))
    assert malformed.status_code == 404


async def test_transcript_compactions_and_files_read_the_blobs_and_the_sandbox(debug) -> None:
    client, blob, sandboxes = debug
    workspace_id, _ = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace_id)
    stored = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content=(
                    RedactedThinkingBlock(data="ZW5jcnlwdGVk"),
                    ThinkingBlock(thinking="check the tree", signature="sig-1"),
                    ReasoningItemBlock(
                        id="rs_1",
                        encrypted_content="Z3B0LWVuY3J5cHRlZA",
                        summary=("check the tree",),
                    ),
                    ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
                    ToolResultBlock(tool_use_id="t1", content="README.md"),
                    TextBlock(text="done"),
                ),
            ),
        ),
        system="you are the agent\n\n<recalled_memory>fact</recalled_memory>",
        injected="<recalled_memory>fact</recalled_memory>",
    )
    await blob.put(transcript_key(conversation_id), encode(stored))
    summary = CompactionSummary(intent="ship", current_work="reading", next_step="write")
    window = CompactionWindow(messages=(Message(role="user", content="hi"),))
    for half, payload in (("before", window), ("after", window), ("summary", summary)):
        await blob.put(
            compaction_key(conversation_id, 1, half),
            lz4.frame.compress(payload.model_dump_json().encode()),
        )
    with ws(workspace_id):
        await sandboxes.write(conversation_id, "report/out.txt", b"hello world")
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")

    transcript = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/transcript", headers=_auth(token)
    )
    assert transcript.status_code == 200
    assert transcript.json()["system"] == stored.system
    assert transcript.json()["injected"] == stored.injected
    blocks = transcript.json()["messages"][1]["content"]
    assert [block["type"] for block in blocks] == [
        "redacted_thinking",
        "thinking",
        "reasoning",
        "tool_use",
        "tool_result",
        "text",
    ]
    assert blocks[1]["signature"] == "sig-1"
    assert blocks[2]["summary"] == ["check the tree"]

    compactions = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/compactions", headers=_auth(token)
    )
    assert compactions.json() == [1]
    record = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/compactions/1", headers=_auth(token)
    )
    assert record.status_code == 200
    assert record.json()["summary"]["intent"] == "ship"

    files = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/files", headers=_auth(token)
    )
    assert [entry["path"] for entry in files.json()] == ["report/out.txt"]
    download = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/files/report/out.txt",
        headers=_auth(token),
    )
    assert download.status_code == 200
    assert download.content == b"hello world"

    empty = await _seed_conversation(workspace_id, queue_key="empty")
    assert (
        await client.get(
            f"/surface/debug/api/conversations/{empty}/transcript", headers=_auth(token)
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/surface/debug/api/conversations/{conversation_id}/files/report/absent.txt",
            headers=_auth(token),
        )
    ).status_code == 404
    escape = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/files/..%2Fmessages.json.lz4",
        headers=_auth(token),
    )
    assert escape.status_code == 404


async def test_stream_tails_a_completed_turn_from_its_durable_terminal(debug) -> None:
    client, _, _ = debug
    workspace_id, agent_id = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace_id)
    turn_id = await _seed_turn(workspace_id, conversation_id, agent_id, 1)
    token = _mint(SECRET, workspace_id, f"alex@{OPERATOR_EMAIL_DOMAIN}")
    response = await client.get(f"/surface/debug/api/turns/{turn_id}/stream", headers=_auth(token))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: terminal" in response.text
    assert '"cost_micro_usd":77' in response.text
    missing = await client.get(f"/surface/debug/api/turns/{uuid4()}/stream", headers=_auth(token))
    assert missing.status_code == 404
