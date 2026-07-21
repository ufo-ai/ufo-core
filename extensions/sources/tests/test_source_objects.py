"""The `source` object kind end to end: private-by-default registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` rows and the verbs' own results: derived names,
error-driven discovery (unknown provider/stream refusals listing the valid sets), the tenant-URL
safety rules, broker- and direct-auth resolution, delete marking the row removed and tombstoning
its pages, and revival on an identical re-registration. A source is private to its registering
member by default; sharing it and deleting it are gated to the registrar or the workspace owner."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import (
    DIRECT_ACCOUNT,
    SOURCE_KIND,
    _binding_name,
    _subscribers_map,
    _validated_base_url,
    on_page_change,
)

from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.grants import GrantStore
from ufo.objects import UnknownObject
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.manifest import HookContext, PageChangeBatch
from ufo.sdk.objects import OwnerRequired, VerbNotSupported
from ufo.sdk.sources import ConnectorSourceConfig, PageChange
from ufo.sdk.tools import ToolContext
from ufo.sources.sync import SyncDriver
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.surfaces.admission import Admission, AdmissionInvoker
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
    no_speaker: bool = False,
) -> ToolContext:
    ext = context_for(NAME, DECLARED_PROVIDERS)
    speaker = None if no_speaker else (speaker_id or state.owner_id)
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
        speaker_member_id=speaker,
        audience_member_id=speaker,
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
        shared=False,
    )


def _manifest_text(
    provider: str,
    streams: tuple[str, ...],
    name: str,
    account_id: str = "",
    base_url: str = "",
    shared: bool = False,
    subscribers: tuple[str, ...] = (),
) -> str:
    spec: dict[str, object] = {"provider": provider, "streams": list(streams)}
    if account_id:
        spec["account_id"] = account_id
    if base_url:
        spec["base_url"] = base_url
    if shared:
        spec["shared"] = shared
    if subscribers:
        spec["subscribers"] = list(subscribers)
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
    assert SOURCE_KIND in {kind.name for kind in declared.objects}
    [hook] = declared.hooks
    assert hook.event == "page_change"
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
        "shared": False,
        "subscribers": [],
    }
    assert set(fetched["status"]["streams"]) == {"projects", "workspaces"}
    assert fetched["status"]["streams"]["projects"]["consecutive_errors"] == 0
    assert fetched["status"]["shared"] is False
    assert fetched["status"]["owner_member_id"] == str(state.owner_id)
    assert repeated == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
    assert {row["subject"] for row in rows} == {f"member:{state.owner_id}"}
    assert {row["owner_member_id"] for row in rows} == {state.owner_id}


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


async def _stranger(state: _Workspace) -> UUID:
    stranger_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=stranger_id,
                workspace_id=state.workspace_id,
                email=f"{stranger_id.hex}@x.test",
                created_at=datetime(2026, 7, 10, tzinfo=UTC),
                updated_at=datetime(2026, 7, 10, tzinfo=UTC),
            )
        )
    return stranger_id


async def test_a_member_registers_a_private_source_by_default(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    ctx = _context(state, None, speaker_id=state.member_id)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id):
        applied = await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
        assert applied == {"kind": SOURCE_KIND, "name": name, "result": "created"}
        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    ctx, get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name})
                )
            )
            .content[0]
            .text
        )
    rows = await _rows(state, GREENHOUSE)
    assert {row["subject"] for row in rows} == {member_subject(state.member_id)}
    assert {row["owner_member_id"] for row in rows} == {state.member_id}
    assert fetched["spec"]["shared"] is False


async def test_the_model_registers_a_shared_source_on_request(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    ctx = _context(state, None, speaker_id=state.member_id)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id):
        await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True))
    rows = await _rows(state, GREENHOUSE)
    assert {row["subject"] for row in rows} == {SHARED_SUBJECT}
    assert {row["owner_member_id"] for row in rows} == {state.member_id}


async def test_the_registrar_shares_their_source_and_a_stranger_cannot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    gh_name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    fd_name = _binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), gh_name))
        await _apply(
            member_ctx,
            _manifest_text(FRESHDESK, ("tickets",), fd_name, base_url="https://acme.freshdesk.com"),
        )
        [gh_row] = await _rows(state, GREENHOUSE)
        old = datetime(2026, 7, 1, tzinfo=UTC)
        page_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=gh_row["id"],
                    digest="sha256:x",
                    body_ref="pages/x",
                    subject=member_subject(state.member_id),
                    tombstone=False,
                    created_at=old,
                    updated_at=old,
                )
            )

        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), gh_name, shared=True))
        await _apply(
            owner_ctx,
            _manifest_text(
                FRESHDESK,
                ("tickets",),
                fd_name,
                base_url="https://acme.freshdesk.com",
                shared=True,
            ),
        )

        with pytest.raises(
            OwnerRequired, match="registering member or the workspace owner"
        ) as raised:
            await _apply(stranger_ctx, _manifest_text(GREENHOUSE, ("jobs",), gh_name))
        assert str(state.member_id) not in str(raised.value)
    assert {row["subject"] for row in await _rows(state, GREENHOUSE)} == {SHARED_SUBJECT}
    assert {row["subject"] for row in await _rows(state, FRESHDESK)} == {SHARED_SUBJECT}
    async with workspace_tx() as connection:
        page = (
            (await connection.execute(sa.select(tables.page).where(tables.page.c.id == page_id)))
            .mappings()
            .one()
        )
    assert page["subject"] == SHARED_SUBJECT


async def test_stranger_applying_a_private_source_name_is_not_found(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stranger cannot see another member's private source, so applying its deterministic name is
    refused as not-found — the same message whether shared=True or shared=False, never a distinct
    owner gate that would confirm the private binding exists or name its owner."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
        messages: list[str] = []
        for shared in (True, False):
            args = apply_tool.input_model.model_validate(
                {"manifest": _manifest_text(GREENHOUSE, ("jobs",), name, shared=shared)}
            )
            with pytest.raises(UnknownObject) as raised:
                await apply_tool.handler(stranger_ctx, args)
            assert "registering member or the workspace owner" not in str(raised.value)
            assert str(state.member_id) not in str(raised.value)
            messages.append(str(raised.value))
        assert messages[0] == messages[1]
    assert {row["subject"] for row in await _rows(state, GREENHOUSE)} == {
        member_subject(state.member_id)
    }


async def test_unsharing_is_delete_and_recreate(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    ctx = _context(state, None, speaker_id=state.member_id)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True))
        args = apply_tool.input_model.model_validate(
            {"manifest": _manifest_text(GREENHOUSE, ("jobs",), name, shared=False)}
        )
        with pytest.raises(VerbNotSupported, match="delete"):
            await apply_tool.handler(ctx, args)
    assert {row["subject"] for row in await _rows(state, GREENHOUSE)} == {SHARED_SUBJECT}


async def test_delete_is_registrar_or_owner(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    delete_tool = _TOOLS["object_delete"]
    member_name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    boot_name = _binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        with pytest.raises(UnknownObject):
            await delete_tool.handler(
                stranger_ctx,
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
            )
        await delete_tool.handler(
            member_ctx,
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
        )
    [row] = await _rows(state, GREENHOUSE)
    assert row["removed_at"] is not None

    ext = context_for(NAME, DECLARED_PROVIDERS)
    with ws(state.workspace_id):
        await ext.register_source(
            FRESHDESK,
            ConnectorSourceConfig(
                account=DIRECT_ACCOUNT, stream="tickets", base_url="https://acme.freshdesk.com"
            ),
            subject=SHARED_SUBJECT,
            owner_member_id=None,
        )
        with pytest.raises(OwnerRequired, match="registering member or the workspace owner"):
            await delete_tool.handler(
                member_ctx,
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": boot_name}),
            )
        await delete_tool.handler(
            owner_ctx,
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": boot_name}),
        )
    [row] = await _rows(state, FRESHDESK)
    assert row["removed_at"] is not None


async def test_read_verbs_hide_other_members_private_sources(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    private_name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    shared_name = _binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    list_tool = _TOOLS["object_list"]
    get_tool = _TOOLS["object_get"]
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), private_name))
        await _apply(
            member_ctx,
            _manifest_text(
                FRESHDESK,
                ("tickets",),
                shared_name,
                base_url="https://acme.freshdesk.com",
                shared=True,
            ),
        )

        listing = json.loads(
            (
                await list_tool.handler(
                    stranger_ctx, list_tool.input_model.model_validate({"kind": SOURCE_KIND})
                )
            )
            .content[0]
            .text
        )
        assert [row["name"] for row in listing["objects"]] == [shared_name]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                stranger_ctx,
                get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": private_name}),
            )

        for ctx in (member_ctx, owner_ctx):
            listing = json.loads(
                (
                    await list_tool.handler(
                        ctx, list_tool.input_model.model_validate({"kind": SOURCE_KIND})
                    )
                )
                .content[0]
                .text
            )
            assert {row["name"] for row in listing["objects"]} == {private_name, shared_name}
            fetched = yaml.safe_load(
                (
                    await get_tool.handler(
                        ctx,
                        get_tool.input_model.model_validate(
                            {"kind": SOURCE_KIND, "name": private_name}
                        ),
                    )
                )
                .content[0]
                .text
            )
            assert fetched["spec"]["provider"] == GREENHOUSE
            assert set(fetched["status"]["streams"]) == {"jobs"}


async def test_registration_requires_a_speaking_member(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    ctx = _context(state, None, no_speaker=True)
    name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {"manifest": _manifest_text(GREENHOUSE, ("jobs",), name)}
    )
    with ws(state.workspace_id), pytest.raises(ValueError, match="speaking member"):
        await tool.handler(ctx, args)


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


async def test_source_delete_needs_a_live_speaker(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a source is destructive: a speakerless scheduled/subagent turn acting on behalf of
    the registrar cannot delete it, even though that turn may sync the source."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    member_ctx = _context(state, None, speaker_id=state.member_id)
    speakerless = replace(
        _context(state, None, no_speaker=True), on_behalf_of_member_id=state.member_id
    )
    delete_tool = _TOOLS["object_delete"]
    member_name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(
                speakerless,
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
            )
    [row] = await _rows(state, GREENHOUSE)
    assert row["removed_at"] is None


async def test_owner_reapplying_a_members_private_source_is_a_noop(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace owner re-applying another member's private source with the identical spec is a
    no-op: it neither errors nor re-attributes the source to the owner (an existing binding never
    falls through to the create path, which would stamp the acting caller as owner)."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    member_name = _binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        await _apply(owner_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        rows = await _rows(state, GREENHOUSE)
    assert {row["subject"] for row in rows} == {member_subject(state.member_id)}
    assert {row["owner_member_id"] for row in rows} == {state.member_id}


# --- subscriptions --------------------------------------------------------------------------


@dataclass
class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        return None


def _admitting(workspace_id: UUID) -> AdmissionInvoker:
    admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset({"cli"}))
    return AdmissionInvoker(admission=admission, workspace_id=workspace_id)


async def _register(
    state: _Workspace,
    *,
    subject: str,
    owner: UUID | None,
    account: str = "acct-one",
    stream: str = "tasks",
) -> tuple[str, UUID]:
    """Register one (account, stream) source row directly, with the given disclosure, and return
    its binding name + row id — subscribing is an edit on an existing visible source, so this sets
    one up without the connect/grant dance registration proper needs."""
    with ws(state.workspace_id):
        ext = context_for(NAME, DECLARED_PROVIDERS)
        source_id = await ext.register_source(
            ASANA,
            ConnectorSourceConfig(account=account, stream=stream),
            subject=subject,
            owner_member_id=owner,
        )
    return _binding_name(ASANA, account, None), source_id


async def _stored_subscribers(state: _Workspace, name: str) -> dict[str, str]:
    with ws(state.workspace_id):
        return await _subscribers_map(context_for(NAME, DECLARED_PROVIDERS), name)


async def _turns(conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn).where(tables.turn.c.conversation_id == conversation_id)
                )
            )
            .mappings()
            .all()
        )


def _subscribe_manifest(name: str, subscribers: tuple[str, ...], *, shared: bool) -> str:
    return _manifest_text(
        ASANA, ("tasks",), name, account_id="acct-one", shared=shared, subscribers=subscribers
    )


def _change(
    source_id: UUID, body: str, changed_at: datetime | None = None, subject: str = SHARED_SUBJECT
) -> PageChange:
    now = changed_at or datetime(2026, 7, 20, tzinfo=UTC)
    return PageChange(
        page_id=uuid4(),
        source_id=source_id,
        subject=subject,
        body=body,
        digest=f"sha256:{uuid4().hex}",
        tombstone=False,
        created_at=now,
        changed_at=now,
    )


async def test_subscribe_and_unsubscribe_self_on_a_shared_source(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        subscribed = await _apply(
            _context(state, None), _subscribe_manifest(name, (caller,), shared=True)
        )
        assert subscribed["result"] == "updated"
        assert await _stored_subscribers(state, name) == {caller: state.agent_id.hex}

        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    _context(state, None),
                    get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
                )
            )
            .content[0]
            .text
        )
        assert fetched["spec"]["subscribers"] == [caller]
        assert fetched["status"]["subscriber_id"] == caller
        assert fetched["status"]["subscribed"] is True

        await _apply(_context(state, None), _subscribe_manifest(name, (), shared=True))
        assert await _stored_subscribers(state, name) == {}


async def test_a_non_owner_may_subscribe_to_a_shared_source(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        subscribed = await _apply(
            _context(state, None, speaker_id=state.member_id),
            _subscribe_manifest(name, (caller,), shared=True),
        )
        assert subscribed["result"] == "updated"
        assert await _stored_subscribers(state, name) == {caller: state.agent_id.hex}


async def test_apply_may_only_toggle_the_callers_own_subscription(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    other = uuid4().hex
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        with pytest.raises(ValueError, match="your own conversation"):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {"manifest": _subscribe_manifest(name, (other,), shared=True)}
                ),
            )
        assert await _stored_subscribers(state, name) == {}


async def test_cannot_subscribe_to_another_members_private_source(db: None) -> None:
    """A source private to member M is invisible to a stranger — the base gate hides it, so the
    subscribers-only fast path is never reached and the stranger cannot subscribe to it."""
    state = await _workspace()
    name, _ = await _register(state, subject=member_subject(state.member_id), owner=state.member_id)
    stranger = await _stranger(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id):
        with pytest.raises(UnknownObject):
            await tool.handler(
                _context(state, None, speaker_id=stranger),
                tool.input_model.model_validate(
                    {
                        "manifest": _subscribe_manifest(
                            name, (state.conversation_id.hex,), shared=False
                        )
                    }
                ),
            )
        assert await _stored_subscribers(state, name) == {}


async def test_page_change_alerts_only_subscribed_conversations_idempotently(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        legal = _change(source_id, "# asana tasks: Follow up with legal")
        stray = _change(uuid4(), "# folder: untracked")
        batch = PageChangeBatch(changes=(shipped, legal, stray))
        await on_page_change(HookContext(ext=ext, payload=batch))
        (turn,) = await _turns(state.conversation_id)
        assert name in turn["inbound"]
        assert "2 synced pages" in turn["inbound"]
        assert f"{PAGE_KIND}/{shipped.page_id}" in turn["inbound"]
        assert f"{PAGE_KIND}/{legal.page_id}" in turn["inbound"]

        await on_page_change(HookContext(ext=ext, payload=batch))
        assert len(await _turns(state.conversation_id)) == 1


async def test_multi_stream_binding_alerts_once_per_conversation(db: None) -> None:
    """Two streams of one binding both changing in a batch is one alert, not two — the hook
    aggregates by binding, so distinct per-stream `changed_at` neither split into two turns nor
    collide on one idempotency key and drop a stream."""
    state = await _workspace()
    name, tasks_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    _, projects_id = await _register(
        state, subject=SHARED_SUBJECT, owner=state.owner_id, stream="projects"
    )
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(
            _context(state, None),
            _manifest_text(
                ASANA,
                ("projects", "tasks"),
                name,
                account_id="acct-one",
                shared=True,
                subscribers=(caller,),
            ),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(
                        _change(tasks_id, "# t", changed_at=datetime(2026, 7, 20, 9, tzinfo=UTC)),
                        _change(
                            projects_id, "# p", changed_at=datetime(2026, 7, 20, 10, tzinfo=UTC)
                        ),
                    )
                ),
            )
        )
        (turn,) = await _turns(state.conversation_id)
        assert "2 synced pages" in turn["inbound"]


async def test_alert_never_surfaces_a_member_private_page(db: None) -> None:
    """A subscription to a shared source surfaces only shared changes — a member-private page in
    the same batch is neither referenced nor counted, so its existence never leaks."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shared = _change(source_id, "# asana tasks: Ship it")
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(shared, private)))
        )
        (turn,) = await _turns(state.conversation_id)
        assert "1 synced page" in turn["inbound"]
        assert f"{PAGE_KIND}/{shared.page_id}" in turn["inbound"]
        assert str(private.page_id) not in turn["inbound"]


async def test_alert_skipped_when_only_member_private_changes(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(private,))))
        assert await _turns(state.conversation_id) == []
