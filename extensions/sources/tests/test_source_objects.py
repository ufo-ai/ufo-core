"""The `source` object kind end to end: private-by-default registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` rows and the verbs' own results: derived names,
error-driven discovery (unknown provider/stream refusals listing the valid sets), the tenant-URL
safety rules, broker- and direct-auth resolution, an apply settling a live binding on the streams
it names, delete marking the row removed and tombstoning its pages, and revival on an identical
re-registration. A source is private to its registering
member by default; sharing it and deleting it are gated to the registrar or a workspace admin."""

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import (
    CHANGE_LOG_DIR,
    CONNECTION_OBJECT_KIND,
    MAX_BACKFILL_DAYS,
    SOURCE_KIND,
    SOURCE_TRIGGER_KIND,
    SourceObjects,
    SourceSpec,
    _validated_base_url,
    on_page_change,
    trigger_name,
)
from ufo_ext_sources.triggers import SourceTrigger, SourceTriggerStore

from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import RUNTIME_DIRNAME, ProxyEndpoint
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore, credential_object_name, named_slots
from ufo.runtime.access.grants import GrantStore, account_object_name
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import JsonValue, context_for
from ufo.runtime.ext.manifest import declared_slots
from ufo.runtime.ext.surface import _binding_fields
from ufo.runtime.kinds.credential_kind import CREDENTIAL_KIND
from ufo.runtime.objects import UnknownObject
from ufo.runtime.sources.sync import SyncDriver
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.authproxy import DIRECT_ACCOUNT
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.manifest import HookContext, PageChangeBatch
from ufo.sdk.objects import AdminRequired, ObjectListQuery, VerbNotSupported
from ufo.sdk.sources import ConnectorSourceConfig, PageChange, binding_name
from ufo.sdk.tools import SpeakerRequired, ToolContext

TOOL_NARRATION = "setting up the connection"

ASANA = "asana"
ASANA_HOST = "app.asana.com"
GREENHOUSE = "greenhouse"
GMAIL = "gmail"
OUTLOOK = "outlook"
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
    agent_id: UUID | None = None,
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
            agent_id=agent_id or state.agent_id,
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
    resync: bool = False,
    backfill_days: int | str | None = None,
) -> str:
    spec: dict[str, object] = {"provider": provider, "streams": list(streams)}
    if account_id:
        spec["account_id"] = account_id
    if base_url:
        spec["base_url"] = base_url
    if shared:
        spec["shared"] = shared
    if resync:
        spec["resync"] = resync
    if backfill_days is not None:
        spec["backfill_days"] = backfill_days
    return yaml.safe_dump({"kind": SOURCE_KIND, "name": name, "spec": spec})


async def _apply(ctx: ToolContext, manifest_text: str) -> dict[str, object]:
    tool = _TOOLS["object_apply"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"manifest": manifest_text}),
    )
    assert result.is_error is False
    return json.loads(result.content[0].text)


async def _get(ctx: ToolContext, name: str) -> dict[str, object]:
    tool = _TOOLS["object_get"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
    )
    assert result.is_error is False
    return yaml.safe_load(result.content[0].text)


def _row_id_before_windows(
    workspace_id: UUID, provider: str, account: str, stream: str, connection_id: UUID
) -> UUID:
    """The `source_row_id` a brokered binding hashed to before a config could carry a backfill
    window — the golden value a windowed registration must still settle on."""
    config = json.dumps({"account": account, "base_url": None, "stream": stream}, sort_keys=True)
    return uuid5(
        NAMESPACE_URL, f"{workspace_id}/source/{provider}/{config}/connection/{connection_id}"
    )


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
    assert "page_change" in {hook.event for hook in declared.hooks}
    assert {slot.name for slot in declared.credentials} == set(CONNECTORS)
    assert {source.backend for source in declared.sources} == set(CONNECTORS)


async def _shipped_agent(state: _Workspace, name: str) -> UUID:
    agent_id = uuid4()
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=state.workspace_id,
                    name=name,
                    prompt="p",
                    model="claude-opus-4-8",
                    is_main=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    return agent_id


async def _granted_agents(state: _Workspace) -> set[UUID]:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            return set(
                (
                    await connection.execute(
                        sa.select(tables.source_grant.c.agent_id).where(
                            tables.source_grant.c.workspace_id == state.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )


async def _readable_sources(state: _Workspace, ctx: ToolContext) -> frozenset[UUID]:
    """Which source rows this turn's agent reaches — the gate its pages come through, so a grant
    row that leaves the agent with nothing to read proves nothing."""
    with ws(state.workspace_id):
        return await context_for(NAME, DECLARED_PROVIDERS).readable_source_ids(ctx.source_reader())


async def test_a_source_the_workspace_holds_is_granted_to_the_agent_that_asks(db: None) -> None:
    """A shipped agent asks for the feed it needs and the workspace already watches that account —
    the case the whole point of shipping an agent runs into. The binding settles where it is, one
    row syncing once, and the asking agent leaves holding it.

    Before this, the apply found the binding, returned ahead of registering, and wrote no grant: no
    error, no feed, and no verb that could give it one. The member's only route was to delete the
    binding and register it again, which tombstones every page the first source synced."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    name = binding_name(ASANA, "acct-one", None)
    streams = ("workspaces", "projects")

    with ws(state.workspace_id), agent(state.agent_id):
        assert (
            await _apply(
                _context(state, grants, brokered=(ASANA,)), _manifest_text(ASANA, streams, name)
            )
        )["result"] == "created"

    assert await _granted_agents(state) == {state.agent_id}
    rows = await _rows(state, ASANA)

    shipped = await _shipped_agent(state, "code-review")
    with ws(state.workspace_id), agent(shipped):
        # The connection half of the same setup already had its verb; the source half did not.
        assert await grants.attach(
            provider=ASANA,
            account_id="acct-one",
            conversation_id=state.conversation_id,
            actor_member_id=state.owner_id,
            shared=False,
        )
        applied = await _apply(
            _context(state, grants, brokered=(ASANA,), agent_id=shipped),
            _manifest_text(ASANA, streams, name),
        )

    assert applied == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
    assert await _granted_agents(state) == {state.agent_id, shipped}
    # One binding, not two: the second agent reads the rows the first one's sync already fills.
    assert [row["id"] for row in await _rows(state, ASANA)] == [row["id"] for row in rows]


async def test_the_agent_asking_for_a_held_source_is_granted_it_by_the_identical_submit(
    db: None,
) -> None:
    """The submit that names the account the binding already carries is identical to the stored
    spec, so it settles on the rows that exist and registers nothing — the path a manifest written
    from a read of the binding takes. The grant is still what the asking agent came for."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    name = binding_name(ASANA, "acct-one", None)
    submitted = _manifest_text(ASANA, ("workspaces", "projects"), name, account_id="acct-one")

    with ws(state.workspace_id), agent(state.agent_id):
        assert (await _apply(_context(state, grants, brokered=(ASANA,)), submitted))[
            "result"
        ] == "created"

    rows = await _rows(state, ASANA)
    shipped = await _shipped_agent(state, "code-review")
    shipped_ctx = _context(state, grants, brokered=(ASANA,), agent_id=shipped)
    assert await _readable_sources(state, shipped_ctx) == frozenset()
    with ws(state.workspace_id), agent(shipped):
        applied = await _apply(shipped_ctx, submitted)

    assert applied == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
    assert await _granted_agents(state) == {state.agent_id, shipped}
    assert await _readable_sources(state, shipped_ctx) == {row["id"] for row in rows}
    assert [row["id"] for row in await _rows(state, ASANA)] == [row["id"] for row in rows]


async def test_a_source_another_member_holds_privately_is_not_granted_away(db: None) -> None:
    """A grant decides which agent reaches a feed, never who may read what it syncs — the subject on
    each page still does that. What it does carry is the authority behind the feed, which belongs to
    the member whose connection serves it, so a second member cannot hand it to an agent."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    name = binding_name(ASANA, "acct-one", None)

    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, grants, brokered=(ASANA,)), _manifest_text(ASANA, ("workspaces",), name)
        )
    source_id = (await _rows(state, ASANA))[0]["id"]
    shipped = await _shipped_agent(state, "code-review")

    with ws(state.workspace_id), agent(shipped):
        with pytest.raises(ValueError, match="privately"):
            await context_for(NAME, DECLARED_PROVIDERS).grant_source(
                source_id, agent_id=shipped, actor_member_id=state.member_id
            )

    assert await _granted_agents(state) == {state.agent_id}


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
        "resync": False,
        "backfill_days": None,
    }
    assert set(fetched["status"]["streams"]) == {"projects", "workspaces"}
    assert fetched["status"]["streams"]["projects"]["backfill_after"] is None
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
                        "manifest": _manifest_text("nonesuch", ("things",), "nonesuch-x"),
                    }
                ),
            )
        with pytest.raises(ValueError, match="workspaces"):
            await tool.handler(
                _context(state, grants, brokered=(ASANA,)),
                tool.input_model.model_validate(
                    {
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
                    get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
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
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
            )
        await delete_tool.handler(
            member_ctx,
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
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
                    list_tool.input_model.model_validate({"kind": SOURCE_KIND}),
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
                        ctx,
                        list_tool.input_model.model_validate({"kind": SOURCE_KIND}),
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


async def test_portal_reads_hide_other_members_private_sources(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The portal reads a source through the same registrar-or-shared-or-admin gate the turn does:
    a stranger's index carries only the shared binding and their detail read of the private one
    answers nothing, while the registrar and a workspace admin read both."""
    monkeypatch.setenv("GREENHOUSE", "secret")
    monkeypatch.setenv("FRESHDESK", "secret")
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, None, speaker_id=state.member_id)
    private_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    shared_name = binding_name(FRESHDESK, DIRECT_ACCOUNT, "https://acme.freshdesk.com")
    store = SourceObjects()
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
        ext = member_ctx.ext
        stranger_page = await store.member_page(
            ext, member_id=stranger_id, admin=False, query=ObjectListQuery()
        )
        assert [row.name for row in stranger_page.rows] == [shared_name]
        assert (
            await store.member_detail(ext, private_name, member_id=stranger_id, admin=False)
        ) is None
        for member_id, admin in ((state.member_id, False), (state.owner_id, True)):
            page = await store.member_page(
                ext, member_id=member_id, admin=admin, query=ObjectListQuery()
            )
            assert {row.name for row in page.rows} == {private_name, shared_name}
            read = await store.member_detail(ext, private_name, member_id=member_id, admin=admin)
            assert read is not None
            assert read.detail.spec.provider == GREENHOUSE


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
            "manifest": _manifest_text(GREENHOUSE, ("jobs",), name),
        }
    )
    with (
        ws(state.workspace_id),
        agent(state.agent_id),
        pytest.raises(ValueError, match="speaking member"),
    ):
        await tool.handler(ctx, args)


async def test_a_mail_binding_pins_a_thirty_day_first_sync_window(db: None) -> None:
    """A mail stream's declared window resolves at registration into an absolute cutoff the row
    keeps, and re-applying the same spec leaves that cutoff where it is. The row id is the one this
    account and stream hashed to before a binding could carry a window at all."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    before = datetime.now(UTC)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name))
        [registered] = await _rows(state, GMAIL)
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name))
        fetched = await _get(ctx, name)
    [row] = await _rows(state, GMAIL)
    pinned = datetime.fromisoformat(str(row["config"]["backfill_after"]))
    assert row["config"]["backfill_days"] is None
    assert before - timedelta(days=30) <= pinned <= datetime.now(UTC) - timedelta(days=30)
    assert row["config"]["backfill_after"] == registered["config"]["backfill_after"]
    assert row["id"] == _row_id_before_windows(
        state.workspace_id, GMAIL, "acct-one", "messages", row["connection_id"]
    )
    assert fetched["spec"]["backfill_days"] is None
    assert fetched["status"]["streams"]["messages"]["backfill_after"] == pinned.isoformat()


async def test_a_binding_narrows_its_first_sync_window(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    before = datetime.now(UTC)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=7))
        fetched = await _get(ctx, name)
    [row] = await _rows(state, GMAIL)
    pinned = datetime.fromisoformat(str(row["config"]["backfill_after"]))
    assert row["config"]["backfill_days"] == 7
    assert before - timedelta(days=7) <= pinned <= datetime.now(UTC) - timedelta(days=7)
    assert fetched["spec"]["backfill_days"] == 7


async def test_a_binding_asks_for_all_history(db: None) -> None:
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days="all"))
        fetched = await _get(ctx, name)
    [row] = await _rows(state, GMAIL)
    assert row["config"]["backfill_days"] == "all"
    assert row["config"]["backfill_after"] is None
    assert fetched["spec"]["backfill_days"] == "all"
    assert fetched["status"]["streams"]["messages"]["backfill_after"] is None


async def test_a_window_pins_only_the_streams_that_take_one(db: None) -> None:
    """A binding's streams do not all read a window: a mail message stream floors its first walk at
    the cutoff, while the calendar stream beside it reaches back the fixed distance Graph gives it.
    So the request is stored on the binding — one window per binding, whichever row is read back —
    while the cutoff is pinned only where a run honours it, which is what `status` reports per
    stream. The request here is wider than the stream's own 30 days, the override direction
    registration is otherwise never asked for."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, OUTLOOK, "acct-one")
    ctx = _context(state, grants, brokered=(OUTLOOK,))
    name = binding_name(OUTLOOK, "acct-one", None)
    before = datetime.now(UTC)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(OUTLOOK, ("events", "messages"), name, backfill_days=90))
        fetched = await _get(ctx, name)
    rows = {str(row["config"]["stream"]): row["config"] for row in await _rows(state, OUTLOOK)}
    assert {stream: config["backfill_days"] for stream, config in rows.items()} == {
        "events": 90,
        "messages": 90,
    }
    assert rows["events"]["backfill_after"] is None
    pinned = datetime.fromisoformat(str(rows["messages"]["backfill_after"]))
    assert before - timedelta(days=90) <= pinned <= datetime.now(UTC) - timedelta(days=90)
    streams = fetched["status"]["streams"]
    assert streams["events"]["backfill_after"] is None
    assert datetime.fromisoformat(str(streams["messages"]["backfill_after"])) == pinned
    assert fetched["spec"]["backfill_days"] == 90


async def test_widening_a_mixed_binding_moves_only_the_streams_that_take_a_window(
    db: None,
) -> None:
    """Widening a binding whose streams do not all take a window has to do two different things at
    once: re-pin and refetch the mail stream, and leave the calendar stream's cursor and (absent)
    cutoff exactly where they were, since nothing about what it reaches has changed. Both rows still
    carry the request, so the binding reads back one window whichever row answers — the invariant
    registration established and a partial widen would break."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, OUTLOOK, "acct-one")
    ctx = _context(state, grants, brokered=(OUTLOOK,))
    name = binding_name(OUTLOOK, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(OUTLOOK, ("events", "messages"), name, backfill_days=30))
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(cursor="delta-link"))
    was = {
        str(row["config"]["stream"]): row["config"]["backfill_after"]
        for row in await _rows(state, OUTLOOK)
    }
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(OUTLOOK, ("events", "messages"), name, backfill_days=365))
        fetched = await _get(ctx, name)
    rows = {str(row["config"]["stream"]): row for row in await _rows(state, OUTLOOK)}

    assert {stream: row["config"]["backfill_days"] for stream, row in rows.items()} == {
        "events": 365,
        "messages": 365,
    }
    assert rows["events"]["config"]["backfill_after"] is None
    assert rows["events"]["cursor"] == "delta-link"
    assert datetime.fromisoformat(
        str(rows["messages"]["config"]["backfill_after"])
    ) == datetime.fromisoformat(str(was["messages"])) - timedelta(days=335)
    assert rows["messages"]["cursor"] is None
    assert fetched["spec"]["backfill_days"] == 365


async def test_a_window_is_refused_where_no_selected_stream_takes_one(db: None) -> None:
    """Accepting the knob on a binding nothing would honour pins, stores, and reports a cutoff that
    changes what no run does — so the apply refuses instead, and says which streams do take a window
    (none, for this provider)."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="no selected 'asana' stream takes a backfill window"):
            await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name, backfill_days=7))
        with pytest.raises(ValueError, match="backfill window"):
            await _apply(ctx, _manifest_text(ASANA, ("workspaces",), name, backfill_days="all"))
    assert await _rows(state, ASANA) == []


def test_the_backfill_knob_refuses_a_day_count_outside_its_bounds() -> None:
    """Both ends of the knob are validation, so a wrong number comes back as a refusal the agent can
    read and correct: zero or negative would pin a cutoff at or after the instant of registration
    and select an empty first sync, and a count big enough to overflow the `timedelta` behind the
    pin would leave the handler contract as an `OverflowError` instead. `"all"` is the unbounded
    case, so no legitimate request needs a six-digit day count."""
    for refused in (0, -30, MAX_BACKFILL_DAYS + 1, 1_000_000, 739_000):
        with pytest.raises(ValidationError):
            SourceSpec(provider=GMAIL, streams=("messages",), backfill_days=refused)
    accepted = SourceSpec(provider=GMAIL, streams=("messages",), backfill_days=MAX_BACKFILL_DAYS)
    assert accepted.backfill_days == MAX_BACKFILL_DAYS


async def test_widening_a_live_bindings_window_repins_it_from_the_same_anchor(db: None) -> None:
    """Raising `backfill_days` re-pins a live binding in place and refetches the wider window,
    because the floor only ever moves EARLIER: every page the binding already holds stays inside
    the window, so nothing it synced is orphaned and the refetch lands each record back on its
    existing page.

    The new floor is resolved against the instant the binding was registered, never against `now`.
    This binding is backdated to prove it: registered 60 days ago at 7 days, it is pinned 67 days
    back, and widening to 30 must reach 90 days back — 30 from the anchor. Resolved against `now`
    instead, 30 days would land 30 days back, which is a month LATER than the floor it replaced and
    would strand everything between. The assertion is the exact instant, so that recompute fails it
    rather than passing on a coin flip.

    The cursor is cleared so the next run actually walks the widened floor; a gmail row that kept
    its `historyId` would stay on the delta path and never revisit the wider window at all."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=7))
    anchor = datetime.now(UTC) - timedelta(days=60)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source).values(
                config={
                    "account": "acct-one",
                    "stream": "messages",
                    "base_url": None,
                    "backfill_days": 7,
                    "backfill_after": (anchor - timedelta(days=7)).isoformat(),
                },
                cursor="9001",
            )
        )
    [before] = await _rows(state, GMAIL)
    with ws(state.workspace_id), agent(state.agent_id):
        widened = await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=30))
    [row] = await _rows(state, GMAIL)

    assert widened["result"] != "created"
    assert row["id"] == before["id"]
    assert row["config"]["backfill_days"] == 30
    assert datetime.fromisoformat(str(row["config"]["backfill_after"])) == anchor - timedelta(
        days=30
    )
    # further back than it was, which is the whole licence for doing this in place
    assert datetime.fromisoformat(str(row["config"]["backfill_after"])) < datetime.fromisoformat(
        str(before["config"]["backfill_after"])
    )
    assert row["cursor"] is None


async def test_dropping_a_request_that_names_the_declared_window_relabels_without_refetching(
    db: None,
) -> None:
    """`backfill_days: 30` and an unset `backfill_days` resolve to the same instant on a stream that
    declares 30, so a member who reads the spec back and resubmits it without the field is asking
    for the window the row already holds. That is neither a widening nor a narrowing: it records
    what was asked for and leaves the pin and the cursor alone. Refusing it as a narrowing — which
    comparing the pins with `>=` would — would refuse a spec round-trip."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=30))
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(cursor="9001"))
    [before] = await _rows(state, GMAIL)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name))
    [row] = await _rows(state, GMAIL)

    assert row["config"]["backfill_days"] is None
    assert row["config"]["backfill_after"] == before["config"]["backfill_after"]
    assert row["cursor"] == "9001"


async def test_the_portals_row_acts_survive_a_binding_that_holds_a_window(db: None) -> None:
    """The portal reconstructs a binding's spec from `_binding_fields` and submits
    `{...row.apply, <the one thing the button changes>}`. So every field the binding's identity is
    built from has to survive that projection, or it arrives as its default and reads as an edit
    the member never made — and both row acts die on a binding that merely holds a window:

    Resync submits the binding's own spec with `resync` set, and `_resync` refuses anything whose
    identity differs from the read-back binding's. Share submits `shared: true`, which reaches the
    window comparison before the share-flip branch. Neither button can supply `backfill_days`, so
    on any windowed binding both were permanently refused — resync with "a resync changes nothing
    else", share with a refusal about the backfill window that names nothing the member touched.

    This drives the portal's own payloads, built from the projection rather than hand-written, so
    it fails again if the field is dropped from either end."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=7))
    [row] = await _rows(state, GMAIL)
    projected = _binding_fields(GMAIL, dict(row["config"]))
    assert projected["backfill_days"] == 7  # the portal can see the window at all

    def portal_act(**changed: object) -> str:
        spec: dict[str, object] = {
            "provider": GMAIL,
            "streams": [str(projected["stream"])],
            "account_id": projected["account_id"],
            "base_url": projected["base_url"],
            "shared": False,
            "backfill_days": projected["backfill_days"],
            **changed,
        }
        return yaml.safe_dump({"kind": SOURCE_KIND, "name": name, "spec": spec})

    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, portal_act(resync=True))
        await _apply(ctx, portal_act(shared=True))

    [after] = await _rows(state, GMAIL)
    assert after["config"]["backfill_days"] == 7
    assert after["config"]["backfill_after"] == row["config"]["backfill_after"]
    assert after["subject"] == SHARED_SUBJECT


async def test_a_submit_editing_the_window_and_the_streams_is_refused_whole(db: None) -> None:
    """Both edits an apply absorbs — the window and the stream set — write rows, so the order of
    the two decides whether a submit that changes both is refused whole or left half applied. Every
    check runs before either write: this submit narrows 90 days to 7 AND drops a stream, and has to
    come back refused with the binding still pinned to 90 and still syncing both streams — not left
    holding one stream by an apply the member was told did not happen."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, OUTLOOK, "acct-one")
    ctx = _context(state, grants, brokered=(OUTLOOK,))
    name = binding_name(OUTLOOK, "acct-one", None)
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(OUTLOOK, ("events", "messages"), name, backfill_days=90))
        args = apply_tool.input_model.model_validate(
            {
                "manifest": _manifest_text(OUTLOOK, ("messages",), name, backfill_days=7),
            }
        )
        with pytest.raises(VerbNotSupported, match="only ever widens"):
            await apply_tool.handler(ctx, args)
    rows = {str(row["config"]["stream"]): row["config"] for row in await _rows(state, OUTLOOK)}
    assert sorted(rows) == ["events", "messages"]
    assert {config["backfill_days"] for config in rows.values()} == {90}


async def test_dropping_the_last_windowed_stream_narrows_a_binding_that_holds_a_window(
    db: None,
) -> None:
    """A narrowing that drops every stream the window reaches has to complete. The window is weighed
    on the rows the submit keeps: the dropped `messages` row goes with its pages, so its pin decides
    nothing and the unset request arriving with the narrowing lowers no live floor. Weighing the
    dropped row instead refused this submit as a narrowing, while keeping `backfill_days: 90` is
    refused because no named stream honours it — the member had no submit left and had to delete a
    binding to drop one stream from it.

    The kept rows are relabelled, never refetched: neither takes a cutoff, so their cursors
    stand."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, OUTLOOK, "acct-one")
    ctx = _context(state, grants, brokered=(OUTLOOK,))
    name = binding_name(OUTLOOK, "acct-one", None)
    kept = ("contacts", "events")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(OUTLOOK, (*kept, "messages"), name, backfill_days=90))
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(cursor="9001"))
    before = {str(row["config"]["stream"]): row for row in await _rows(state, OUTLOOK)}
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(
            ValueError, match="no selected 'outlook' stream takes a backfill window"
        ):
            await _apply(ctx, _manifest_text(OUTLOOK, kept, name, backfill_days=90))
        narrowed = await _apply(ctx, _manifest_text(OUTLOOK, kept, name))
        fetched = await _get(ctx, name)
    rows = {str(row["config"]["stream"]): row for row in await _rows(state, OUTLOOK)}

    assert narrowed["result"] == "updated"
    assert rows["messages"]["removed_at"] is not None
    assert [rows[stream]["removed_at"] for stream in kept] == [None, None]
    assert [rows[stream]["id"] for stream in kept] == [before[stream]["id"] for stream in kept]
    assert {rows[stream]["config"]["backfill_days"] for stream in kept} == {None}
    assert {rows[stream]["config"]["backfill_after"] for stream in kept} == {None}
    assert {rows[stream]["cursor"] for stream in kept} == {"9001"}
    assert fetched["spec"]["streams"] == list(kept)
    assert fetched["spec"]["backfill_days"] is None


async def test_a_re_apply_settles_the_binding_on_the_streams_it_names(db: None) -> None:
    """A binding's name derives from provider, account and tenant URL alone, so its streams are
    what it carries rather than which binding it is: a submit naming a different set is the same
    binding with different content, never a second one, and the member ends with exactly the
    streams they name.

    Dropping `stories` removes its row and tombstones its pages — the one act that clears what a
    stream synced, which is why the whole binding had to be deleted before. Adding `users` takes
    the binding's own disclosure and owner. Naming `stories` again revives its single row, since
    the row id derives from the config: no stream ever holds two."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, ASANA, "acct-one")
    ctx = _context(state, grants, brokered=(ASANA,))
    name = binding_name(ASANA, "acct-one", None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(ASANA, ("stories", "tasks"), name))
        before = {str(row["config"]["stream"]): row for row in await _rows(state, ASANA)}
        page_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=before["stories"]["id"],
                    digest="sha256:x",
                    body_ref="pages/x",
                    subject=member_subject(state.owner_id),
                    tombstone=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        narrowed = await _apply(ctx, _manifest_text(ASANA, ("tasks", "users"), name))
        rows = {str(row["config"]["stream"]): row for row in await _rows(state, ASANA)}
        async with workspace_tx() as connection:
            tombstone = (
                await connection.execute(
                    sa.select(tables.page.c.tombstone).where(tables.page.c.id == page_id)
                )
            ).scalar_one()
        fetched = await _get(ctx, name)
        readded = await _apply(ctx, _manifest_text(ASANA, ("stories", "tasks", "users"), name))
        revived = {str(row["config"]["stream"]): row for row in await _rows(state, ASANA)}
    assert narrowed == {"kind": SOURCE_KIND, "name": name, "result": "updated"}
    assert sorted(rows) == ["stories", "tasks", "users"]
    assert rows["stories"]["removed_at"] is not None
    assert tombstone is True or tombstone == 1
    assert rows["tasks"] == before["tasks"]
    assert rows["users"]["removed_at"] is None
    assert rows["users"]["subject"] == member_subject(state.owner_id)
    assert rows["users"]["owner_member_id"] == state.owner_id
    assert rows["users"]["connection_id"] == before["tasks"]["connection_id"]
    assert fetched["spec"]["streams"] == ["tasks", "users"]
    assert readded["result"] == "updated"
    assert sorted(revived) == ["stories", "tasks", "users"]
    assert revived["stories"]["id"] == before["stories"]["id"]
    assert revived["stories"]["removed_at"] is None


async def test_narrowing_a_live_bindings_window_is_delete_and_recreate(db: None) -> None:
    """Lowering `backfill_days` is refused where raising it is not, and the asymmetry is the point:
    the pages between the old floor and the narrower one would be stranded live — never revisited,
    never tombstoned, because mail is not `delete_missing`. Only delete-and-recreate tombstones
    them, so that is the way out the refusal names. A binding already reaching all history refuses
    every finite window for the same reason."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    delete_tool = _TOOLS["object_delete"]
    apply_tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=90))
        args = apply_tool.input_model.model_validate(
            {
                "manifest": _manifest_text(GMAIL, ("messages",), name, backfill_days=7),
            }
        )
        with pytest.raises(VerbNotSupported, match="only ever widens"):
            await apply_tool.handler(ctx, args)
        [unchanged] = await _rows(state, GMAIL)
        assert unchanged["config"]["backfill_days"] == 90
        # all history is the widest there is, so every finite window narrows it
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days="all"))
        narrowing = apply_tool.input_model.model_validate(
            {
                "manifest": _manifest_text(GMAIL, ("messages",), name, backfill_days=365),
            }
        )
        with pytest.raises(VerbNotSupported, match="already reaches all history"):
            await apply_tool.handler(ctx, narrowing)
        await delete_tool.handler(
            ctx,
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
        )
        recreated = await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=7))
    assert recreated["result"] == "created"
    [row] = await _rows(state, GMAIL)
    assert row["removed_at"] is None
    assert row["config"]["backfill_days"] == 7


async def test_the_window_belongs_to_what_identifies_a_submitted_binding(db: None) -> None:
    """A submit that differs from the binding's own read-back spec only in the window has to reach
    the window path rather than the no-op an identity-equal submit takes, and a
    resync carrying a changed window is refused whole rather than run while ignoring what it asked
    for. Both ride on the window's membership in the submitted binding's identity. The narrowing
    submit is the one that proves it reached the window path at all, since a widening one would
    have been applied and a submit that never reached it would be a silent no-op."""
    state = await _workspace()
    grants = GrantStore()
    await _grant(state, grants, GMAIL, "acct-one")
    ctx = _context(state, grants, brokered=(GMAIL,))
    name = binding_name(GMAIL, "acct-one", None)
    apply_tool = _TOOLS["object_apply"]
    narrowed = _manifest_text(GMAIL, ("messages",), name, account_id="acct-one", backfill_days=7)
    resynced = _manifest_text(
        GMAIL, ("messages",), name, account_id="acct-one", backfill_days=7, resync=True
    )
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(GMAIL, ("messages",), name, backfill_days=90))
        for submitted, refusal in ((narrowed, "only ever widens"), (resynced, "resync changes")):
            args = apply_tool.input_model.model_validate({"manifest": submitted})
            with pytest.raises(VerbNotSupported, match=refusal):
                await apply_tool.handler(ctx, args)
    [row] = await _rows(state, GMAIL)
    assert row["config"]["backfill_days"] == 90


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
                    get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
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
        stored = yaml.safe_load(
            (
                await _TOOLS["object_get"].handler(
                    owner_ctx,
                    _TOOLS["object_get"].input_model.model_validate(
                        {"kind": SOURCE_KIND, "name": name}
                    ),
                )
            )
            .content[0]
            .text
        )
        assert stored["spec"]["resync"] is False  # an act, never state


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
                    ctx,
                    list_tool.input_model.model_validate({"kind": SOURCE_KIND}),
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
    assert row["config"] == {
        "account": "default",
        "stream": "jobs",
        "base_url": None,
        "backfill_days": None,
        "backfill_after": None,
    }


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
            "manifest": _manifest_text(GREENHOUSE, ("jobs",), "greenhouse-unkeyed"),
        }
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError) as refusal:
            await tool.handler(ctx, args)
    assert str(refusal.value) == (
        "add the 'greenhouse' credential before registering its sources "
        "(the credential collection's request_credentials action for slot 'greenhouse')"
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
        (
            "quickbooks",
            "https://quickbooks.api.intuit.com/v3/company/9130347596/",
            "https://quickbooks.api.intuit.com/v3/company/9130347596",
        ),
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
        ("quickbooks", "https://quickbooks.api.intuit.com.evil.test/v3/company/9130347596"),
        ("quickbooks", "https://quickbooks.api.intuit.com/v3/company"),
        ("quickbooks", "https://quickbooks.api.intuit.com/v3/company/9130347596/query"),
        ("quickbooks", "http://quickbooks.api.intuit.com/v3/company/9130347596"),
        ("recruitee", "https://api.recruitee.com/c/acme?redirect=evil"),
        ("salesforce", "https://evil.test"),
        ("zendesk", "https://attacker@acme.zendesk.com"),
    ],
)
def test_tenant_urls_reject_cross_provider_and_unsafe_origins(provider: str, base_url: str) -> None:
    with pytest.raises(ValueError):
        _validated_base_url(provider, base_url)


def test_quickbooks_requires_the_company_its_address_names() -> None:
    """QBO addresses one company file per request and no broker holds that company id, so a binding
    without the whole address could never run — it is refused at registration, where the agent can
    ask the member for it, rather than failing every sync afterwards. A provider whose host is
    complete still refuses any override."""
    with pytest.raises(ValueError, match="requires base_url"):
        _validated_base_url("quickbooks", None)
    with pytest.raises(ValueError, match="requires base_url"):
        _validated_base_url("quickbooks", "")
    with pytest.raises(ValueError, match="fixed API host"):
        _validated_base_url("asana", "https://app.asana.com/api/1.0")
    with pytest.raises(ValueError, match="requires base_url"):
        _validated_base_url("zendesk", None)


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
        _context(state, None, no_speaker=True),
        turn=_context(state, None, no_speaker=True).turn.model_copy(
            update={"on_behalf_of_member_id": state.member_id}
        ),
    )
    delete_tool = _TOOLS["object_delete"]
    member_name = binding_name(GREENHOUSE, DIRECT_ACCOUNT, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(GREENHOUSE, ("jobs",), member_name))
        with pytest.raises(SpeakerRequired):
            await delete_tool.handler(
                speakerless,
                delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": member_name}),
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
    its binding name + row id — a trigger names a source that already exists, so this sets one up
    without the connect/grant dance registration proper needs."""
    with ws(state.workspace_id), agent(state.agent_id):
        ext = context_for(NAME, DECLARED_PROVIDERS)
        source_id = await ext.register_source(
            ASANA,
            ConnectorSourceConfig(account=account, stream=stream),
            subject=subject,
            owner_member_id=owner,
        )
    return binding_name(ASANA, account, None), source_id


async def _woken(state: _Workspace, name: str) -> dict[UUID, UUID]:
    """Which conversation each trigger on this binding wakes, and as which agent — the durable
    state an apply writes and a delete takes away, read workspace-wide the way the alert does."""
    with ws(state.workspace_id):
        triggers = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(name)
    return {row.conversation_id: row.agent_id for row in triggers}


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


def _trigger_manifest(source: str, conversation_id: UUID, *, delivery: str = "current") -> str:
    return yaml.safe_dump(
        {
            "kind": SOURCE_TRIGGER_KIND,
            "name": trigger_name(source, conversation_id),
            "spec": {"source": source, "delivery": delivery},
        }
    )


def _change(
    source_id: UUID,
    body: str,
    changed_at: datetime | None = None,
    subject: str = SHARED_SUBJECT,
    stream: str = "tasks",
    title: str = "",
    disposition: str = "added",
    page_id: UUID | None = None,
    revision: int = 1,
) -> PageChange:
    """One replayed page. `disposition` shapes the three fields the alert reads it from: a removed
    page is tombstoned, an added page carries equal create/change stamps, an updated page's change
    stamp is later — exactly what `SyncDriver._write` leaves behind."""
    now = changed_at or datetime(2026, 7, 20, tzinfo=UTC)
    created = now - timedelta(days=1) if disposition == "updated" else now
    return PageChange(
        page_id=page_id or uuid4(),
        source_id=source_id,
        subject=subject,
        stream=stream,
        title=title or body.removeprefix("# ")[:40],
        body=body,
        digest=f"sha256:{uuid4().hex}",
        revision=revision,
        tombstone=disposition == "removed",
        created_at=created,
        as_of=now,
        changed_at=now,
    )


async def test_a_trigger_wakes_its_own_conversation_until_it_is_deleted(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        watched = await _apply(
            _context(state, None), _trigger_manifest(name, state.conversation_id)
        )
        assert watched == {
            "kind": SOURCE_TRIGGER_KIND,
            "name": trigger_name(name, state.conversation_id),
            "result": "created",
        }
        assert await _woken(state, name) == {state.conversation_id: state.agent_id}

        get_tool = _TOOLS["object_get"]
        fetched = yaml.safe_load(
            (
                await get_tool.handler(
                    _context(state, None),
                    get_tool.input_model.model_validate(
                        {
                            "kind": SOURCE_TRIGGER_KIND,
                            "name": trigger_name(name, state.conversation_id),
                        }
                    ),
                )
            )
            .content[0]
            .text
        )
        assert fetched["spec"] == {"source": name, "delivery": "current"}
        assert fetched["status"]["source"] == name
        assert fetched["status"]["conversation"] == str(state.conversation_id)
        assert {(link["relation"], link["target"]["name"]) for link in fetched["links"]} == {
            ("watches", name),
            ("reports_to", str(state.conversation_id)),
        }

        delete_tool = _TOOLS["object_delete"]
        await delete_tool.handler(
            _context(state, None),
            delete_tool.input_model.model_validate(
                {
                    "kind": SOURCE_TRIGGER_KIND,
                    "name": trigger_name(name, state.conversation_id),
                }
            ),
        )
        assert await _woken(state, name) == {}


async def test_a_non_owner_may_watch_a_shared_source(db: None) -> None:
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        watched = await _apply(
            _context(state, None, speaker_id=state.member_id),
            _trigger_manifest(name, state.conversation_id),
        )
        assert watched["result"] == "created"
        assert await _woken(state, name) == {state.conversation_id: state.agent_id}


async def test_a_trigger_is_named_for_the_pair_it_is(db: None) -> None:
    """The name derives from the source and the conversation, so a submit under any other name is
    refused with the exact one to use rather than filed as a second row over the same pair."""
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match=trigger_name(name, state.conversation_id)):
            await tool.handler(
                _context(state, None),
                tool.input_model.model_validate(
                    {
                        "manifest": yaml.safe_dump(
                            {
                                "kind": SOURCE_TRIGGER_KIND,
                                "name": "whatever-i-please",
                                "spec": {"source": name},
                            }
                        ),
                    }
                ),
            )
        assert await _woken(state, name) == {}


async def test_applying_another_conversations_trigger_name_reports_no_success(db: None) -> None:
    """The name names the conversation, so its own creator re-applying it from somewhere else is
    refused with the name to use — never answered `updated` for a conversation no row was written
    for, which is what the member would be told had been watched."""
    state = await _workspace()
    name, _ = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    elsewhere = await _second_conversation(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        base = _context(state, None)
        from_elsewhere = replace(
            base, turn=base.turn.model_copy(update={"conversation_id": elsewhere})
        )
        with pytest.raises(ValueError, match=trigger_name(name, elsewhere)):
            await tool.handler(
                from_elsewhere,
                tool.input_model.model_validate(
                    {
                        "manifest": _trigger_manifest(name, state.conversation_id),
                    }
                ),
            )
        assert await _woken(state, name) == {state.conversation_id: state.agent_id}


async def test_a_private_source_cannot_be_watched_and_a_strangers_is_unknown(db: None) -> None:
    """A source private to member M reaches no other reader, so its changes could wake nobody: M is
    told exactly that, while a stranger is told the source does not exist at all — the refusal a
    guessed binding name earns."""
    state = await _workspace()
    name, _ = await _register(state, subject=member_subject(state.member_id), owner=state.member_id)
    stranger = await _stranger(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="syncs privately"):
            await tool.handler(
                _context(state, None, speaker_id=state.member_id),
                tool.input_model.model_validate(
                    {
                        "manifest": _trigger_manifest(name, state.conversation_id),
                    }
                ),
            )
        with pytest.raises(UnknownObject, match=name):
            await tool.handler(
                _context(state, None, speaker_id=stranger),
                tool.input_model.model_validate(
                    {
                        "manifest": _trigger_manifest(name, state.conversation_id),
                    }
                ),
            )
        assert await _woken(state, name) == {}


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
                    get_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
                )
            )
            .content[0]
            .text
        )
    assert fetched["spec"]["provider"] == ASANA
    assert fetched["status"] is None
    [row] = await _rows(state, ASANA)
    assert row["removed_at"] is not None


def _delete_source_before_create(state: _Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Have the registrar's delete land between the trigger verb's read of the binding and the row
    it writes, so the write commits against a source whose rows are already gone."""
    real_create = SourceTriggerStore.create
    delete_tool = _TOOLS["object_delete"]

    async def delete_before_create(
        store: SourceTriggerStore,
        conversation_id: UUID,
        binding: str,
        delivery: str,
        created_by_member_id: UUID | None = None,
    ) -> SourceTrigger:
        await delete_tool.handler(
            _context(state, None),
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": binding}),
        )
        return await real_create(
            store,
            conversation_id,
            binding,
            delivery,
            created_by_member_id=created_by_member_id,
        )

    monkeypatch.setattr(SourceTriggerStore, "create", delete_before_create)


async def test_a_trigger_strands_nothing_on_a_source_removed_mid_verb(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trigger stands only while its source does: one written after the registrar's delete
    refuses and leaves nothing behind. A row outliving its source would state a wake-up on a
    source nobody can reach and that nothing will ever fire."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    _delete_source_before_create(state, monkeypatch)

    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(UnknownObject, match=name):
            await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        assert await _woken(state, name) == {}
        await on_page_change(
            HookContext(
                ext=context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id)),
                payload=PageChangeBatch(changes=(_change(source_id, "# asana tasks: dropped"),)),
            )
        )
        assert await _turns(state.conversation_id) == []
    [row] = await _rows(state, ASANA)
    assert row["removed_at"] is not None


async def test_removing_a_source_takes_its_triggers_and_a_revival_inherits_none(
    db: None,
) -> None:
    """A source name derives from its (provider, account, base_url) identity, so re-registering that
    identity — the documented recreate path — revives the same name. The removal took every trigger
    on it, so the revived binding arrives unwatched and a change on its rows wakes nobody."""
    state = await _workspace()
    name, _removed = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        assert await _woken(state, name) == {state.conversation_id: state.agent_id}
        await delete_tool.handler(
            _context(state, None),
            delete_tool.input_model.model_validate({"kind": SOURCE_KIND, "name": name}),
        )
        assert await _woken(state, name) == {}

    revived_name, revived_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await on_page_change(
            HookContext(
                ext=context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id)),
                payload=PageChangeBatch(changes=(_change(revived_id, "# asana tasks: revived"),)),
            )
        )
        assert await _woken(state, name) == {}
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


async def test_page_change_alerts_only_woken_conversations_idempotently(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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


async def test_per_page_delivery_keeps_one_conversation_per_page(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    first_page = uuid4()
    second_page = uuid4()
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, None),
            _trigger_manifest(name, state.conversation_id, delivery="per_page"),
        )
        [trigger] = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(name)
        assert trigger.delivery == "per_page"
        fetched = yaml.safe_load(
            (
                await _TOOLS["object_get"].handler(
                    _context(state, None),
                    _TOOLS["object_get"].input_model.model_validate(
                        {
                            "kind": SOURCE_TRIGGER_KIND,
                            "name": trigger_name(name, state.conversation_id),
                        }
                    ),
                )
            )
            .content[0]
            .text
        )
        assert fetched["spec"] == {"source": name, "delivery": "per_page"}
        assert fetched["status"]["delivery"] == "per_page"
        assert [link["relation"] for link in fetched["links"]] == ["watches"]
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        first = _change(source_id, "# first", page_id=first_page)
        second = _change(source_id, "# second", page_id=second_page)
        batch = PageChangeBatch(changes=(first, second))

        await on_page_change(HookContext(ext=ext, payload=batch))
        await on_page_change(HookContext(ext=ext, payload=batch))

    async with workspace_tx() as connection:
        conversations = (
            (
                await connection.execute(
                    sa.select(tables.conversation).where(
                        tables.conversation.c.workspace_id == state.workspace_id,
                        tables.conversation.c.surface == NAME,
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(conversations) == 2
    turns_by_page = {}
    for conversation in conversations:
        [turn] = await _turns(conversation["id"])
        if str(first_page) in turn["inbound"]:
            turns_by_page[first_page] = turn
        if str(second_page) in turn["inbound"]:
            turns_by_page[second_page] = turn
    assert set(turns_by_page) == {first_page, second_page}
    assert (
        turns_by_page[first_page]["conversation_id"]
        != turns_by_page[second_page]["conversation_id"]
    )
    assert await _turns(state.conversation_id) == []

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="done", terminal={"status": "done", "text": "Handled."})
            .where(tables.turn.c.id == turns_by_page[first_page]["id"])
        )
    changed = _change(
        source_id,
        "# first changed",
        changed_at=datetime(2026, 7, 21, tzinfo=UTC),
        disposition="updated",
        page_id=first_page,
        revision=2,
    )
    with ws(state.workspace_id), agent(state.agent_id):
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(changed,))))

    assert len(await _turns(turns_by_page[first_page]["conversation_id"])) == 2
    assert len(await _turns(turns_by_page[second_page]["conversation_id"])) == 1


async def test_multi_stream_binding_alerts_once_per_conversation(db: None) -> None:
    """Two streams of one binding both changing in a batch is one alert, not two — the hook
    aggregates by binding, so distinct per-stream `changed_at` neither split into two turns nor
    collide on one idempotency key and drop a stream."""
    state = await _workspace()
    name, tasks_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    _, projects_id = await _register(
        state, subject=SHARED_SUBJECT, owner=state.owner_id, stream="projects"
    )
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, None),
            _manifest_text(ASANA, ("projects", "tasks"), name, account_id="acct-one", shared=True),
        )
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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


async def _second_conversation(state: _Workspace) -> UUID:
    """Another conversation of the same agent — the second place one member can apply from."""
    conversation_id = uuid4()
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=state.workspace_id,
                agent_id=state.agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=state.owner_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return conversation_id


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


async def test_alert_skips_a_trigger_whose_agent_holds_no_grant(db: None, tmp_path) -> None:
    """Watching is a member act on a shared source, but reading its pages is the agent's own
    grant. A conversation bound to an agent that was never granted the changed source is alerted
    about nothing: no turn, no change log, no page ids anywhere — while the granted trigger in
    the same batch is alerted in full, so the suppression is per-trigger and not a dropped
    batch."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    outsider_agent_id, outsider_conversation_id = await _second_agent_conversation(state)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id):
        with agent(state.agent_id):
            await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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
                _trigger_manifest(name, outsider_conversation_id),
            )
        assert await _woken(state, name) == {
            state.conversation_id: state.agent_id,
            outsider_conversation_id: outsider_agent_id,
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
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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
    """The real runtime seam under the change log."""
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
    """Every line of the one change log written for this binding."""
    carrier = sandboxes.carrier
    assert isinstance(carrier, LocalCarrier)
    log_dir = carrier.ufo_home / RUNTIME_DIRNAME / conversation_id.hex / CHANGE_LOG_DIR / name
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
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, None),
            _manifest_text(ASANA, ("projects", "tasks"), name, account_id="acct-one", shared=True),
        )
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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
        assert (
            f"$UFO_HOME/{RUNTIME_DIRNAME}/{state.conversation_id.hex}/{CHANGE_LOG_DIR}/{name}/"
            in turn["inbound"]
        )
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
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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
    written to a file a reader of the woken conversation cannot open."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
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
    Here the workspace root is a file, so the carrier cannot make the conversation's directory —
    internal state, not external flakiness — so it raises, the cursor stays put for the next tick,
    and no alert claims a delta whose detail was dropped."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        sandboxes.workspace_root.parent.mkdir(parents=True, exist_ok=True)
        sandboxes.workspace_root.write_text("not a directory")
        changes = tuple(_change(source_id, f"# task {n}") for n in range(6))
        with pytest.raises(OSError):
            await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        assert await _turns(state.conversation_id) == []


async def test_alert_degrades_to_counts_when_no_sandbox_is_wired(db: None) -> None:
    """No workspace seam means no change log; the alert still reports what changed and names the
    object_list route rather than losing the turn to plumbing."""
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        changes = tuple(_change(source_id, f"# task {n}") for n in range(6))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        (turn,) = await _turns(state.conversation_id)
        assert "tasks: 6 added" in turn["inbound"]
        assert "object_list page" in turn["inbound"]


async def test_alert_skipped_when_only_member_private_changes(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(private,))))
        assert await _turns(state.conversation_id) == []


async def test_a_trigger_on_an_archived_app_alerts_nothing_and_stops_no_batch(db: None) -> None:
    state = await _workspace()
    name, source_id = await _register(state, subject=SHARED_SUBJECT, owner=state.owner_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, None), _trigger_manifest(name, state.conversation_id))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    name=f"~archived-{state.agent_id}",
                    archived_name=tables.agent.c.name,
                    is_main=False,
                    archived_at=sa.func.now(),
                )
                .where(tables.agent.c.id == state.agent_id)
            )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        batch = PageChangeBatch(changes=(_change(source_id, "# asana tasks: Ship it"),))

        await on_page_change(HookContext(ext=ext, payload=batch))

        assert await _turns(state.conversation_id) == []
        assert [trigger.id for trigger in await SourceTriggerStore(ext).waking(name)]
