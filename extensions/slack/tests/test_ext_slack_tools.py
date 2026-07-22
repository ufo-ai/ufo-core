"""The slack setup tools over real dispatch: `slack_connect` walks the whole state machine off
the real credential store, identity record, and marker blob — deriving the identity over a mocked
`auth.test`, owner-gated — and the manifest tool renders the exact YAML the skill shows."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_slack.surface as slack
import yaml
from cryptography.fernet import Fernet
from ufo_ext_slack import tools
from ufo_ext_slack.manifest import manifest as slack_manifest
from ufo_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    _reply_text,
    _reply_with_oversize_links,
    bot_token_fingerprint,
    read_identity,
    signing_secret_fingerprint,
    url_verified_blob_key,
)

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext
from ufo.ext.loader import skill_registry, turn_tools
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.surfaces import (
    CredentialPrompt,
    CredentialRequest,
    SurfaceContext,
    Writeback,
)
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import init_workspace_credentials, ws

OWNER_EMAIL = "owner@acme.com"
JOINER_EMAIL = "late@acme.com"
PUBLIC_BASE_URL = "https://tenant.example.com"
TEAM_ID = "T012345"
BOT_USER_ID = "U098765"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("a setup tool must not touch the sandbox")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        raise AssertionError("a setup tool must not touch the sandbox")

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError("a setup tool must not touch the sandbox")


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("a setup tool must not spawn a subagent")


async def _seed() -> tuple[UUID, UUID, UUID]:
    """A workspace whose owner (the earliest member) is OWNER_EMAIL plus one later joiner, so the
    connect tool's owner gate has both sides to check."""
    workspace_id, owner_id, joiner_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member_id, email, created in (
            (owner_id, OWNER_EMAIL, datetime(2026, 1, 1, tzinfo=UTC)),
            (joiner_id, JOINER_EMAIL, datetime(2026, 6, 1, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    created_at=created,
                    updated_at=created,
                )
            )
    return workspace_id, owner_id, joiner_id


def _registry(
    store: CredentialStore,
) -> tuple[dict[str, object], dict[str, ExtensionContext]]:
    declared_tools, ext_by_tool = turn_tools((slack_manifest(),), store)
    return {tool.name: tool for tool in declared_tools}, ext_by_tool


def _context(
    workspace_id: UUID,
    ext: ExtensionContext,
    blob: FilesystemBlobStore,
    member_id: UUID | None,
    public_base_url: str | None = PUBLIC_BASE_URL,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="connect slack",
            created_at=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience_member_id=member_id,
        artifact_token_secret="",
        ext=ext,
        public_base_url=public_base_url,
    )


async def _run(
    registry: dict[str, object], tool_name: str, ctx: ToolContext, **args: object
) -> str:
    tool = registry[tool_name]
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
    return result.content[0].text


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
        if str(request.url) == slack.SLACK_AUTH_TEST_URL:
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

    monkeypatch.setattr(slack.httpx, "AsyncClient", factory)


async def test_slack_connect_walks_the_state_machine(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tool, every state: missing secrets read not_configured; stored secrets derive and
    persist the identity (owner-only, one auth.test); a second call is a pure read; the marker
    flips it to connected; a rotated signing secret reads pending again; a rotated bot token
    stales the identity, and re-deriving is again the owner's act."""
    workspace_id, owner_id, joiner_id = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _auth_test_transport(recorder))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ext = ext_by_tool["slack_connect"]
    owner = _context(workspace_id, ext, blob, owner_id)
    joiner = _context(workspace_id, ext, blob, joiner_id)
    with ws(workspace_id):
        bare = json.loads(await _run(registry, "slack_connect", owner))
        assert bare["state"] == "not_configured"
        assert set(bare["missing"]) == set(tools.SLACK_SECRET_SLOTS)
        assert bare["events_url"] == f"{PUBLIC_BASE_URL}/surface/slack"
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "shhh")
        with pytest.raises(ValueError, match="workspace owner"):
            await _run(registry, "slack_connect", joiner)
        assert recorder == []
        derived = json.loads(await _run(registry, "slack_connect", owner))
        assert derived["state"] == "pending"
        assert derived["team_id"] == TEAM_ID
        assert len(recorder) == 1
        identity = await read_identity(blob, workspace_id, "xoxb-1")
        assert identity is not None and identity.bot_user_id == BOT_USER_ID
        assert identity.bot_token_fingerprint == bot_token_fingerprint("xoxb-1")
        async with workspace_tx() as connection:
            registration = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == workspace_id,
                        tables.surface_installation.c.surface == slack.SURFACE_SLACK,
                    )
                )
            ).scalar_one()
        assert registration == slack.slack_installation_id(TEAM_ID)
        # identity now proven: a joiner's call is a pure read, no auth.test, no gate
        again = json.loads(await _run(registry, "slack_connect", joiner))
        assert again["state"] == "pending"
        assert len(recorder) == 1
        await blob.put(
            url_verified_blob_key(workspace_id),
            json.dumps({"fingerprint": signing_secret_fingerprint("shhh"), "at": 1.0}).encode(),
        )
        assert json.loads(await _run(registry, "slack_connect", owner))["state"] == "connected"
        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "rotated")
        assert json.loads(await _run(registry, "slack_connect", owner))["state"] == "pending"
        # a rotated bot token stales the identity: derive again, owner-only
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-2")
        with pytest.raises(ValueError, match="workspace owner"):
            await _run(registry, "slack_connect", joiner)
        rederived = json.loads(await _run(registry, "slack_connect", owner))
        assert rederived["state"] == "pending"
        assert len(recorder) == 2
        fresh = await read_identity(blob, workspace_id, "xoxb-2")
        assert fresh is not None and fresh.bot_token_fingerprint == bot_token_fingerprint("xoxb-2")


async def test_connect_reports_a_rejected_token_without_persisting(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-revoked")
    await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "shhh")
    _patch_httpx(monkeypatch, _auth_test_transport([], ok=False, error="invalid_auth"))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, owner_id)
    with ws(workspace_id):
        rejected = json.loads(await _run(registry, "slack_connect", ctx))
        assert rejected["state"] == "not_configured"
        assert "rejected the bot token" in rejected["hint"]
        assert await read_identity(blob, workspace_id, "xoxb-revoked") is None
        async with workspace_tx() as connection:
            registrations = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.surface_installation)
                )
            ).scalar_one()
        assert registrations == 0


async def test_slack_connect_rejects_an_installation_owned_by_another_workspace(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_a, owner_a, _ = await _seed()
    workspace_b, owner_b, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    for workspace_id in (workspace_a, workspace_b):
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-shared")
        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "shared-secret")
    _patch_httpx(monkeypatch, _auth_test_transport([]))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ext = ext_by_tool["slack_connect"]

    with ws(workspace_a):
        first = json.loads(
            await _run(registry, "slack_connect", _context(workspace_a, ext, blob, owner_a))
        )
    with ws(workspace_b):
        second = json.loads(
            await _run(registry, "slack_connect", _context(workspace_b, ext, blob, owner_b))
        )

    assert first["state"] == "pending"
    assert second["state"] == "not_configured"
    assert second["hint"] == "This Slack workspace is already connected to another UFO workspace."
    async with workspace_tx() as connection:
        registrations = (
            await connection.execute(
                sa.select(
                    tables.surface_installation.c.workspace_id,
                    tables.surface_installation.c.installation_id,
                ).where(tables.surface_installation.c.surface == slack.SURFACE_SLACK)
            )
        ).all()
    assert [tuple(row) for row in registrations] == [
        (workspace_a, slack.slack_installation_id(TEAM_ID))
    ]


async def test_manifest_tool_matches_the_skill_and_validates_the_name(
    db: None, tmp_path: Path
) -> None:
    """The tool's YAML is the skill's YAML — one manifest, pinned, so scopes and events never
    drift between what the agent renders and what the skill teaches."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_app_manifest"], blob, owner_id)
    served = yaml.safe_load(await _run(registry, "slack_app_manifest", ctx, name="acme bot"))
    assert served["display_information"]["name"] == "acme bot"
    assert served["oauth_config"]["scopes"]["bot"] == [
        "app_mentions:read",
        "assistant:write",
        "channels:history",
        "channels:read",
        "chat:write",
        "files:read",
        "files:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "mpim:history",
        "mpim:read",
        "users:read",
        "users:read.email",
    ]
    assert served["settings"]["event_subscriptions"]["request_url"] == (
        f"{PUBLIC_BASE_URL}/surface/slack"
    )
    assert served["settings"]["interactivity"]["request_url"] == (
        f"{PUBLIC_BASE_URL}/surface/slack/interactive"
    )
    skill_body = skill_registry((slack_manifest(),)).named("slack-app-setup").instructions
    block = re.search(r"```yaml\n(.*?)```", skill_body, re.DOTALL)
    assert block is not None
    skill_yaml = yaml.safe_load(
        block.group(1)
        .replace("<bot display name>", "acme bot")
        .replace("<public_base_url>", PUBLIC_BASE_URL)
    )
    assert served == skill_yaml
    with pytest.raises(ValueError, match="display name"):
        await _run(registry, "slack_app_manifest", ctx, name="<script>")
    bare = _context(workspace_id, ext_by_tool["slack_app_manifest"], blob, owner_id, None)
    with pytest.raises(ValueError, match="public base URL"):
        await _run(registry, "slack_app_manifest", bare)


def _channel_row(
    channel_id: str,
    name: str,
    *,
    purpose: str = "",
    topic: str = "",
    is_private: bool = False,
    is_member: bool = True,
) -> dict[str, object]:
    return {
        "id": channel_id,
        "name": name,
        "is_private": is_private,
        "is_member": is_member,
        "purpose": {"value": purpose},
        "topic": {"value": topic},
    }


def _user(real_name: str, email: str) -> dict[str, object]:
    return {"real_name": real_name, "profile": {"email": email}, "is_email_confirmed": True}


async def _seed_identity(blob: FilesystemBlobStore, workspace_id: UUID, bot_token: str) -> None:
    """The derived-identity blob slack_connect would have written, so the tool knows the bot's own
    user id and can drop it from a group DM's people."""
    identity = slack.SlackIdentity(
        bot_token_fingerprint=bot_token_fingerprint(bot_token),
        team_id=TEAM_ID,
        bot_user_id=BOT_USER_ID,
    )
    await blob.put(slack.identity_blob_key(workspace_id), identity.model_dump_json().encode())


def _directory_transport(
    pages: list[dict[str, object]],
    *,
    members: dict[str, list[str]] | None = None,
    users: dict[str, dict[str, object]] | None = None,
    recorder: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Serve the three reads the tool makes: conversations.list (paged by the previous page's
    cursor), conversations.members for a group DM, and users.info for a DM member."""
    members = members or {}
    users = users or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request)
        path = request.url.path
        if path == "/api/conversations.list":
            cursor = request.url.params.get("cursor", "")
            return httpx.Response(200, json=pages[int(cursor) if cursor else 0])
        if path == "/api/conversations.members":
            channel = request.url.params.get("channel", "")
            return httpx.Response(200, json={"ok": True, "members": members.get(channel, [])})
        if path == "/api/users.info":
            user = users.get(request.url.params.get("user", ""))
            if user is None:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": user})
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    return httpx.MockTransport(handler)


async def test_slack_channels_searches_across_pages(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool reads the app's own bot token, pages conversations.list, and returns the channels
    whose name, purpose, or topic match the query — the consumer the manifest's read scopes exist
    for. The result is walled untrusted because its text is member-authored."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
    pages: list[dict[str, object]] = [
        {
            "ok": True,
            "channels": [
                _channel_row("C1", "general"),
                _channel_row("C2", "engineering", purpose="ship the product", is_private=True),
            ],
            "response_metadata": {"next_cursor": "1"},
        },
        {
            "ok": True,
            "channels": [
                _channel_row("C3", "random", topic="engineering memes"),
                _channel_row("C4", "ops", purpose="engineering on-call rotation"),
            ],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    _patch_httpx(monkeypatch, _directory_transport(pages))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_identity(blob, workspace_id, "xoxb-1")
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        matched = await tool.handler(ctx, tool.input_model.model_validate({"query": "engineering"}))
        listed = await tool.handler(ctx, tool.input_model.model_validate({}))
    assert matched.untrusted is True
    payload = json.loads(matched.content[0].text)
    # "engineering" hits each match on a different field — C2 by name, C3 by topic, C4 only by
    # purpose ("ops" name and empty topic don't contain it) — across both pages.
    by_id = {convo["id"]: convo for convo in payload["conversations"]}
    assert set(by_id) == {"C2", "C3", "C4"}
    assert by_id["C4"]["purpose"] == "engineering on-call rotation"
    assert by_id["C2"]["kind"] == "private"
    assert payload["truncated"] is False
    assert json.loads(listed.content[0].text)["conversations"][0] == {
        "id": "C1",
        "kind": "channel",
        "name": "general",
        "people": [],
        "purpose": "",
        "topic": "",
        "is_member": True,
    }
    assert len(json.loads(listed.content[0].text)["conversations"]) == 4


async def test_slack_channels_finds_a_dm_by_its_people(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DM has no name, so it is found by who is in it: the tool resolves each 1:1 DM's and group
    DM's members to their display name/email — dropping the bot itself — and matches the query on
    those, giving the agent the conversation id to act on."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_identity(blob, workspace_id, "xoxb-1")
    pages: list[dict[str, object]] = [
        {
            "ok": True,
            "channels": [
                _channel_row("C1", "general"),
                {"id": "D1", "is_im": True, "user": "U_ALICE", "is_member": True},
                {
                    "id": "G1",
                    "is_mpim": True,
                    "is_private": True,
                    "name": "mpdm-alice--bob-1",
                    "is_member": True,
                },
            ],
            "response_metadata": {"next_cursor": ""},
        }
    ]
    users = {
        "U_ALICE": _user("Alice Eng", "alice@acme.com"),
        "U_BOB": _user("Bob", "bob@acme.com"),
    }
    recorder: list[httpx.Request] = []
    transport = _directory_transport(
        pages, members={"G1": [BOT_USER_ID, "U_ALICE", "U_BOB"]}, users=users, recorder=recorder
    )
    _patch_httpx(monkeypatch, transport)
    registry, ext_by_tool = _registry(store)
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate({"query": "alice"}))
    by_id = {convo["id"]: convo for convo in json.loads(result.content[0].text)["conversations"]}
    assert set(by_id) == {"D1", "G1"}
    assert by_id["D1"]["kind"] == "im"
    assert by_id["D1"]["name"] == ""
    assert by_id["D1"]["people"] == ["Alice Eng (alice@acme.com)"]
    # the group DM resolves both members and drops the bot itself.
    assert by_id["G1"]["kind"] == "mpim"
    assert by_id["G1"]["people"] == ["Alice Eng (alice@acme.com)", "Bob (bob@acme.com)"]
    # The two DM kinds are consumed by distinct requests, the acts im:read/mpim:read authorize: the
    # list asks Slack for both im and mpim, the 1:1 DM's member comes straight off the list object,
    # and only the group DM triggers a conversations.members read.
    requested_types = recorder[0].url.params["types"].split(",")
    assert {"im", "mpim"} <= set(requested_types)
    members_channels = [
        request.url.params["channel"]
        for request in recorder
        if request.url.path == "/api/conversations.members"
    ]
    assert members_channels == ["G1"]


async def test_slack_channels_requires_a_resolved_identity(db: None, tmp_path: Path) -> None:
    """The bot token is stored but slack_connect never derived the identity: the tool fails loud
    toward slack_connect rather than searching without knowing its own id — which would leak the
    bot into a group DM's people."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        with pytest.raises(ValueError, match="identity is not resolved"):
            await tool.handler(ctx, tool.input_model.model_validate({}))


async def test_slack_channels_caps_people_resolution(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More DMs than SLACK_PEOPLE_RESOLVE_MAX cannot fan out an unbounded number of member lookups:
    resolution stops at the cap and the result is reported truncated so the agent narrows."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_identity(blob, workspace_id, "xoxb-1")
    dms = [
        {"id": f"D{n}", "is_im": True, "user": "U_ALICE", "is_member": True}
        for n in range(slack.SLACK_PEOPLE_RESOLVE_MAX + 1)
    ]
    pages: list[dict[str, object]] = [
        {"ok": True, "channels": dms, "response_metadata": {"next_cursor": ""}}
    ]
    users = {"U_ALICE": _user("Alice Eng", "alice@acme.com")}
    _patch_httpx(monkeypatch, _directory_transport(pages, users=users))
    registry, ext_by_tool = _registry(store)
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate({}))
    assert json.loads(result.content[0].text)["truncated"] is True


async def test_slack_channels_stops_at_the_page_bound(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace that keeps handing back a cursor — or a malformed page that never empties it —
    cannot spin the tool unbounded: it issues at most SLACK_CONVERSATIONS_MAX_PAGES requests and
    reports the result as truncated so the agent narrows and searches again."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-1")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}},
        )

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_identity(blob, workspace_id, "xoxb-1")
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate({"query": "eng"}))
    assert len(requests) == slack.SLACK_CONVERSATIONS_MAX_PAGES
    # the read scopes are exercised: every list request asks Slack for DMs and group DMs too.
    assert requests[0].url.params["types"] == "public_channel,private_channel,mpim,im"
    payload = json.loads(result.content[0].text)
    assert payload["conversations"] == []
    assert payload["truncated"] is True


async def test_slack_channels_needs_the_bot_token(db: None, tmp_path: Path) -> None:
    """No bot token means Slack is not connected — the tool fails loud toward slack_connect rather
    than calling Slack unauthenticated."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_channels"], blob, owner_id)
    tool = registry["slack_channels"]
    with ws(workspace_id):
        with pytest.raises(ValueError, match="Slack is not connected"):
            await tool.handler(ctx, tool.input_model.model_validate({}))


def test_slack_writeback_hints_at_the_terminal_for_a_credential_request() -> None:
    """Slack never collects a secret: a turn that asked for credentials renders as a pointer to
    the member's own terminal, where the member asks again and the prompts render privately."""
    writeback = Writeback(
        turn_id=uuid4(),
        queue_key="C1:1.0",
        status="done",
        text="I need two values from Slack.",
        tokens=0,
        cost_micro_usd=0,
        cache_percent=0,
        model="",
        reasoning=None,
        artifacts=(),
        question=None,
        credential_request=CredentialRequest(
            reason="connecting Slack",
            prompts=(CredentialPrompt(slot=SLACK_BOT_TOKEN_SLOT, prompt="Bot User OAuth Token"),),
            sealed="opaque",
        ),
        connect_request=None,
    )
    text = _reply_with_oversize_links(cast(SurfaceContext, None), writeback)
    assert "connecting Slack" in text
    assert "`ufo`" in text
    assert "secrets never pass through chat" in text


def test_reply_text_renders_a_cancelled_turns_reason() -> None:
    """A cancelled turn carrying the gate's reason — a seat refusal, a spend rejection — delivers
    that reason to the thread; the static marker covers only a reasonless cancellation."""
    refusal = Writeback(
        turn_id=uuid4(),
        queue_key="C1:1.0",
        status="cancelled",
        text="This workspace has no open seat for you yet — ask the workspace owner.",
        tokens=0,
        cost_micro_usd=0,
        cache_percent=0,
        model="",
        reasoning=None,
        artifacts=(),
        question=None,
        credential_request=None,
        connect_request=None,
    )
    assert _reply_text(refusal) == refusal.text
    bare = replace(refusal, text="")
    assert _reply_text(bare) == slack.SLACK_TURN_CANCELLED_TEXT
