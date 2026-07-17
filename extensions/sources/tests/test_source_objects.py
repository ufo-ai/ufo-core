"""The `source` object kind end to end: owner-gated registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` rows and the verbs' own results: derived names,
error-driven discovery (unknown provider/stream refusals listing the valid sets), the tenant-URL
safety rules, broker- and direct-auth resolution, delete marking the row removed and tombstoning
its pages, and revival on an identical re-registration."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import (
    DIRECT_ACCOUNT,
    SOURCE_KIND,
    _binding_name,
    _validated_base_url,
)

from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.grants import GrantStore
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.objects import OwnerRequired, VerbNotSupported
from ufo.sdk.tools import ToolContext
from ufo.sources.sync import SyncDriver
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

ASANA = "asana"
ASANA_HOST = "app.asana.com"
GREENHOUSE = "greenhouse"
FRESHDESK = "freshdesk"
DECLARED_PROVIDERS = frozenset(CONNECTORS)


class _UnusedBroker:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> None:
        raise AssertionError("source objects must not resolve provider credentials")


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


_TOOLS: dict[str, ToolDef] = {
    tool.name: tool
    for tool in turn_tools((manifest(),), CredentialStore(fernet=Fernet(Fernet.generate_key())))[0]
}


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


def _manifest_text(
    provider: str, streams: tuple[str, ...], name: str, account_id: str = "", base_url: str = ""
) -> str:
    spec: dict[str, object] = {"provider": provider, "streams": list(streams)}
    if account_id:
        spec["account_id"] = account_id
    if base_url:
        spec["base_url"] = base_url
    return yaml.safe_dump({"kind": SOURCE_KIND, "name": name, "spec": spec})


async def _apply(ctx: ToolContext, manifest_text: str) -> dict[str, object]:
    tool = _TOOLS["object_apply"]
    result = await tool.handler(ctx, tool.input_model.model_validate({"manifest": manifest_text}))
    assert result.is_error is False
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


def test_manifest_declares_the_source_kind() -> None:
    declared = manifest()
    assert declared.tools == ()
    [kind] = declared.objects
    assert kind.name == SOURCE_KIND
    assert {slot.name for slot in declared.credentials} == set(CONNECTORS)
    assert {source.backend for source in declared.sources} == set(CONNECTORS)


async def test_owner_applies_a_binding_and_reads_it_back(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = _binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id):
        applied = await _apply(
            ctx, _manifest_text(ASANA, ("workspaces", "projects", "workspaces"), name)
        )
        assert applied == {"kind": SOURCE_KIND, "name": name, "result": "created"}
        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
                )
            )
            .content[0]
            .text
        )
        repeated = await _apply(ctx, _manifest_text(ASANA, ("projects", "workspaces"), name))
    rows = await _rows(state, ASANA)
    assert len(rows) == 2
    assert {row["config"]["stream"] for row in rows} == {"workspaces", "projects"}
    assert {row["config"]["account"] for row in rows} == {"acct-one"}
    assert (
        state.workspace_id
        in await SyncDriver(blob=None, postgres=False, backends={}).candidate_workspaces()
    )
    assert fetched["spec"] == {
        "provider": ASANA,
        "streams": ["projects", "workspaces"],
        "account_id": "acct-one",
        "base_url": "",
    }
    assert set(fetched["status"]["streams"]) == {"projects", "workspaces"}
    assert fetched["status"]["streams"]["projects"]["consecutive_errors"] == 0
    assert repeated == {"kind": SOURCE_KIND, "name": name, "result": "updated"}


async def test_wrong_name_refusal_hands_back_the_derived_name(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    derived = _binding_name(ASANA, "acct-one", None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {"manifest": _manifest_text(ASANA, ("workspaces",), "my-asana")}
    )
    with ws(state.workspace_id), pytest.raises(ValueError, match=derived):
        await tool.handler(ctx, args)
    assert await _rows(state, ASANA) == []


async def test_unknown_provider_and_stream_refuse_with_the_valid_sets(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        with pytest.raises(ValueError, match=ASANA):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {"manifest": _manifest_text("nonesuch", ("things",), "nonesuch-x")}
                ),
            )
        with pytest.raises(ValueError, match="workspaces"):
            await tool.handler(
                _context(state, grants, brokered=(ASANA,)),
                tool.input_model.model_validate(
                    {
                        "manifest": _manifest_text(
                            ASANA, ("nonesuch",), _binding_name(ASANA, "acct-one", None)
                        )
                    }
                ),
            )
    assert await _rows(state, ASANA) == []


async def test_non_owner_mutations_are_refused(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    name = _binding_name(ASANA, "acct-one", None)
    apply_tool = _TOOLS["object_apply"]
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id):
        with pytest.raises(OwnerRequired):
            await apply_tool.handler(
                _context(state, grants, brokered=(ASANA,), speaker_id=state.member_id),
                apply_tool.input_model.model_validate(
                    {"manifest": _manifest_text(ASANA, ("workspaces",), name)}
                ),
            )
        await _apply(
            _context(state, grants, brokered=(ASANA,)),
            _manifest_text(ASANA, ("workspaces",), name),
        )
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(
                _context(state, grants, brokered=(ASANA,), speaker_id=state.member_id),
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
            )
    assert len(await _rows(state, ASANA)) == 1


async def test_changing_streams_is_refused_as_an_update(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = _binding_name(ASANA, "acct-one", None)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name))
        args = tool.input_model.model_validate(
            {"manifest": _manifest_text(ASANA, ("workspaces", "projects"), name)}
        )
        with pytest.raises(VerbNotSupported, match="delete"):
            await tool.handler(ctx, args)
    rows = await _rows(state, ASANA)
    assert [row["config"]["stream"] for row in rows] == ["workspaces"]


async def test_delete_marks_rows_removed_tombstones_pages_and_revives(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = _binding_name(ASANA, "acct-one", None)
    delete_tool = _TOOLS["object_delete"]
    list_tool = _TOOLS["object_list"]
    with ws(state.workspace_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name))
        [row] = await _rows(state, ASANA)
        page_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=row["id"],
                    digest="sha256:x",
                    body_ref="pages/x",
                    subject="shared",
                    tombstone=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        deleted = json.loads(
            (
                await delete_tool.handler(
                    ctx,
                    delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
                )
            )
            .content[0]
            .text
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["provider"] == ASANA
        listing = json.loads(
            (
                await list_tool.handler(
                    ctx, list_tool.input_model.model_validate({"kind": SOURCE_KIND})
                )
            )
            .content[0]
            .text
        )
        assert listing["objects"] == []
        async with workspace_tx() as connection:
            page = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.tombstone).where(tables.page.c.id == page_id)
                    )
                )
                .mappings()
                .one()
            )
        [row] = await _rows(state, ASANA)
        assert row["removed_at"] is not None
        assert page["tombstone"] is True or page["tombstone"] == 1
    assert (
        state.workspace_id
        not in await SyncDriver(blob=None, postgres=False, backends={}).candidate_workspaces()
    )
    with ws(state.workspace_id):
        revived = await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name))
        assert revived["result"] == "created"
        [row] = await _rows(state, ASANA)
        assert row["removed_at"] is None
        assert row["consecutive_errors"] == 0


async def test_direct_provider_requires_its_credential_then_registers(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREENHOUSE", raising=False)
    state = await _workspace()
    ctx = _context(state, None)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {"manifest": _manifest_text(GREENHOUSE, ("jobs",), name)}
    )
    with ws(state.workspace_id):
        with pytest.raises(ValueError, match="request_credentials"):
            await tool.handler(ctx, args)
        monkeypatch.setenv("GREENHOUSE", "secret")
        registered = await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
    assert registered["result"] == "created"
    [row] = await _rows(state, GREENHOUSE)
    assert row["config"] == {"account": "default", "stream": "jobs", "base_url": None}


async def test_missing_or_ambiguous_broker_account_refuses_with_repair(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    ctx = _context(state, grants, brokered=(ASANA,))
    tool = _TOOLS["object_apply"]
    unconnected = tool.input_model.model_validate(
        {"manifest": _manifest_text(ASANA, ("workspaces",), "asana-x")}
    )
    with ws(state.workspace_id):
        with pytest.raises(ValueError, match="connect_account"):
            await tool.handler(ctx, unconnected)
        await _grant(state, grants, ASANA, "acct-b")
        await _grant(state, grants, ASANA, "acct-a")
        with pytest.raises(ValueError, match="acct-a"):
            await tool.handler(ctx, unconnected)
        accepted = await _apply(
            ctx,
            _manifest_text(
                ASANA,
                ("workspaces",),
                _binding_name(ASANA, "acct-b", None),
                account_id="acct-b",
            ),
        )
    assert accepted["result"] == "created"
    [row] = await _rows(state, ASANA)
    assert row["config"]["account"] == "acct-b"


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


async def test_invalid_base_url_registers_nothing(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        with pytest.raises(ValueError, match="freshdesk"):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {
                        "manifest": _manifest_text(
                            FRESHDESK, ("tickets",), "freshdesk-x", base_url="https://127.0.0.1"
                        )
                    }
                ),
            )
        with pytest.raises(ValueError, match="fixed API host"):
            await tool.handler(
                _context(state, grants, brokered=(ASANA,)),
                tool.input_model.model_validate(
                    {
                        "manifest": _manifest_text(
                            ASANA, ("workspaces",), "asana-x", base_url="https://evil.test"
                        )
                    }
                ),
            )
    assert await _rows(state, FRESHDESK) == []
    assert await _rows(state, ASANA) == []
