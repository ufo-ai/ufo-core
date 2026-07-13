"""The owner-gated source catalog and registration act."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import SyncSourceInput, _validated_base_url, sync_source

from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.grants import GrantStore
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.tools import ToolContext, ToolResult
from ufo.workspace import ws

ASANA = "asana"
ASANA_HOST = "app.asana.com"
GREENHOUSE = "greenhouse"
FRESHDESK = "freshdesk"
DECLARED_PROVIDERS = frozenset(CONNECTORS)


class _UnusedBroker:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> None:
        raise AssertionError("sync_source must not resolve provider credentials")


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


async def _workspace() -> _Workspace:
    workspace_id = uuid4()
    owner_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=created_at, updated_at=created_at
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": owner_id,
                    "workspace_id": workspace_id,
                    "email": f"{owner_id.hex}@x.test",
                    "created_at": created_at,
                    "updated_at": created_at,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "created_at": created_at + timedelta(seconds=1),
                    "updated_at": created_at + timedelta(seconds=1),
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=owner_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return _Workspace(workspace_id, owner_id, member_id, agent_id, conversation_id)


def _context(
    state: _Workspace,
    grants: GrantStore | None,
    *,
    brokered: tuple[str, ...] = (),
    speaker_id: UUID | None = None,
    direct_fallback: bool = True,
) -> ToolContext:
    ext = context_for(NAME, DECLARED_PROVIDERS)
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=state.agent_id,
            seq=1,
            status="running",
            inbound="connect a source",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker_id or state.owner_id,
        audience_member_id=speaker_id or state.owner_id,
        artifact_token_secret="",
        grants=grants,
        connectors=ConnectorRegistry(
            entries={
                provider: ConnectorEntry(provider=provider, label=provider, broker=_UnusedBroker())
                for provider in brokered
            },
            fallback=DirectAuthProxy(credentials=ext.credentials) if direct_fallback else None,
        ),
        ext=ext,
    )


async def _grant(state: _Workspace, store: GrantStore, provider: str, account: str) -> None:
    await store.record(
        workspace_id=state.workspace_id,
        agent_id=state.agent_id,
        provider=provider,
        account_id=account,
        host=ASANA_HOST,
        grantor_member_id=state.owner_id,
        conversation_id=state.conversation_id,
    )


def _payload(result: ToolResult) -> dict[str, object]:
    return json.loads(result.content[0].text)


async def _rows(state: _Workspace, backend: str) -> list[sa.RowMapping]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.source)
                        .where(
                            tables.source.c.workspace_id == state.workspace_id,
                            tables.source.c.backend == backend,
                        )
                        .order_by(tables.source.c.id)
                    )
                )
                .mappings()
                .all()
            )


def test_manifest_declares_sync_source_as_side_effecting() -> None:
    declared = manifest()
    [tool] = declared.tools
    assert tool.name == "sync_source"
    assert tool.side_effecting is True
    assert tool.handler is sync_source
    assert {slot.name for slot in declared.credentials} == set(CONNECTORS)
    assert {source.backend for source in declared.sources} == set(CONNECTORS)


async def test_discovery_lists_providers_then_one_providers_streams(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, None, brokered=(ASANA,))
    with ws(state.workspace_id):
        result = await sync_source(ctx, SyncSourceInput())
        detail = await sync_source(ctx, SyncSourceInput(provider=ASANA))
    providers = _payload(result)["providers"]
    assert isinstance(providers, list)
    assert [row["provider"] for row in providers] == sorted(CONNECTORS)
    asana = next(row for row in providers if row["provider"] == ASANA)
    assert asana == {"provider": ASANA, "auth": "broker", "requires_base_url": False}
    greenhouse = next(row for row in providers if row["provider"] == GREENHOUSE)
    assert greenhouse["auth"] == "direct"
    assert _payload(detail)["provider"] == {
        **asana,
        "streams": [stream.name for stream in CONNECTORS[ASANA]().streams()],
        "canonical_streams": [
            stream.name for stream in CONNECTORS[ASANA]().streams() if stream.canonical
        ],
    }


async def test_owner_registers_exact_streams_idempotently_through_one_grant(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    args = SyncSourceInput(provider=ASANA, streams=("workspaces", "projects", "workspaces"))
    with ws(state.workspace_id):
        result = await sync_source(ctx, args)
        repeated = await sync_source(ctx, args)
    assert result.is_error is False and repeated.is_error is False
    assert _payload(result)["sources"] == _payload(repeated)["sources"]
    rows = await _rows(state, ASANA)
    assert len(rows) == 2
    assert {row["config"]["stream"] for row in rows} == {"workspaces", "projects"}
    assert {row["config"]["account"] for row in rows} == {"acct-one"}


async def test_missing_or_ambiguous_broker_account_returns_a_repair(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    ctx = _context(state, grants, brokered=(ASANA,))
    with ws(state.workspace_id):
        missing = await sync_source(ctx, SyncSourceInput(provider=ASANA, streams=("workspaces",)))
    assert _payload(missing)["error"] == {
        "code": "account_not_connected",
        "message": "Connect a 'asana' account before registering its sources.",
        "action": {"tool": "connect_account", "arguments": {"provider": ASANA}},
    }
    await _grant(state, grants, ASANA, "acct-b")
    await _grant(state, grants, ASANA, "acct-a")
    with ws(state.workspace_id):
        ambiguous = await sync_source(ctx, SyncSourceInput(provider=ASANA, streams=("workspaces",)))
        accepted = await sync_source(
            ctx,
            SyncSourceInput(provider=ASANA, streams=("workspaces",), account_id="acct-b"),
        )
    assert _payload(ambiguous)["error"]["accounts"] == ["acct-a", "acct-b"]
    assert accepted.is_error is False
    [row] = await _rows(state, ASANA)
    assert row["config"]["account"] == "acct-b"


async def test_non_owner_cannot_register_shared_sources(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    with ws(state.workspace_id):
        result = await sync_source(
            _context(state, grants, brokered=(ASANA,), speaker_id=state.member_id),
            SyncSourceInput(provider=ASANA, streams=("workspaces",)),
        )
    assert _payload(result)["error"]["code"] == "owner_required"
    assert await _rows(state, ASANA) == []


async def test_unsupported_provider_and_stream_do_not_register(db: None) -> None:
    state = await _workspace()
    with ws(state.workspace_id):
        provider = await sync_source(
            _context(state, None),
            SyncSourceInput(provider="nonesuch", streams=("things",)),
        )
        stream = await sync_source(
            _context(state, GrantStore(), brokered=(ASANA,)),
            SyncSourceInput(provider=ASANA, streams=("nonesuch",)),
        )
    assert _payload(provider)["error"]["code"] == "unsupported_provider"
    assert _payload(stream)["error"]["code"] == "unsupported_stream"
    assert await _rows(state, ASANA) == []


async def test_direct_provider_guides_credentials_then_registers(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREENHOUSE", raising=False)
    state = await _workspace()
    ctx = _context(state, None)
    args = SyncSourceInput(provider=GREENHOUSE, streams=("jobs",))
    with ws(state.workspace_id):
        missing = await sync_source(ctx, args)
        monkeypatch.setenv("GREENHOUSE", "secret")
        registered = await sync_source(ctx, args)
    error = _payload(missing)["error"]
    assert error["code"] == "credential_required"
    assert error["action"]["tool"] == "request_credentials"
    assert registered.is_error is False
    [row] = await _rows(state, GREENHOUSE)
    assert row["config"] == {"account": "default", "stream": "jobs", "base_url": None}


@pytest.mark.parametrize(
    ("provider", "base_url", "normalized"),
    [
        ("activecampaign", "https://acme.api-us1.com/", "https://acme.api-us1.com"),
        (
            "bamboohr",
            "https://api.bamboohr.com/api/gateway.php/acme/",
            "https://api.bamboohr.com/api/gateway.php/acme",
        ),
        ("chargebee", "https://acme.chargebee.com/api/v2/", "https://acme.chargebee.com/api/v2"),
        ("freshdesk", "https://acme.freshdesk.com/", "https://acme.freshdesk.com"),
        ("mailchimp", "https://us21.api.mailchimp.com/", "https://us21.api.mailchimp.com"),
        ("recruitee", "https://api.recruitee.com/c/acme/", "https://api.recruitee.com/c/acme"),
        ("salesforce", "https://acme.my.salesforce.com/", "https://acme.my.salesforce.com"),
        ("zendesk", "https://acme.zendesk.com/", "https://acme.zendesk.com"),
    ],
)
def test_tenant_urls_are_validated_by_provider(
    provider: str, base_url: str, normalized: str
) -> None:
    assert _validated_base_url(provider, base_url) == normalized


@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("activecampaign", "https://acme.freshdesk.com"),
        ("bamboohr", "https://api.bamboohr.com.evil.test/api/gateway.php/acme"),
        ("chargebee", "https://acme.chargebee.com/admin"),
        ("freshdesk", "https://localhost"),
        ("mailchimp", "http://us21.api.mailchimp.com"),
        ("recruitee", "https://api.recruitee.com/c/acme?redirect=evil"),
        ("salesforce", "https://evil.test"),
        ("zendesk", "https://attacker@acme.zendesk.com"),
    ],
)
def test_tenant_urls_reject_cross_provider_and_unsafe_origins(provider: str, base_url: str) -> None:
    with pytest.raises(ValueError):
        _validated_base_url(provider, base_url)


async def test_invalid_dynamic_or_fixed_base_url_registers_nothing(db: None) -> None:
    state = await _workspace()
    with ws(state.workspace_id):
        dynamic = await sync_source(
            _context(state, None),
            SyncSourceInput(
                provider=FRESHDESK,
                streams=("tickets",),
                base_url="https://127.0.0.1",
            ),
        )
        fixed = await sync_source(
            _context(state, GrantStore(), brokered=(ASANA,)),
            SyncSourceInput(
                provider=ASANA,
                streams=("workspaces",),
                base_url="https://evil.test",
            ),
        )
    assert _payload(dynamic)["error"]["code"] == "invalid_base_url"
    assert _payload(fixed)["error"]["code"] == "invalid_base_url"
    assert await _rows(state, FRESHDESK) == []
    assert await _rows(state, ASANA) == []
