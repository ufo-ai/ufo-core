"""The Slack install actions over real dispatch: `slack_connect` walks the install state machine off
the real credential store, identity record, and marker blob for both paths — the default OAuth path
mints the owner an "Add to Slack" link (falling back to manifest when the deploy has no app), and
`method="manifest"` walks not_configured → pending (identity derived via `auth.test`) → connected —
and `slack_app_manifest` renders the exact YAML the skill teaches. The three are instance actions
on core's `surface/slack` object: a read of that object lists them before Slack is connected, and
the real TurnEngine dispatches an `object_action` call to each handler under this extension's
context."""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_slack.surface as slack
import yaml
from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_slack.manifest import manifest as slack_manifest
from ufo_ext_slack.surface import (
    IDENTITY_BLOB_KEY,
    MARKDOWN_LINK_PATTERN,
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    URL_VERIFIED_BLOB_KEY,
    _reply_text,
    _reply_with_oversize_links,
    bot_token_fingerprint,
    signing_secret_fingerprint,
)
from ufo_ext_slack.tools import (
    SLACK_SECRET_SLOTS,
    SlackChannelsInput,
    SlackConnectInput,
    SlackManifestInput,
)

from ufo.access.connectors import ConnectorRegistry
from ufo.access.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CredentialRequests,
    CredentialStore,
    open_credential_request,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext
from ufo.ext.loader import skill_registry, turn_hooks, turn_tools
from ufo.hub import InProcessHub
from ufo.kinds.surface_kind import SURFACE_KIND
from ufo.loop.compaction import Compaction
from ufo.loop.engine import TurnEngine
from ufo.loop.prompts.render import rendered_prompt
from ufo.loop.queue import _agent_actions, _agent_tools, _with_action_verbs
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, Agent, Turn, Usage
from ufo.sdk.audience import conversation_audience
from ufo.sdk.objects import ObjectActionTarget
from ufo.sdk.surfaces import (
    CredentialPrompt,
    CredentialRequest,
    TerminalFrame,
    Writeback,
)
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ObjectBinding, ToolDef, ToolRegistry
from ufo.turns.activity import ActivitySummarizer
from ufo.workspace import init_workspace_credentials, ws

TOOL_NARRATION = "getting Slack connected"

OWNER_EMAIL = "owner@acme.com"
JOINER_EMAIL = "late@acme.com"
PUBLIC_BASE_URL = "https://tenant.example.com"
CLIENT_ID = "112233.445566"
SIGNING_SECRET = "signing-secret"
TEAM_ID = "T012345"
BOT_USER_ID = "U098765"


@pytest.fixture(autouse=True)
def _deploy_secrets():
    """The deploy's OAuth app env, set for every test. Own MonkeyPatch, not the shared
    `monkeypatch` fixture — see the note in test_ext_slack.py: depending on `monkeypatch` reorders
    it ahead of other autouse fixtures at teardown. Individual tests unset these to exercise the
    no-OAuth (manifest-only) deploy."""
    patch = pytest.MonkeyPatch()
    patch.setenv(slack.SLACK_CLIENT_ID_ENV, CLIENT_ID)
    patch.setenv(slack.SLACK_CLIENT_SECRET_ENV, "client-secret")
    patch.setenv(slack.SLACK_SIGNING_SECRET_ENV, SIGNING_SECRET)
    try:
        yield
    finally:
        patch.undo()


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("a setup tool must not touch the sandbox")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError("a setup tool must not touch the sandbox")


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("a setup tool must not spawn a subagent")


async def _seed() -> tuple[UUID, UUID, UUID]:
    """A workspace with one admin plus one member, exercising both sides of the connect gate."""
    workspace_id, owner_id, joiner_id, agent_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
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
                    is_admin=member_id == owner_id,
                    created_at=created,
                    updated_at=created,
                )
            )
    return workspace_id, owner_id, joiner_id


def _registry(
    store: CredentialStore,
) -> tuple[dict[str, ToolDef], dict[str, ExtensionContext | None]]:
    """The Slack actions as the deploy registers them — bound to the `surface` kind, so they ride
    the action registry rather than the wire tool set — beside the context each dispatches under."""
    _, _, verbs = turn_tools((slack_manifest(),), store, audience=conversation_audience(None))
    actions = verbs.actions[SURFACE_KIND]
    return (
        {name: bound.action for name, bound in actions.items()},
        {name: bound.context for name, bound in actions.items()},
    )


def _context(
    workspace_id: UUID,
    ext: ExtensionContext | None,
    blob: FilesystemBlobStore,
    member_id: UUID | None,
    store: CredentialStore | None = None,
    public_base_url: str | None = PUBLIC_BASE_URL,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=WorkspaceBlobStore(backend=blob),
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
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
        public_base_url=public_base_url,
        target=ObjectActionTarget(
            kind=SURFACE_KIND,
            name=slack.SURFACE_SLACK,
            agent=None,
            generation=None,
            expected_generation=None,
        ),
        requestable_credentials=(
            None
            if store is None
            else CredentialRequests(
                fernet=store.fernet,
                declared=frozenset(slot.name for slot in slack_manifest().credentials),
                fillable=frozenset(
                    slot.name for slot in slack_manifest().credentials if slot.member_filled
                ),
            )
        ),
    )


async def _run(
    registry: dict[str, ToolDef], tool_name: str, ctx: ToolContext, **args: object
) -> str:
    tool = registry[tool_name]
    with ws(ctx.turn.workspace_id):
        result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    return result.content[0].text


class _ActivityModel:
    model = "gpt-5.6-luna"

    async def complete(self, request: ModelRequest) -> str:
        return "Connecting Slack."


class _OneCallModel:
    """Emits one tool call in its first round and answers once the result returns."""

    def __init__(self, name: str, args: dict[str, object]) -> None:
        self.name = name
        self.args = args

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        answered = any(
            isinstance(message.content, tuple)
            and any(isinstance(block, ToolResultBlock) for block in message.content)
            for message in request.messages
        )
        if answered:
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name=self.name)
        yield ToolCallDelta(id="c1", partial_json=json.dumps(self.args))
        yield Usage(input_tokens=1, output_tokens=1)


async def _seed_turn(workspace_id: UUID, speaker_member_id: UUID) -> Turn:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id, tables.agent.c.is_main
                )
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=speaker_member_id,
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
                status="queued",
                inbound="connect slack",
                admission_source=MEMBER_ADMISSION,
                speaker_member_id=speaker_member_id,
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="queued",
        inbound="connect slack",
        admission_source=MEMBER_ADMISSION,
        speaker_member_id=speaker_member_id,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        terminal=None,
    )


async def _dispatch(
    store: CredentialStore,
    blob: FilesystemBlobStore,
    turn: Turn,
    action: str,
    **args: object,
) -> ToolResultBlock:
    """One `object_action` call on `surface/slack` through the real TurnEngine: the model names the
    action, the engine resolves it against the action registry, reads the surface row, and runs the
    handler under the slack extension's context. Returns the round's tool result."""
    audience = conversation_audience(turn.speaker_member_id)
    all_tools, tool_ext, verbs = turn_tools(
        (slack_manifest(),), store, audience=audience, public_base_url=PUBLIC_BASE_URL
    )
    granted = _agent_actions(verbs.actions, None, MEMBER_ADMISSION)
    registry = ToolRegistry(
        _with_action_verbs(_agent_tools(all_tools, None, MEMBER_ADMISSION), all_tools, granted)
    )
    model = _OneCallModel(
        "object_action",
        {"kind": SURFACE_KIND, "name": slack.SURFACE_SLACK, "action": action, "input": args},
    )
    workspace_blob = WorkspaceBlobStore(backend=blob)
    engine = TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        byok=False,
        system_prompt=rendered_prompt("p"),
        model=model,
        activity_summarizer=ActivitySummarizer(_ActivityModel()),
        provider="anthropic",
        transcript=Transcript(blob=workspace_blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model,
            model="claude-opus-4-8",
            blob=workspace_blob,
            conversation_id=turn.conversation_id,
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=turn.conversation_id, container_id="test"),
        ),
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=registry,
        tool_ext=tool_ext,
        hooks=turn_hooks((), store, audience=audience),
        blob=workspace_blob,
        spawn=_unavailable_spawn,
        audience=audience,
        artifact_token_secret="",
        grants=None,
        verbs=verbs,
        granted_actions=granted,
        public_base_url=PUBLIC_BASE_URL,
    )
    frame = await engine.run()
    assert frame is not None and frame.status == "done"
    stored = await engine.transcript.read()
    assert stored is not None
    return next(
        block
        for message in stored.messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock)
    )


async def _write_identity(blob: FilesystemBlobStore, workspace_id: UUID, bot_token: str) -> None:
    identity = slack.SlackIdentity(
        bot_token_fingerprint=bot_token_fingerprint(bot_token),
        team_id=TEAM_ID,
        bot_user_id=BOT_USER_ID,
    )
    with ws(workspace_id):
        await WorkspaceBlobStore(backend=blob).put(
            IDENTITY_BLOB_KEY, identity.model_dump_json().encode()
        )


async def _mark_verified(
    blob: FilesystemBlobStore, workspace_id: UUID, secret: str = SIGNING_SECRET
) -> None:
    """Stamp the url-verified marker with the fingerprint of the verifying secret, as a
    signature-verified inbound request would — the signal `slack_connect` reads as `connected`."""
    with ws(workspace_id):
        await WorkspaceBlobStore(backend=blob).put(
            URL_VERIFIED_BLOB_KEY,
            json.dumps({"fingerprint": signing_secret_fingerprint(secret), "at": 1.0}).encode(),
        )


def _auth_test_transport(
    recorder: list[httpx.Request], *, ok: bool = True, error: str = ""
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if str(request.url) == slack.SLACK_AUTH_TEST_URL:
            if ok:
                return httpx.Response(
                    200, json={"ok": True, "team_id": TEAM_ID, "user_id": BOT_USER_ID}
                )
            return httpx.Response(200, json={"ok": False, "error": error})
        return httpx.Response(404, json={"ok": False, "error": "not_mocked"})

    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(slack.httpx, "AsyncClient", factory)


async def test_admin_mints_an_add_to_slack_link_carrying_sealed_state(
    db: None, tmp_path: Path
) -> None:
    """With Slack not yet installed, the admin's call returns not_installed and an authorize link
    that carries the deploy app's client id, the bot scopes, the redirect, and a state that
    decrypts to this workspace, this owner, and the bot-token install marker."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, owner_id, store)
    with ws(workspace_id):
        result = json.loads(await _run(registry, "slack_connect", ctx))
    assert result["state"] == "not_installed"
    parsed = urlparse(result["authorize_url"])
    assert parsed.netloc == "slack.com" and parsed.path == "/oauth/v2/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == [CLIENT_ID]
    assert set(query["scope"][0].split(",")) == set(slack.SLACK_BOT_SCOPES)
    assert query["redirect_uri"] == [f"{PUBLIC_BASE_URL}/surface/slack/oauth"]
    claims = open_credential_request(
        store.fernet, query["state"][0], purpose=CREDENTIAL_REQUEST_PURPOSE
    )
    assert claims.workspace_id == workspace_id
    assert claims.member_id == owner_id
    assert claims.slots == (SLACK_BOT_TOKEN_SLOT,)
    assert claims.payload == slack.SLACK_INSTALL_PAYLOAD


async def test_non_admin_reads_state_but_cannot_mint(db: None, tmp_path: Path) -> None:
    """A member sees the install state but gets no link — the bot is shared, so only an admin
    installs it."""
    workspace_id, _, joiner_id = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, joiner_id, store)
    with ws(workspace_id):
        result = json.loads(await _run(registry, "slack_connect", ctx))
    assert result["state"] == "not_installed"
    assert "authorize_url" not in result
    assert "admin" in result["hint"]


async def test_connect_reads_pending_then_connected(db: None, tmp_path: Path) -> None:
    """A stored bot token with a matching identity but no verified marker reads `pending`; once a
    signature-verified request has stamped the marker it reads `connected`, for owner and joiner
    alike — a pure read, no link minted."""
    workspace_id, owner_id, joiner_id = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-installed")
    await _write_identity(blob, workspace_id, "xoxb-installed")
    ext = ext_by_tool["slack_connect"]
    owner_ctx = _context(workspace_id, ext, blob, owner_id, store)
    with ws(workspace_id):
        pending = json.loads(await _run(registry, "slack_connect", owner_ctx))
        assert pending["state"] == "pending"
        await _mark_verified(blob, workspace_id)
        for member_id in (owner_id, joiner_id):
            result = json.loads(
                await _run(
                    registry, "slack_connect", _context(workspace_id, ext, blob, member_id, store)
                )
            )
            assert result["state"] == "connected"
            assert result["team_id"] == TEAM_ID


async def test_oauth_default_falls_back_to_manifest_when_unconfigured(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deploy with no OAuth app configured reports not_configured with a pointer to the
    bring-your-own-app path, rather than minting a link it cannot complete."""
    monkeypatch.delenv(slack.SLACK_CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(slack.SLACK_CLIENT_SECRET_ENV, raising=False)
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, owner_id, store)
    with ws(workspace_id):
        result = json.loads(await _run(registry, "slack_connect", ctx))
    assert result["state"] == "not_configured"
    assert "manifest" in result["hint"]


async def test_stale_identity_reads_not_installed(db: None, tmp_path: Path) -> None:
    """A stored token whose identity record was written for a different token (a reinstall left the
    fingerprint mismatched) reads not_installed, so the owner reconnects via OAuth."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-new")
    await _write_identity(blob, workspace_id, "xoxb-old")
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, owner_id, store)
    with ws(workspace_id):
        result = json.loads(await _run(registry, "slack_connect", ctx))
    assert result["state"] == "not_installed"
    assert "authorize_url" in result


async def test_manifest_path_walks_not_configured_to_pending_to_connected(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bring-your-own-app path: with no OAuth app on the deploy, `method='manifest'` reports
    not_configured until both secret slots are filled, derives identity with `auth.test` (owner-
    only) into `pending`, and a verified marker flips it to `connected`."""
    monkeypatch.delenv(slack.SLACK_CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(slack.SLACK_CLIENT_SECRET_ENV, raising=False)
    monkeypatch.delenv(slack.SLACK_SIGNING_SECRET_ENV, raising=False)
    workspace_id, owner_id, joiner_id = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    recorder: list[httpx.Request] = []
    _patch_httpx(monkeypatch, _auth_test_transport(recorder))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ext = ext_by_tool["slack_connect"]
    owner = _context(workspace_id, ext, blob, owner_id, store)
    joiner = _context(workspace_id, ext, blob, joiner_id, store)
    with ws(workspace_id):
        bare = json.loads(await _run(registry, "slack_connect", owner, method="manifest"))
        assert bare["state"] == "not_configured"
        assert set(bare["missing"]) == set(SLACK_SECRET_SLOTS)
        assert bare["events_url"] == f"{PUBLIC_BASE_URL}/surface/slack"
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-byo")
        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "byo-secret")
        with pytest.raises(ValueError, match="workspace admin"):
            await _run(registry, "slack_connect", joiner, method="manifest")
        assert recorder == []
        derived = json.loads(await _run(registry, "slack_connect", owner, method="manifest"))
        assert derived["state"] == "pending"
        assert derived["team_id"] == TEAM_ID
        assert len(recorder) == 1
        assert await slack.read_identity(WorkspaceBlobStore(backend=blob), "xoxb-byo") is not None
        await _mark_verified(blob, workspace_id, "byo-secret")
        connected = json.loads(await _run(registry, "slack_connect", owner, method="manifest"))
        assert connected["state"] == "connected"


async def test_manifest_path_reports_a_rejected_token(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bring-your-own-app token Slack rejects reads not_configured with a re-copy hint, and no
    identity is persisted."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    with ws(workspace_id):
        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-revoked")
        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "byo-secret")
    _patch_httpx(monkeypatch, _auth_test_transport([], ok=False, error="invalid_auth"))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_connect"], blob, owner_id, store)
    with ws(workspace_id):
        rejected = json.loads(await _run(registry, "slack_connect", ctx, method="manifest"))
    assert rejected["state"] == "not_configured"
    assert "rejected the bot token" in rejected["hint"]
    with ws(workspace_id):
        assert await slack.read_identity(WorkspaceBlobStore(backend=blob), "xoxb-revoked") is None


async def test_manifest_tool_matches_the_skill_and_validates_the_name(
    db: None, tmp_path: Path
) -> None:
    """The tool's YAML is the skill's YAML — one manifest, pinned, so scopes and events never drift
    between what the agent renders and what the skill teaches."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    registry, ext_by_tool = _registry(store)
    blob = FilesystemBlobStore(root=tmp_path)
    ctx = _context(workspace_id, ext_by_tool["slack_app_manifest"], blob, owner_id, store)
    served = yaml.safe_load(await _run(registry, "slack_app_manifest", ctx, bot_name="acme bot"))
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
    skill_body = skill_registry((slack_manifest(),)).named("slack-app-setup").instructions
    assert "Only a workspace admin can fill these" in skill_body
    assert "workspace owner" not in skill_body
    block = re.search(r"```yaml\n(.*?)```", skill_body, re.DOTALL)
    assert block is not None
    skill_yaml = yaml.safe_load(
        block.group(1)
        .replace("<bot display name>", "acme bot")
        .replace("<public_base_url>", PUBLIC_BASE_URL)
    )
    assert served == skill_yaml
    with pytest.raises(ValueError, match="display name"):
        await _run(registry, "slack_app_manifest", ctx, bot_name="<script>")


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
    with ws(workspace_id):
        await WorkspaceBlobStore(backend=blob).put(
            slack.IDENTITY_BLOB_KEY, identity.model_dump_json().encode()
        )


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
        matched = await tool.handler(
            ctx,
            tool.input_model.model_validate({"query": "engineering"}),
        )
        listed = await tool.handler(
            ctx,
            tool.input_model.model_validate({}),
        )
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
        result = await tool.handler(
            ctx,
            tool.input_model.model_validate({"query": "alice"}),
        )
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
            await tool.handler(
                ctx,
                tool.input_model.model_validate({}),
            )


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
        result = await tool.handler(
            ctx,
            tool.input_model.model_validate({}),
        )
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
        result = await tool.handler(
            ctx,
            tool.input_model.model_validate({"query": "eng"}),
        )
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
            await tool.handler(
                ctx,
                tool.input_model.model_validate({}),
            )


@dataclass(frozen=True)
class _PortalCtx:
    """Stands in for the surface context's one collaborator this rendering reads. `home_url`'s own
    format is proved against the real context in `core/tests/ext/test_surface.py`; what is asserted
    here is what Slack does with the address it is handed, and what it says without one."""

    base: str | None

    def home_url(self, fragment: str = "") -> str | None:
        return None if self.base is None else f"{self.base}{fragment}"


def test_slack_writeback_links_the_portal_for_a_credential_request() -> None:
    """Slack collects no secret, so it hands the member a link to the one screen that fills a slot
    whatever raised it. The link is Markdown, matching the oversize-artifact lines this same text
    carries, because the reply rides a Block Kit `markdown` block — Slack's `<url|label>` form
    would print verbatim there. A deploy with no address names the screen in words instead."""
    writeback = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key="C1:1.0",
        terminal=TerminalFrame(
            status="done",
            text="I need a value from Slack.",
            credential_request=CredentialRequest(
                reason="connecting Slack",
                prompts=(
                    CredentialPrompt(slot=SLACK_BOT_TOKEN_SLOT, prompt="Bot User OAuth Token"),
                ),
                sealed="opaque",
            ),
        ),
        artifacts=(),
    )
    linked = _reply_with_oversize_links(
        _PortalCtx("https://ufo.example.test/surface/web"), writeback
    )
    assert "connecting Slack" in linked
    assert (
        "[Workspace → Credentials]"
        "(https://ufo.example.test/surface/web#/workspace/credentials)" in linked
    )
    assert re.search(MARKDOWN_LINK_PATTERN, linked) is not None
    assert "|Workspace" not in linked
    assert "secrets never pass through chat" in linked
    assert "terminal" not in linked and "`ufo`" not in linked

    unlinked = _reply_with_oversize_links(_PortalCtx(None), writeback)
    assert "the ufo portal, under Workspace → Credentials" in unlinked
    assert "http" not in unlinked and "[" not in unlinked


def test_reply_text_renders_a_cancelled_turns_reason() -> None:
    """A cancelled turn carrying the gate's reason — a seat refusal, a spend rejection — delivers
    that reason to the thread; the static marker covers only a reasonless cancellation."""
    refusal = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key="C1:1.0",
        terminal=TerminalFrame(
            status="cancelled",
            text="This workspace has no open seat for you yet — ask a workspace admin.",
        ),
        artifacts=(),
    )
    assert _reply_text(refusal) == refusal.terminal.text
    bare = replace(refusal, terminal=TerminalFrame(status="cancelled"))
    assert _reply_text(bare) == slack.SLACK_TURN_CANCELLED_TEXT


def test_tool_inputs_refuse_an_extra_key() -> None:
    for model, args in (
        (SlackConnectInput, {"method": "oauth"}),
        (SlackManifestInput, {"bot_name": "ufo"}),
        (SlackChannelsInput, {"query": "general"}),
    ):
        with pytest.raises(ValidationError, match="surprise"):
            model.model_validate({**args, "surprise": "x"})


def test_the_slack_tools_are_instance_actions_on_the_slack_surface() -> None:
    """Each setup tool binds to the `surface` kind as an instance action pinned to the Slack row, so
    none rides the wire tool set, each answers to `action:surface:<name>`, and none is offered on
    another surface's row. `slack_connect` binds an installation and
    seals an install handoff — durable writes — so it is side-effecting and keyed; it declares the
    presentation the portal's connect step submits through. `slack_channels` keeps its untrusted
    wall over Slack's own text."""
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    wire, _, verbs = turn_tools((slack_manifest(),), store, audience=conversation_audience(None))
    actions = verbs.actions[SURFACE_KIND]
    assert set(actions) == {"slack_connect", "slack_app_manifest", "slack_channels"}
    assert not {"slack_connect", "slack_app_manifest", "slack_channels"} & {t.name for t in wire}
    for name, bound in actions.items():
        assert bound.action.bound == ObjectBinding(
            kind=SURFACE_KIND, binding="instance", name=slack.SURFACE_SLACK
        )
        assert bound.action.canonical_id == f"action:{SURFACE_KIND}:{name}"
        assert bound.extension == slack_manifest().name
    connect = actions["slack_connect"].action
    assert connect.side_effecting is True
    assert connect.untrusted is True
    assert connect.presentation is not None and connect.presentation.label == "Connect Slack"
    assert actions["slack_channels"].action.untrusted is True
    assert actions["slack_channels"].action.side_effecting is False
    assert actions["slack_app_manifest"].action.presentation is None


async def test_reading_the_slack_surface_lists_its_actions_before_slack_is_connected(
    db: None, tmp_path: Path
) -> None:
    """Discovery is the setup path's front door: a granted agent reading `surface/slack` on a
    workspace with no installation sees the three actions with their call templates pre-bound to the
    surface, so connecting Slack starts from an object read and never from a memorized tool name."""
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    tools, _, verbs = turn_tools((slack_manifest(),), store, audience=conversation_audience(None))
    get = next(tool for tool in tools if tool.name == "object_get")
    ctx = replace(
        _context(workspace_id, None, FilesystemBlobStore(root=tmp_path), owner_id, store),
        target=None,
        granted_actions=_agent_actions(verbs.actions, None, MEMBER_ADMISSION),
    )
    with ws(workspace_id):
        result = await get.handler(
            ctx, get.input_model.model_validate({"kind": SURFACE_KIND, "name": slack.SURFACE_SLACK})
        )
    read = yaml.safe_load(result.content[0].text)
    assert read["status"] == {"bound": False}
    assert [view["name"] for view in read["actions"]] == [
        "slack_app_manifest",
        "slack_channels",
        "slack_connect",
    ]
    for view in read["actions"]:
        assert view["call"] == {
            "kind": SURFACE_KIND,
            "action": view["name"],
            "name": slack.SURFACE_SLACK,
            "input": {},
        }
    manifest_view = read["actions"][0]
    assert set(manifest_view["input_schema"]["properties"]) == {"bot_name"}


async def test_dispatch_reaches_the_manifest_handler_under_the_slack_context(
    db: None, tmp_path: Path
) -> None:
    workspace_id, owner_id, _ = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    turn = await _seed_turn(workspace_id, owner_id)
    with ws(workspace_id):
        result = await _dispatch(
            store, FilesystemBlobStore(root=tmp_path), turn, "slack_app_manifest", bot_name="acme"
        )
    assert result.is_error is False
    served = yaml.safe_load(str(result.content))
    assert served["display_information"]["name"] == "acme"
    assert served["settings"]["event_subscriptions"]["request_url"] == (
        f"{PUBLIC_BASE_URL}/surface/slack"
    )


async def test_dispatch_holds_the_connect_admin_gate_and_the_channels_precondition(
    db: None, tmp_path: Path
) -> None:
    """The handlers' own gates answer through dispatch exactly as they do in chat: a member who is
    not an admin reads the install state and is told who installs, minting no link; a channel search
    with no bot token fails loud toward `slack_connect` rather than searching blind."""
    workspace_id, _, joiner_id = await _seed()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        connect = await _dispatch(
            store, blob, await _seed_turn(workspace_id, joiner_id), "slack_connect"
        )
        channels = await _dispatch(
            store, blob, await _seed_turn(workspace_id, joiner_id), "slack_channels", query="eng"
        )
    assert connect.is_error is False
    stated = json.loads(re.search(r"\{.*\}", str(connect.content), re.DOTALL).group())
    assert stated["state"] == "not_installed"
    assert "admin" in stated["hint"]
    assert "authorize_url" not in stated
    assert channels.is_error is True
    assert "Slack is not connected" in str(channels.content)
