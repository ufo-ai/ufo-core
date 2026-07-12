"""The `sync_source` chat act end to end: a granted or keyed provider registers durable source
rows the sync driver will poll — one per canonical stream, or the one named stream — idempotently
on re-ask; the account resolves the way the sync driver will resolve the credential (a brokered
provider through the turn-agent's grant, any other through the fallback backend's credential
slot); and the failure modes are loud at the call, not at the first sync run: an unknown provider
or stream names the valid set, a brokered provider without a grant says connect in chat, an
unbrokered one without a key or fallback says what the operator must set."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.manifest import NAME
from ufo_ext_sources.syncing import SyncSourceInput, sync_source

from ufo.connectors import ConnectorEntry, ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess, context_for
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws


@dataclass(frozen=True)
class _Grant:
    provider: str
    account_id: str


@dataclass(frozen=True)
class _Grants:
    grants: tuple[_Grant, ...]

    async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[_Grant, ...]:
        return self.grants


@dataclass(frozen=True)
class _NeverBroker:
    """A broker whose credential path must never run — `sync_source` resolves a brokered account
    from the turn-agent's grants, not by asking the broker."""

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> None:
        raise AssertionError("sync_source must not ask the broker for a credential")


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the sync_source tests")


def _tool_ctx(
    workspace_id: UUID,
    *,
    slots: frozenset[str] = frozenset(),
    grants: _Grants | None = None,
    brokered: frozenset[str] = frozenset(),
    fallback: bool = True,
) -> ToolContext:
    registry = ConnectorRegistry(
        entries={
            provider: ConnectorEntry(provider=provider, label=provider, broker=_NeverBroker())
            for provider in brokered
        },
        fallback=DirectAuthProxy(credentials=CredentialAccess(declared=slots))
        if fallback
        else None,
    )
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="sync my sources",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        grants=grants,
        connectors=registry,
        ext=context_for(NAME, slots),
    )


async def _rows(workspace_id: UUID, backend: str) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.source).where(
                        tables.source.c.workspace_id == workspace_id,
                        tables.source.c.backend == backend,
                    )
                )
            )
            .mappings()
            .all()
        )


async def test_unbrokered_provider_registers_from_its_slot_idempotently(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "gh-key")
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id, slots=frozenset({"greenhouse"}))
    with ws(workspace_id):
        result = await sync_source(ctx, SyncSourceInput(provider="greenhouse", stream="jobs"))
        again = await sync_source(ctx, SyncSourceInput(provider="greenhouse", stream="jobs"))

    assert result.is_error is False and again.is_error is False
    rows = await _rows(workspace_id, "greenhouse")
    assert len(rows) == 1
    assert rows[0]["config"] == {"account": "default", "stream": "jobs", "base_url": None}


async def test_without_a_stream_the_canonical_streams_register(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR", "lin-key")
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id, slots=frozenset({"linear"}))
    with ws(workspace_id):
        await sync_source(ctx, SyncSourceInput(provider="linear"))

    streams = {row["config"]["stream"] for row in await _rows(workspace_id, "linear")}
    assert streams == {"projects", "issues", "project_milestones", "comments", "users"}


async def test_brokered_provider_resolves_the_granted_account(db: None) -> None:
    workspace_id = await _seed_workspace()
    grants = _Grants(grants=(_Grant(provider="google_meet", account_id="ca_123"),))
    ctx = _tool_ctx(workspace_id, brokered=frozenset({"google_meet"}), grants=grants)
    with ws(workspace_id):
        await sync_source(ctx, SyncSourceInput(provider="google_meet"))

    rows = await _rows(workspace_id, "google_meet")
    assert [row["config"]["account"] for row in rows] == ["ca_123"]


async def test_brokered_provider_ignores_a_byok_key_and_says_connect(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync driver routes a brokered provider to its broker unconditionally, so a key in the
    provider's slot cannot authenticate it — registering on the key would create rows whose every
    sync run fails asking the broker for an account it never granted."""
    monkeypatch.setenv("GOOGLE_MEET", "meet-token")
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(
        workspace_id, slots=frozenset({"google_meet"}), brokered=frozenset({"google_meet"})
    )
    with ws(workspace_id), pytest.raises(ValueError, match="Connect the account in chat"):
        await sync_source(ctx, SyncSourceInput(provider="google_meet"))
    assert await _rows(workspace_id, "google_meet") == []


async def test_unknown_provider_names_the_valid_set(db: None) -> None:
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id)
    with ws(workspace_id), pytest.raises(ValueError, match="google_meet"):
        await sync_source(ctx, SyncSourceInput(provider="nonesuch"))
    assert await _rows(workspace_id, "nonesuch") == []


async def test_unknown_stream_names_the_provider_streams(db: None) -> None:
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id)
    with ws(workspace_id), pytest.raises(ValueError, match="meeting_artifacts"):
        await sync_source(ctx, SyncSourceInput(provider="google_meet", stream="transcripts"))
    assert await _rows(workspace_id, "google_meet") == []


async def test_per_tenant_provider_requires_a_base_url_at_the_call(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider whose connector class carries no host (Freshdesk-style per-tenant) fails this
    call rather than register a row whose every sync run fails; passing base_url registers it."""
    monkeypatch.setenv("FRESHDESK", "fd-key")
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id, slots=frozenset({"freshdesk"}))
    with ws(workspace_id):
        with pytest.raises(ValueError, match="base_url"):
            await sync_source(ctx, SyncSourceInput(provider="freshdesk"))
        assert await _rows(workspace_id, "freshdesk") == []
        await sync_source(
            ctx,
            SyncSourceInput(provider="freshdesk", base_url="https://acme.freshdesk.com"),
        )

    rows = await _rows(workspace_id, "freshdesk")
    assert rows and all(row["config"]["base_url"] == "https://acme.freshdesk.com" for row in rows)


async def test_no_key_and_no_grant_fails_with_what_to_do(db: None) -> None:
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id, slots=frozenset({"greenhouse"}))
    with ws(workspace_id), pytest.raises(ValueError, match="ufoctl credential set greenhouse"):
        await sync_source(ctx, SyncSourceInput(provider="greenhouse"))
    assert await _rows(workspace_id, "greenhouse") == []


async def test_unbrokered_provider_without_a_fallback_backend_fails_loud(db: None) -> None:
    workspace_id = await _seed_workspace()
    ctx = _tool_ctx(workspace_id, slots=frozenset({"greenhouse"}), fallback=False)
    with ws(workspace_id), pytest.raises(ValueError, match="auth_backend"):
        await sync_source(ctx, SyncSourceInput(provider="greenhouse"))
    assert await _rows(workspace_id, "greenhouse") == []
