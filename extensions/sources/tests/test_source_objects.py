"""The `source` object kind end to end: private-by-default registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` rows and the verbs' own results: derived names,
error-driven discovery (unknown provider/stream refusals listing the valid sets), the tenant-URL
safety rules, broker- and direct-auth resolution, delete marking the row removed and tombstoning
its pages, and revival on an identical re-registration. A source is private to its registering
member by default; sharing it and deleting it are gated to the registrar or a workspace admin."""

import json
from collections import Counter
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
    CHANGE_LOG_DIR,
    CONNECTION_OBJECT_KIND,
    SOURCE_KIND,
    SourceObjects,
    SourceSpec,
    _subscribers_map,
    _validated_base_url,
    on_page_change,
)

from ufo.agent_scope import agent
from ufo.credential_kind import CREDENTIAL_KIND
from ufo.credentials import CredentialStore, credential_object_name, named_slots
from ufo.db import workspace_tx
from ufo.ext.context import JsonValue, context_for
from ufo.ext.loader import turn_tools
from ufo.ext.manifest import declared_slots
from ufo.grants import GrantStore, account_object_name
from ufo.objects import UnknownObject
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.authproxy import DIRECT_ACCOUNT
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.manifest import HookContext, PageChangeBatch
from ufo.sdk.objects import AdminRequired, VerbNotSupported
from ufo.sdk.sources import ConnectorSourceConfig, PageChange, binding_name
from ufo.sdk.tools import ToolContext
from ufo.sources.sync import SyncDriver
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

TOOL_NARRATION = "setting up the connection"

ASANA = "asana"
ASANA_HOST = "app.asana.com"
GREENHOUSE = "greenhouse"
GOOGLEDRIVE = "googledrive"
FRESHDESK = "freshdesk"
DECLARED_PROVIDERS = frozenset(CONNECTORS)
NAMESPACE_BROKERED = frozenset({ASANA, GOOGLEDRIVE})


class _UnusedBroker:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> None:
        raise AssertionError("source objects must not resolve provider credentials")


async def _broker_down(self: object, provider: str) -> bool:
    raise RuntimeError("COMPOSIO_API_KEY is required to broker a connector's OAuth")


class _OpenNamespace:
    """Stands in for a broker's open namespace: it claims the slugs its broker's live catalog serves
    and refuses the rest, so a provider it does not claim is resolvable only through its workspace
    credential. Account resolution never reaches its broker (it refuses before, asking the member to
    connect), so no method beyond `claims` is called."""

    transfer_hosts: tuple[str, ...] = ()

    async def claims(self, provider: str) -> bool:
        return provider in NAMESPACE_BROKERED

    def entry(self, provider: str) -> None:
        raise AssertionError("account resolution must not reach the namespace broker")


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
                    "is_admin": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "is_admin": False,
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
                is_main=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
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
    for tool in turn_tools(
        (manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )[0]
}


def _context(
    state: _Workspace,
    grants: GrantStore | None,
    *,
    brokered: tuple[str, ...] = (),
    speaker_id: UUID | None = None,
    direct_fallback: bool = True,
    no_speaker: bool = False,
    open_namespace: bool = False,
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
        audience=conversation_audience(speaker),
        artifact_token_secret="",
        grants=grants,
        connectors=ConnectorRegistry(
            entries={
                provider: ConnectorEntry(provider=provider, label=provider, broker=_UnusedBroker())
                for provider in brokered
            },
            resolver=_OpenNamespace() if open_namespace else None,
            fallback=DirectAuthProxy(credentials=ext.credentials) if direct_fallback else None,
        ),
        ext=ext,
    )


async def _grant(state: _Workspace, store: GrantStore, provider: str, account: str) -> None:
    with ws(state.workspace_id), agent(state.agent_id):
        await store.record(
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
    resync: bool = False,
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
    if resync:
        spec["resync"] = resync
    return yaml.safe_dump({"kind": SOURCE_KIND, "name": name, "spec": spec})


async def _apply(ctx: ToolContext, manifest_text: str) -> dict[str, object]:
    tool = _TOOLS["object_apply"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "manifest": manifest_text}
        ),
    )
    assert result.is_error is False
    return json.loads(result.content[0].text)


async def _get(ctx: ToolContext, name: str) -> dict[str, object]:
    tool = _TOOLS["object_get"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
        ),
    )
    assert result.is_error is False
    return yaml.safe_load(result.content[0].text)


async def _rows(state: _Workspace, backend: str) -> list[sa.RowMapping]:
    with ws(state.workspace_id), agent(state.agent_id):
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
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(
            ctx, _manifest_text(ASANA, ("workspaces", "projects", "workspaces"), name)
        )
        assert applied == {"kind": SOURCE_KIND, "name": name, "result": "created"}
        async with workspace_tx() as connection:
            for row in (
                (await connection.execute(sa.select(tables.source.c.id, tables.source.c.config)))
                .mappings()
                .all()
            ):
                stamp = datetime(
                    2026, 7, 3 if row["config"]["stream"] == "projects" else 5, tzinfo=UTC
                )
                await connection.execute(
                    sa.update(tables.source)
                    .where(tables.source.c.id == row["id"])
                    .values(created_at=stamp, updated_at=stamp)
                )
        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
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
        "resync": False,
    }
    assert set(fetched["status"]["streams"]) == {"projects", "workspaces"}
    assert datetime.fromisoformat(fetched["created_at"]).replace(tzinfo=UTC) == datetime(
        2026, 7, 3, tzinfo=UTC
    )
    assert datetime.fromisoformat(fetched["updated_at"]).replace(tzinfo=UTC) == datetime(
        2026, 7, 5, tzinfo=UTC
    )
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
    derived = binding_name(ASANA, "acct-one", None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(ASANA, ("workspaces",), "my-asana"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id), pytest.raises(ValueError, match=derived):
        await tool.handler(ctx, args)
    assert await _rows(state, ASANA) == []


async def test_unknown_provider_and_stream_refuse_with_the_valid_sets(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match=ASANA):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _manifest_text("nonesuch", ("things",), "nonesuch-x"),
                    }
                ),
            )
        with pytest.raises(ValueError, match="workspaces"):
            await tool.handler(
                _context(state, grants, brokered=(ASANA,)),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _manifest_text(
                            ASANA, ("nonesuch",), binding_name(ASANA, "acct-one", None)
                        ),
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
        assert applied == {"kind": SOURCE_KIND, "name": name, "result": "created"}
        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True))
    rows = await _rows(state, GREENHOUSE)
    assert {row["subject"] for row in rows} == {SHARED_SUBJECT}
    assert {row["owner_member_id"] for row in rows} == {state.member_id}


async def test_only_the_registrar_may_widen_a_private_source(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    gh_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    fd_name = binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    with ws(state.workspace_id), agent(state.agent_id):
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
        with pytest.raises(AdminRequired, match="only the registering member may change"):
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

        with pytest.raises(AdminRequired, match="only the registering member may change") as raised:
            await _apply(stranger_ctx, _manifest_text(GREENHOUSE, ("jobs",), gh_name))
        assert str(state.member_id) not in str(raised.value)
    assert {row["subject"] for row in await _rows(state, GREENHOUSE)} == {SHARED_SUBJECT}
    assert {row["subject"] for row in await _rows(state, FRESHDESK)} == {
        member_subject(state.member_id)
    }
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
        messages: list[str] = []
        for shared in (True, False):
            args = apply_tool.input_model.model_validate(
                {
                    "user_description": TOOL_NARRATION,
                    "manifest": _manifest_text(GREENHOUSE, ("jobs",), name, shared=shared),
                }
            )
            with pytest.raises(UnknownObject) as raised:
                await apply_tool.handler(stranger_ctx, args)
            assert "only the registering member may change" not in str(raised.value)
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True))
        args = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _manifest_text(GREENHOUSE, ("jobs",), name, shared=False),
            }
        )
        with pytest.raises(VerbNotSupported, match="delete"):
            await apply_tool.handler(ctx, args)
    assert {row["subject"] for row in await _rows(state, GREENHOUSE)} == {SHARED_SUBJECT}


async def test_delete_is_registrar_or_admin(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    delete_tool = _TOOLS["object_delete"]
    member_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    boot_name = binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        with pytest.raises(UnknownObject):
            await delete_tool.handler(
                stranger_ctx,
                delete_tool.input_model.model_validate(
                    {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": member_name}
                ),
            )
        await delete_tool.handler(
            member_ctx,
            delete_tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": member_name}
            ),
        )
    [row] = await _rows(state, GREENHOUSE)
    assert row["removed_at"] is not None

    ext = context_for(NAME, DECLARED_PROVIDERS)
    with ws(state.workspace_id), agent(state.agent_id):
        await ext.register_source(
            FRESHDESK,
            ConnectorSourceConfig(
                account=DIRECT_ACCOUNT, stream="tickets", base_url="https://acme.freshdesk.com"
            ),
            subject=SHARED_SUBJECT,
            owner_member_id=None,
        )
        with pytest.raises(AdminRequired, match="registering member or a workspace admin"):
            await delete_tool.handler(
                member_ctx,
                delete_tool.input_model.model_validate(
                    {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": boot_name}
                ),
            )
        await delete_tool.handler(
            owner_ctx,
            delete_tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": boot_name}
            ),
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
    private_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    shared_name = binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    list_tool = _TOOLS["object_list"]
    get_tool = _TOOLS["object_get"]
    with ws(state.workspace_id), agent(state.agent_id):
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
                    stranger_ctx,
                    list_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND}
                    ),
                )
            )
            .content[0]
            .text
        )
        assert [row["name"] for row in listing["objects"]] == [shared_name]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                stranger_ctx,
                get_tool.input_model.model_validate(
                    {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": private_name}
                ),
            )

        for ctx in (member_ctx, owner_ctx):
            listing = json.loads(
                (
                    await list_tool.handler(
                        ctx,
                        list_tool.input_model.model_validate(
                            {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND}
                        ),
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
                            {
                                "user_description": TOOL_NARRATION,
                                "kind": SOURCE_KIND,
                                "name": private_name,
                            }
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(GREENHOUSE, ("jobs",), name),
        }
    )
    with (
        ws(state.workspace_id),
        agent(state.agent_id),
        pytest.raises(ValueError, match="speaking member"),
    ):
        await tool.handler(ctx, args)


async def test_changing_streams_is_refused_as_an_update(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name))
        args = tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _manifest_text(ASANA, ("workspaces", "projects"), name),
            }
        )
        with pytest.raises(VerbNotSupported, match="delete"):
            await tool.handler(ctx, args)
    rows = await _rows(state, ASANA)
    assert [row["config"]["stream"] for row in rows] == ["workspaces"]


async def test_resync_pulls_the_bindings_next_sync_to_now(db: None) -> None:
    """A resync apply — the binding's current spec with `resync` set — schedules every stream
    row now, changes nothing else, and always reads back false; a resync that also edits the
    binding is refused whole."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces", "projects"), name))
        future = datetime(2027, 1, 1, tzinfo=UTC)
        async with workspace_tx() as connection:
            await connection.execute(sa.update(tables.source).values(next_sync_at=future))
        before = datetime.now(UTC)
        resynced = await _apply(
            ctx,
            _manifest_text(
                ASANA, ("workspaces", "projects"), name, account_id="acct-one", resync=True
            ),
        )
        assert resynced == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
                )
            )
            .content[0]
            .text
        )
        assert fetched["spec"]["resync"] is False
        with pytest.raises(VerbNotSupported, match="a resync changes nothing else"):
            await _apply(
                ctx,
                _manifest_text(ASANA, ("workspaces",), name, account_id="acct-one", resync=True),
            )
    rows = await _rows(state, ASANA)
    assert len(rows) == 2
    for row in rows:
        scheduled = row["next_sync_at"]
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        assert before - timedelta(seconds=5) <= scheduled <= datetime.now(UTC)


async def test_resync_is_registrar_or_admin(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The resync gate is the delete gate's population, across all three members it
    distinguishes: the registering member may resync their own binding even without admin, a
    member who merely sees the shared source is refused — a resync drives connector traffic on the
    registrar's credential, so seeing it is not enough to spend it — and a workspace admin who
    registered nothing may resync it."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    stranger_ctx = _context(state, None, speaker_id=stranger_id)
    owner_ctx = _context(state, None)
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True))
        registrar = await _apply(
            member_ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True, resync=True)
        )
        assert registrar == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
        with pytest.raises(AdminRequired, match="may resync a source"):
            await _apply(
                stranger_ctx,
                _manifest_text(GREENHOUSE, ("jobs",), name, shared=True, resync=True),
            )
        resynced = await _apply(
            owner_ctx, _manifest_text(GREENHOUSE, ("jobs",), name, shared=True, resync=True)
        )
        assert resynced == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
        carried = await _apply(
            owner_ctx,
            _manifest_text(
                GREENHOUSE,
                ("jobs",),
                name,
                shared=True,
                resync=True,
                subscribers=(uuid4().hex,),
            ),
        )
        assert carried == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
        stored = yaml.safe_load(
            (
                await _TOOLS["object_get"].handler(
                    owner_ctx,
                    _TOOLS["object_get"].input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
                )
            )
            .content[0]
            .text
        )
        assert stored["spec"].get("subscribers", []) == []  # a resync ignores subscribers


async def test_delete_marks_rows_removed_tombstones_pages_and_revives(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    delete_tool = _TOOLS["object_delete"]
    list_tool = _TOOLS["object_list"]
    with ws(state.workspace_id), agent(state.agent_id):
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
                    delete_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
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
                    ctx,
                    list_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND}
                    ),
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
    with ws(state.workspace_id), agent(state.agent_id):
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
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(GREENHOUSE, ("jobs",), name),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="request_credentials"):
            await tool.handler(ctx, args)
        monkeypatch.setenv("GREENHOUSE", "secret")
        registered = await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
    assert registered["result"] == "created"
    [row] = await _rows(state, GREENHOUSE)
    assert row["config"] == {"account": "default", "stream": "jobs", "base_url": None}


async def test_a_private_brokered_binding_links_to_the_connection_it_uses(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name))
        fetched = await _get(ctx, name)
    assert fetched["links"] == [
        {
            "relation": "access_to",
            "target": {
                "kind": CONNECTION_OBJECT_KIND,
                "name": account_object_name(ASANA, "acct-one"),
            },
        },
    ]


async def test_a_shared_brokered_binding_names_no_connection(db: None) -> None:
    """A shared source is workspace-readable while its connection stays owner-or-admin, so naming
    the connection would point at narrower visibility — spec.md demands equal-or-wider. `_status`
    withholds `owner_member_id` on a shared row for the same reason."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name, shared=True))
        fetched = await _get(ctx, name)
    assert fetched["links"] == []
    assert "owner_member_id" not in fetched["status"]


async def test_a_direct_binding_links_to_its_workspace_credential_slot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    ctx = _context(state, None)
    name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GREENHOUSE, ("jobs",), name))
        fetched = await _get(ctx, name)
    assert fetched["links"] == [
        {
            "relation": "access_to",
            "target": {"kind": CREDENTIAL_KIND, "name": credential_object_name(GREENHOUSE)},
        },
    ]
    assert {credential_object_name(provider) for provider in CONNECTORS} == set(
        named_slots(declared_slots((manifest(),)))
    )


async def test_missing_or_ambiguous_broker_account_refuses_with_repair(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    ctx = _context(state, grants, brokered=(ASANA,))
    tool = _TOOLS["object_apply"]
    unconnected = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(ASANA, ("workspaces",), "asana-x"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
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
                binding_name(ASANA, "acct-b", None),
                account_id="acct-b",
            ),
        )
    assert accepted["result"] == "created"
    [row] = await _rows(state, ASANA)
    assert row["config"]["account"] == "acct-b"


async def test_open_namespace_provider_without_an_account_asks_to_connect(db: None) -> None:
    """A provider the open namespace brokers — no explicit entry — with no connected account and no
    direct BYOK credential guides the member to connect an account, never a dead direct backend."""
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), direct_fallback=False, open_namespace=True)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(ASANA, ("workspaces",), "asana-open"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="connect_account with provider='asana'"):
            await tool.handler(ctx, args)


@pytest.mark.parametrize(
    ("keyed", "account_id"),
    [(False, None), (False, "acct-one"), (True, "acct-one")],
)
async def test_a_brokered_provider_without_an_account_asks_to_connect(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    keyed: bool,
    account_id: str | None,
) -> None:
    if keyed:
        monkeypatch.setenv(GOOGLEDRIVE.upper(), "secret")
    else:
        monkeypatch.delenv(GOOGLEDRIVE.upper(), raising=False)
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), open_namespace=True)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(
                GOOGLEDRIVE, ("files",), "drive-open", account_id=account_id
            ),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError) as refusal:
            await tool.handler(ctx, args)
    assert str(refusal.value) == (
        "connect a 'googledrive' account before registering its sources "
        "(connect_account with provider='googledrive')"
    )


async def test_a_provider_the_namespace_does_not_claim_asks_for_its_credential(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open namespace is installed but does not claim this provider, so no account can ever be
    connected for it: with its slot unset the member is asked for the key, never sent through a
    connect flow that has nothing to connect."""
    monkeypatch.delenv(GREENHOUSE.upper(), raising=False)
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), open_namespace=True)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(GREENHOUSE, ("jobs",), "greenhouse-unkeyed"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError) as refusal:
            await tool.handler(ctx, args)
    assert str(refusal.value) == (
        "add the 'greenhouse' credential before registering its sources "
        "(request_credentials for slot 'greenhouse')"
    )


@pytest.mark.parametrize("keyed", [True, False])
async def test_a_direct_only_provider_refuses_an_account_id_keyed_or_not(
    db: None, monkeypatch: pytest.MonkeyPatch, keyed: bool
) -> None:
    """A provider the workspace credential is the only path for syncs through that credential, so
    naming an account_id is refused as the contradiction it is whether or not the key is set — the
    member is never sent to add a key that leaves the same apply failing, nor through an OAuth
    connect the namespace has nothing to connect."""
    if keyed:
        monkeypatch.setenv(GREENHOUSE.upper(), "secret")
    else:
        monkeypatch.delenv(GREENHOUSE.upper(), raising=False)
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), open_namespace=True)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(
                GREENHOUSE, ("jobs",), "greenhouse-keyed", account_id="acct-one"
            ),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError) as refusal:
            await tool.handler(ctx, args)
    assert str(refusal.value) == (
        "'greenhouse' uses its workspace credential, not a connected account"
    )


@pytest.mark.parametrize("direct_fallback", [True, False])
async def test_a_broker_failure_for_a_claimed_provider_raises(
    db: None, monkeypatch: pytest.MonkeyPatch, direct_fallback: bool
) -> None:
    monkeypatch.delenv(GOOGLEDRIVE.upper(), raising=False)
    monkeypatch.setattr(_OpenNamespace, "claims", _broker_down)
    state = await _workspace()
    ctx = _context(
        state,
        GrantStore(),
        brokered=(),
        direct_fallback=direct_fallback,
        open_namespace=True,
    )
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(GOOGLEDRIVE, ("files",), "drive-broker-down"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(RuntimeError, match="COMPOSIO_API_KEY"):
            await tool.handler(ctx, args)


async def test_provider_with_no_broker_and_no_direct_backend_refuses(db: None) -> None:
    """A provider no connector brokers, no open namespace claims, and no direct backend can
    authenticate is refused loud — the deploy simply cannot sync it."""
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), direct_fallback=False, open_namespace=False)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest_text(ASANA, ("workspaces",), "asana-orphan"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="no direct authentication backend can sync 'asana'"):
            await tool.handler(ctx, args)


async def test_open_namespace_provider_with_a_byok_key_syncs_directly(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider the open namespace does broker, with no connected account and a set BYOK
    credential, syncs through its direct key — the key is read before the namespace is asked, so the
    member who picked the path by setting a key is never forced through connect."""
    assert GOOGLEDRIVE in NAMESPACE_BROKERED
    monkeypatch.setenv(GOOGLEDRIVE.upper(), "secret")
    state = await _workspace()
    ctx = _context(state, GrantStore(), brokered=(), open_namespace=True)
    name = binding_name(GOOGLEDRIVE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        registered = await _apply(ctx, _manifest_text(GOOGLEDRIVE, ("files",), name))
    assert registered["result"] == "created"
    [row] = await _rows(state, GOOGLEDRIVE)
    assert row["config"]["account"] == DIRECT_ACCOUNT


@pytest.mark.parametrize(
    ("provider", "base_url", "normalized"),
    [
        ("active_campaign", "https://acme.api-us1.com/", "https://acme.api-us1.com"),
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
        ("active_campaign", "https://acme.freshdesk.com"),
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
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="freshdesk"):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _manifest_text(
                            FRESHDESK, ("tickets",), "freshdesk-x", base_url="https://127.0.0.1"
                        ),
                    }
                ),
            )
        with pytest.raises(ValueError, match="fixed API host"):
            await tool.handler(
                _context(state, grants, brokered=(ASANA,)),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _manifest_text(
                            ASANA, ("workspaces",), "asana-x", base_url="https://evil.test"
                        ),
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
    member_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        with pytest.raises(AdminRequired):
            await delete_tool.handler(
                speakerless,
                delete_tool.input_model.model_validate(
                    {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": member_name}
                ),
            )
    [row] = await _rows(state, GREENHOUSE)
    assert row["removed_at"] is None


async def test_admin_reapplying_a_members_private_source_is_a_noop(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace admin re-applying another member's private source with the identical spec is a
    no-op: it neither errors nor re-attributes the source to the admin (an existing binding never
    falls through to the create path, which would stamp the acting caller as owner)."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    state = await _workspace()
    member_ctx = _context(state, None, speaker_id=state.member_id)
    owner_ctx = _context(state, None)
    member_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
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
    with ws(state.workspace_id), agent(state.agent_id):
        ext = context_for(NAME, DECLARED_PROVIDERS)
        source_id = await ext.register_source(
            ASANA,
            ConnectorSourceConfig(account=account, stream=stream),
            subject=subject,
            owner_member_id=owner,
        )
    return binding_name(ASANA, account, None), source_id


async def _stored_subscribers(state: _Workspace, name: str) -> dict[str, str]:
    with ws(state.workspace_id), agent(state.agent_id):
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
    source_id: UUID,
    body: str,
    changed_at: datetime | None = None,
    subject: str = SHARED_SUBJECT,
    stream: str = "tasks",
    title: str = "",
    disposition: str = "added",
) -> PageChange:
    """One replayed page. `disposition` shapes the three fields the alert reads it from: a removed
    page is tombstoned, an added page carries equal create/change stamps, an updated page's change
    stamp is later — exactly what `SyncDriver._write` leaves behind."""
    now = changed_at or datetime(2026, 7, 20, tzinfo=UTC)
    created = now - timedelta(days=1) if disposition == "updated" else now
    return PageChange(
        page_id=uuid4(),
        source_id=source_id,
        subject=subject,
        stream=stream,
        title=title or body.removeprefix("# ")[:40],
        body=body,
        digest=f"sha256:{uuid4().hex}",
        revision=1,
        tombstone=disposition == "removed",
        created_at=created,
        as_of=now,
        changed_at=now,
    )


async def test_subscribe_and_unsubscribe_self_on_a_shared_source(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id), agent(state.agent_id):
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
                    get_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
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
    with ws(state.workspace_id), agent(state.agent_id):
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
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="your own conversation"):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _subscribe_manifest(name, (other,), shared=True),
                    }
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
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(UnknownObject):
            await tool.handler(
                _context(state, None, speaker_id=stranger),
                tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _subscribe_manifest(
                            name, (state.conversation_id.hex,), shared=False
                        ),
                    }
                ),
            )
        assert await _stored_subscribers(state, name) == {}


async def test_get_renders_an_empty_status_for_a_binding_removed_mid_verb(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`source` returns no generation, so it is last-write-wins: `object_get` reads the spec, then
    reads status under the snapshot it just took, and a binding removed in between is simply gone —
    an empty status, never a refusal that the source changed while reading."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    real_status = SourceObjects.status
    get_tool = _TOOLS["object_get"]

    async def remove_before_status(
        store: SourceObjects,
        ctx: ToolContext,
        read: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        await context_for(NAME, DECLARED_PROVIDERS).remove_source(source_id)
        return await real_status(store, ctx, read, expected_generation=expected_generation)

    monkeypatch.setattr(SourceObjects, "status", remove_before_status)
    with ws(state.workspace_id), agent(state.agent_id):
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    _context(state, None),
                    get_tool.input_model.model_validate(
                        {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": name}
                    ),
                )
            )
            .content[0]
            .text
        )
    assert fetched["spec"]["provider"] == ASANA
    assert fetched["status"] is None
    [row] = await _rows(state, ASANA)
    assert row["removed_at"] is not None


def _delete_binding_before_apply(state: _Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Have the registrar's delete land between the subscribe verb's read and its edit, so the edit
    commits against a binding whose rows and subscribers are already gone."""
    real_apply = SourceObjects.apply
    delete_tool = _TOOLS["object_delete"]

    async def delete_before_apply(
        store: SourceObjects,
        ctx: ToolContext,
        edited: str,
        spec: SourceSpec,
        old: SourceSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        await delete_tool.handler(
            _context(state, None),
            delete_tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "kind": SOURCE_KIND, "name": edited}
            ),
        )
        await real_apply(store, ctx, edited, spec, old, expected_generation=expected_generation)

    monkeypatch.setattr(SourceObjects, "apply", delete_before_apply)


async def test_a_subscription_edit_strands_nothing_on_a_binding_removed_mid_verb(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subscribers map belongs to the binding, so the binding's removal takes it: an edit that
    commits after the registrar's delete refuses and leaves nothing behind. A stored map outliving
    its rows would alert a conversation about a source it can no longer see."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    _delete_binding_before_apply(state, monkeypatch)

    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(UnknownObject, match=name):
            await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        assert await _stored_subscribers(state, name) == {}
        await on_page_change(
            HookContext(
                ext=context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id)),
                payload=PageChangeBatch(changes=(_change(source_id, "# asana tasks: dropped"),)),
            )
        )
        assert await _turns(state.conversation_id) == []
    [row] = await _rows(state, ASANA)
    assert row["removed_at"] is not None


async def test_re_registering_a_removed_binding_carries_no_old_subscribers(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source name derives from its (provider, account, base_url) identity, so re-registering that
    identity — the documented recreate path — revives the same name. It arrives unsubscribed: a
    change on the revived rows alerts nobody, because no subscription to the binding that was
    removed survives to be inherited."""
    state = await _workspace()
    name, _removed = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    _delete_binding_before_apply(state, monkeypatch)

    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(UnknownObject, match=name):
            await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))

    revived_name, revived_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await on_page_change(
            HookContext(
                ext=context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id)),
                payload=PageChangeBatch(changes=(_change(revived_id, "# asana tasks: revived"),)),
            )
        )
        assert await _stored_subscribers(state, name) == {}
        assert await _turns(state.conversation_id) == []
    assert revived_name == name
    [row] = await _rows(state, ASANA)
    assert row["removed_at"] is None


async def test_a_registration_absorbs_a_binding_another_turn_created_first(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`source` returns no generation, so two turns registering one binding settle on one row: the
    second reads no source, finds the first's rows when it applies, and the identical spec is the
    idempotent no-op this kind already promises — never a refusal that the source changed while
    editing, which would end the turn on a name the registrar asked for and now has."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    real_apply = SourceObjects.apply

    async def register_before_apply(
        store: SourceObjects,
        tool_ctx: ToolContext,
        applied: str,
        spec: SourceSpec,
        old: SourceSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id, stream="workspaces")
        await real_apply(
            store, tool_ctx, applied, spec, old, expected_generation=expected_generation
        )

    monkeypatch.setattr(SourceObjects, "apply", register_before_apply)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(
            ctx,
            _manifest_text(ASANA, ("workspaces",), name, account_id="acct-one", shared=True),
        )

    assert applied == {"kind": SOURCE_KIND, "name": name, "result": "created"}
    [row] = await _rows(state, ASANA)
    assert row["config"]["stream"] == "workspaces"
    assert row["subject"] == SHARED_SUBJECT


async def test_page_change_alerts_only_subscribed_conversations_idempotently(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        legal = _change(source_id, "# asana tasks: Follow up with legal")
        stray = _change(uuid4(), "# folder: untracked")
        batch = PageChangeBatch(changes=(shipped, legal, stray))
        await on_page_change(HookContext(ext=ext, payload=batch))
        (turn,) = await _turns(state.conversation_id)
        assert name in turn["inbound"]
        assert "tasks: 2 added" in turn["inbound"]
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
    with ws(state.workspace_id), agent(state.agent_id):
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
                        _change(
                            tasks_id,
                            "# t",
                            changed_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
                            stream="tasks",
                        ),
                        _change(
                            projects_id,
                            "# p",
                            changed_at=datetime(2026, 7, 20, 10, tzinfo=UTC),
                            stream="projects",
                        ),
                    )
                ),
            )
        )
        (turn,) = await _turns(state.conversation_id)
        assert "projects: 1 added; tasks: 1 added" in turn["inbound"]


async def _second_agent_conversation(state: _Workspace) -> tuple[UUID, UUID]:
    """A second agent and a conversation bound to it. It registers nothing, so it holds no
    `source_grant` and reaches no source at all."""
    agent_id, conversation_id = uuid4(), uuid4()
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=state.workspace_id,
                name="scout",
                prompt="p",
                model="claude-opus-4-8",
                is_main=False,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=state.workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=state.owner_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return agent_id, conversation_id


async def test_alert_skips_a_subscriber_whose_agent_holds_no_grant(db: None, tmp_path) -> None:
    """Subscribing is a member act on a shared source, but reading its pages is the agent's own
    grant. A conversation bound to an agent that was never granted the changed source is alerted
    about nothing: no turn, no change log, no page ids anywhere — while the granted subscriber in
    the same batch is alerted in full, so the suppression is per-subscriber and not a dropped
    batch."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    outsider_agent_id, outsider_conversation_id = await _second_agent_conversation(state)
    granted, outsider = state.conversation_id.hex, outsider_conversation_id.hex
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id):
        with agent(state.agent_id):
            await _apply(_context(state, None), _subscribe_manifest(name, (granted,), shared=True))
        with agent(outsider_agent_id):
            base = _context(state, None)
            await _apply(
                replace(
                    base,
                    turn=base.turn.model_copy(
                        update={
                            "conversation_id": outsider_conversation_id,
                            "agent_id": outsider_agent_id,
                        }
                    ),
                ),
                _subscribe_manifest(name, tuple(sorted((granted, outsider))), shared=True),
            )
        assert await _stored_subscribers(state, name) == {
            granted: state.agent_id.hex,
            outsider: outsider_agent_id.hex,
        }

        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(shipped,))))

    (alerted,) = await _turns(state.conversation_id)
    assert f"{PAGE_KIND}/{shipped.page_id}" in alerted["inbound"]
    assert len(await _change_log(sandboxes, state.conversation_id, name)) == 1

    assert await _turns(outsider_conversation_id) == []
    assert not (sandboxes.workspace_root / str(outsider_conversation_id) / CHANGE_LOG_DIR).exists()


async def test_alert_never_surfaces_a_member_private_page(db: None) -> None:
    """A subscription to a shared source surfaces only shared changes — a member-private page in
    the same batch is neither referenced nor counted, so its existence never leaks."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shared = _change(source_id, "# asana tasks: Ship it")
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(shared, private)))
        )
        (turn,) = await _turns(state.conversation_id)
        assert "tasks: 1 added" in turn["inbound"]
        assert f"{PAGE_KIND}/{shared.page_id}" in turn["inbound"]
        assert str(private.page_id) not in turn["inbound"]


def _sandboxes(tmp_path) -> ConversationSandbox:
    """The real workspace seam under the change log: a `ConversationSandbox` over the local
    carrier, so the log lands as bytes at `tmp_path/workspaces/<conversation>/<rel>`."""
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=tmp_path / "workspaces",
    )


async def _change_log(
    sandboxes: ConversationSandbox, conversation_id: UUID, name: str
) -> list[dict]:
    """Every line of the one change log written for this binding, read back off the host directory
    the carrier serves as `/workspace`."""
    log_dir = sandboxes.workspace_root / str(conversation_id) / CHANGE_LOG_DIR / name
    (entry,) = sorted(log_dir.iterdir())
    return [json.loads(line) for line in entry.read_text().splitlines()]


async def test_alert_counts_by_stream_and_never_truncates(db: None, tmp_path) -> None:
    """A batch past the naming bound carries per-stream added/updated/removed counts and the path
    to the change log — never a prefix of page ids and an opaque `+N more`. Every changed page is
    in the log, so nothing the agent needs is dropped."""
    state = await _workspace()
    name, tasks_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    _, projects_id = await _register(
        state, subject=SHARED_SUBJECT, owner=state.owner_id, stream="projects"
    )
    sandboxes = _sandboxes(tmp_path)
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
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        changes = (
            *(_change(tasks_id, f"# task {n}", stream="tasks") for n in range(3)),
            *(
                _change(tasks_id, f"# stale {n}", stream="tasks", disposition="updated")
                for n in range(4)
            ),
            *(
                _change(projects_id, f"# gone {n}", stream="projects", disposition="removed")
                for n in range(2)
            ),
        )
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        (turn,) = await _turns(state.conversation_id)
        assert "projects: 2 removed; tasks: 3 added, 4 updated" in turn["inbound"]
        assert "more" not in turn["inbound"]
        assert not any(str(change.page_id) in turn["inbound"] for change in changes)

        logged = await _change_log(sandboxes, state.conversation_id, name)
        assert f"/workspace/{CHANGE_LOG_DIR}/{name}/" in turn["inbound"]
        assert {entry["page"] for entry in logged} == {
            f"{PAGE_KIND}/{change.page_id}" for change in changes
        }
        assert Counter(entry["change"] for entry in logged) == {
            "added": 3,
            "updated": 4,
            "removed": 2,
        }
        assert {entry["stream"] for entry in logged} == {"tasks", "projects"}


async def test_change_log_replay_rewrites_rather_than_appends(db: None, tmp_path) -> None:
    """The log is named for the same latest-change stamp the alert's idempotency key carries, so a
    replayed batch overwrites one file instead of appending its pages a second time."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    sandboxes = _sandboxes(tmp_path)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        batch = PageChangeBatch(changes=tuple(_change(source_id, f"# task {n}") for n in range(6)))
        await on_page_change(HookContext(ext=ext, payload=batch))
        await on_page_change(HookContext(ext=ext, payload=batch))

        assert len(await _turns(state.conversation_id)) == 1
        logged = await _change_log(sandboxes, state.conversation_id, name)
        assert len(logged) == 6


async def test_change_log_omits_a_member_private_page(db: None, tmp_path) -> None:
    """The shared-only filter governs the log as well as the message — a private page is not
    written to a file a subscriber who cannot read it will open."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    sandboxes = _sandboxes(tmp_path)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        shared = tuple(_change(source_id, f"# task {n}") for n in range(6))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(*shared, private)))
        )

        logged = await _change_log(sandboxes, state.conversation_id, name)
        assert {entry["page"] for entry in logged} == {
            f"{PAGE_KIND}/{change.page_id}" for change in shared
        }


async def test_change_log_failure_propagates_rather_than_degrading(db: None, tmp_path) -> None:
    """A change log that cannot be written fails the batch instead of quietly alerting without it.
    Here the subscribed conversation no longer resolves in this workspace — internal state, not
    external flakiness — so it raises, the cursor stays put for the next tick, and no alert claims
    a delta whose detail was dropped."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    sandboxes = _sandboxes(tmp_path)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.conversation).where(
                    tables.conversation.c.id == state.conversation_id
                )
            )
        changes = tuple(_change(source_id, f"# task {n}") for n in range(6))
        with pytest.raises(ValueError):
            await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        assert await _turns(state.conversation_id) == []


async def test_alert_degrades_to_counts_when_no_sandbox_is_wired(db: None) -> None:
    """No workspace seam means no change log; the alert still reports what changed and names the
    object_list route rather than losing the turn to plumbing."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        changes = tuple(_change(source_id, f"# task {n}") for n in range(6))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        (turn,) = await _turns(state.conversation_id)
        assert "tasks: 6 added" in turn["inbound"]
        assert "object_list page" in turn["inbound"]


async def test_alert_skipped_when_only_member_private_changes(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    caller = state.conversation_id.hex
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _subscribe_manifest(name, (caller,), shared=True))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(private,))))
        assert await _turns(state.conversation_id) == []
