"""The `source_trigger` object kind end to end: a conversation's standing interest in one shared
connection's feed, applied and deleted through the object verbs, delivered by the `page_change`
hook, and offered by the link hooks.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable trigger rows, the turns the alert admits, and the change
logs it writes. Covered here: a trigger wakes its own conversation until it is deleted; a private
connection cannot be watched and a stranger's is unknown; the alert reaches only woken
conversations, only with shared pages, only for agents that still hold the grant, and only once per
batch; separate batches with one timestamp stay distinct; a narrowed trigger wakes on its resource
alone under every spelling of the link; and a link a conversation does not watch earns exactly one
offer."""

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import CONNECTION_OBJECT_KIND, PAGE_KIND, PAGE_OBJECT
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.resources import (
    canonical_resource,
    default_clauses,
    resource_clauses,
    resource_scope,
)
from ufo_ext_sources.tools import (
    ALERT_CLOSING,
    CHANGE_LOG_DIR,
    SOURCE_TRIGGER_KIND,
    TRIGGER_SCOPE_HEX,
    WATCH_OFFER_MAX,
    SourceTriggerObjects,
    SourceTriggerSpec,
    on_link_seen,
    on_page_change,
)
from ufo_ext_sources.triggers import SourceTrigger, SourceTriggerStore, source_trigger

from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import RUNTIME_DIRNAME, ProxyEndpoint
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.objects import ObjectListQuery, UnknownObject
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, TurnContext, TurnRuntimeConfig
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import SUBAGENT_SURFACE
from ufo.sdk.grants import account_object_name, feed_connections, provider_label
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    InjectContext,
    PageChangeBatch,
    PostToolUse,
    UserPromptSubmit,
)
from ufo.sdk.sources import ConnectorSourceConfig, PageChange
from ufo.sdk.surfaces import member_message_text
from ufo.sdk.tools import SpeakerRequired, ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ASANA = "asana"
GITHUB = "github"
ACCOUNT = "acct-one"
PR_URL = "https://github.com/acme/ufo/pull/1684"
WATCH_LINE = "The egress retry pull request"
DECLARED_PROVIDERS = frozenset(CONNECTORS)


def _repo(resource: str) -> str:
    """The repository a link names, which is the spelling the offer hands the clause builder."""
    return resource_scope(GITHUB, resource) or ""


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class _Feed:
    id: UUID
    name: str
    provider: str


async def _workspace() -> _Workspace:
    workspace_id = uuid4()
    owner_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
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


async def _stranger(state: _Workspace) -> UUID:
    stranger_id = uuid4()
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=stranger_id,
                workspace_id=state.workspace_id,
                email=f"{stranger_id.hex}@x.test",
                is_admin=False,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return stranger_id


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
    *,
    speaker_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> ToolContext:
    speaker = speaker_id or state.owner_id
    turn_id = uuid4()
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=turn_id,
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=agent_id or state.agent_id,
            seq=1,
            status="running",
            inbound="watch this feed",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker,
        requesting_message_ref=turn_id,
        audience=conversation_audience(speaker),
        artifact_token_secret="",
        grants=None,
        connectors=ConnectorRegistry(entries={}, resolver=None, fallback=None),
        ext=context_for(NAME, DECLARED_PROVIDERS),
    )


async def _connect(
    state: _Workspace,
    *,
    shared: bool = True,
    owner: UUID | None = None,
    account: str = ACCOUNT,
    provider: str = ASANA,
    agent_id: UUID | None = None,
) -> _Feed:
    """One connection, recorded exactly as the connect callback records it — a trigger names a
    connection that already exists, so this sets one up without the OAuth dance."""
    with ws(state.workspace_id), agent(agent_id or state.agent_id):
        connection_id = await GrantStore().record(
            provider=provider,
            account_id=account,
            host=f"api.{provider}.test",
            grantor_member_id=owner or state.owner_id,
            shared=shared,
        )
    return _Feed(id=connection_id, name=account_object_name(provider, account), provider=provider)


async def _stream(
    state: _Workspace, feed: _Feed, *, provider: str = ASANA, stream: str = "tasks"
) -> UUID:
    """One stream of that connection, registered as the connect-time registrar registers it."""
    with ws(state.workspace_id), agent(state.agent_id):
        return await context_for(NAME, DECLARED_PROVIDERS).register_source(
            provider, ConnectorSourceConfig(stream=stream), connection_id=feed.id
        )


async def _feed_with_stream(
    state: _Workspace,
    *,
    shared: bool = True,
    owner: UUID | None = None,
    account: str = ACCOUNT,
    provider: str = ASANA,
    stream: str = "tasks",
) -> tuple[_Feed, UUID]:
    feed = await _connect(state, shared=shared, owner=owner, account=account, provider=provider)
    return feed, await _stream(state, feed, provider=provider, stream=stream)


@dataclass
class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        return None


def _admitting(workspace_id: UUID) -> AdmissionInvoker:
    admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset({"cli"}))
    return AdmissionInvoker(admission=admission, workspace_id=workspace_id)


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
        tool.input_model.model_validate({"ref": f"{SOURCE_TRIGGER_KIND}/{name}"}),
    )
    assert result.is_error is False
    return yaml.safe_load(result.content[0].text)


async def _woken(state: _Workspace, feed: _Feed) -> dict[UUID, UUID]:
    """Which conversation each trigger on this connection wakes, and as which agent — the durable
    state an apply writes and a delete takes away, read workspace-wide the way the alert does."""
    with ws(state.workspace_id):
        triggers = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
    return {row.conversation_id: row.agent_id for row in triggers}


async def _watches(state: _Workspace, feed: _Feed) -> list[list[str]]:
    with ws(state.workspace_id):
        triggers = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
    return sorted(list(row.when) for row in triggers)


async def _clauses_of(state: _Workspace, feed: _Feed) -> list[list[str]]:
    with ws(state.workspace_id):
        triggers = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
    return sorted(list(row.when) for row in triggers)


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


def _on(*streams: str) -> tuple[str, ...]:
    """The clauses that wake on every page of these streams, which is what naming streams meant."""
    return tuple(f"stream == '{stream}'" for stream in streams)


def _name(feed: _Feed, conversation_id: UUID, when: tuple[str, ...] = ()) -> str:
    """A name for a trigger on this feed, the way an agent picks one — stable per feed,
    conversation and clause list, so a test applying one trigger twice asks for one object."""
    clauses = "\n".join(when or default_clauses(feed.provider))
    identity = f"{feed.name}\0{conversation_id}\0{clauses}".encode()
    return f"{feed.name[:55]}-{sha256(identity).hexdigest()[:8]}"


def _trigger_manifest(
    feed: _Feed,
    conversation_id: UUID,
    *,
    delivery: str = "current",
    when: tuple[str, ...] = (),
    paused: bool = False,
    name: str | None = None,
    description: str = "",
) -> str:
    """One manifest as an agent writes it. `when` left empty resolves the way apply does, so the
    name the test asks for is the name apply expects."""
    spec: dict[str, object] = {
        "connection": feed.name,
        "delivery": delivery,
        "when": list(when),
        "paused": paused,
    }
    if description:
        spec["description"] = description
    return yaml.safe_dump(
        {
            "kind": SOURCE_TRIGGER_KIND,
            "name": name or _name(feed, conversation_id, when),
            "spec": spec,
        }
    )


def _watch_manifest(feed: _Feed, conversation_id: UUID, resource: str, **named: object) -> str:
    """The manifest the offer on a link writes: the clauses for that one resource."""
    return _trigger_manifest(
        feed,
        conversation_id,
        when=resource_clauses(feed.provider, resource, _repo(resource)),
        **named,
    )


def test_source_trigger_delivery_is_current_only() -> None:
    with pytest.raises(ValidationError):
        SourceTriggerSpec.model_validate({"connection": "github-account", "delivery": "per_page"})


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
    """One replayed page."""
    now = changed_at or datetime(2026, 7, 20, tzinfo=UTC)
    created = now - timedelta(days=1) if disposition == "updated" else now
    return PageChange(
        page_id=page_id or uuid4(),
        source_id=source_id,
        connection_id=uuid4(),
        provider="github",
        subject=subject,
        stream=stream,
        title=title or body.removeprefix("# ")[:40],
        body=body,
        digest=f"sha256:{uuid4().hex}",
        revision=revision,
        tombstone=disposition == "removed",
        indexed=True,
        created_at=created,
        as_of=now,
        changed_at=now,
    )


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
    sandboxes: ConversationSandbox, conversation_id: UUID, name: str, scope: str | None = None
) -> list[dict]:
    """Every line of the one change log written for this trigger's scope."""
    log_dir = _log_dir(sandboxes, conversation_id, name)
    if scope is None:
        (log_dir,) = sorted(path for path in log_dir.iterdir() if path.is_dir())
    else:
        log_dir = log_dir / scope
    (entry,) = sorted(path for path in log_dir.iterdir() if path.is_file())
    return [json.loads(line) for line in entry.read_text().splitlines()]


def _log_dir(sandboxes: ConversationSandbox, conversation_id: UUID, name: str) -> Path:
    carrier = sandboxes.carrier
    assert isinstance(carrier, LocalCarrier)
    return carrier.ufo_home / RUNTIME_DIRNAME / conversation_id.hex / CHANGE_LOG_DIR / name


def test_manifest_declares_the_trigger_and_page_kinds() -> None:
    declared = manifest()
    assert declared.tools == ()
    assert {kind.name for kind in declared.objects} == {SOURCE_TRIGGER_KIND, PAGE_OBJECT.name}
    assert {hook.event for hook in declared.hooks} == {
        "page_change",
        "connection_recorded",
        "user_prompt_submit",
        "post_tool_use",
    }
    assert {hook.event for hook in declared.hooks if hook.handler is on_link_seen} == {
        "user_prompt_submit",
        "post_tool_use",
    }
    assert {hook.event for hook in declared.hooks if hook.best_effort} == {"user_prompt_submit"}
    assert (
        next(
            hook.page_change_failure_scope for hook in declared.hooks if hook.event == "page_change"
        )
        == "batch"
    )
    assert {slot.name for slot in declared.credentials} == {
        name for name, connector in CONNECTORS.items() if not connector.key_headers
    } | {slot for connector in CONNECTORS.values() for slot in connector.key_headers.values()}
    assert {source.backend for source in declared.sources} == set(CONNECTORS)


async def test_a_trigger_wakes_its_own_conversation_until_it_is_deleted(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    name = _name(feed, state.conversation_id)
    with ws(state.workspace_id), agent(state.agent_id):
        watched = await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        assert watched == {
            "kind": SOURCE_TRIGGER_KIND,
            "name": name,
            "result": "created",
        }
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}

        fetched = await _get(_context(state), name)
        assert fetched["spec"] == {
            "connection": feed.name,
            "description": "",
            "delivery": "current",
            "paused": False,
            "when": list(default_clauses(feed.provider)),
        }
        assert fetched["status"]["connection"] == feed.name
        assert fetched["status"]["conversation"] == str(state.conversation_id)
        assert {(link["relation"], link["target"]) for link in fetched["links"]} == {
            ("watches", f"{CONNECTION_OBJECT_KIND}/{feed.name}"),
            ("reports_to", f"conversation/{state.conversation_id}"),
        }

        delete_tool = _TOOLS["object_delete"]
        await delete_tool.handler(
            _context(state),
            delete_tool.input_model.model_validate(
                {"kind": SOURCE_TRIGGER_KIND, "name": name},
            ),
        )
        assert await _woken(state, feed) == {}


async def test_a_paused_trigger_keeps_its_row_and_wakes_nothing_until_it_resumes(db: None) -> None:
    """Pausing is what stops a trigger without losing it: the sweep's read drops it, the row still
    lists and reads back as paused, and resuming puts it back on the next batch."""
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    name = _name(feed, state.conversation_id)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}

        paused = await _apply(
            _context(state), _trigger_manifest(feed, state.conversation_id, paused=True)
        )
        assert paused["result"] == "updated"
        assert await _woken(state, feed) == {}
        fetched = await _get(_context(state), name)
        assert fetched["spec"]["paused"] is True
        assert fetched["status"]["paused"] is True

        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}
        assert (await _get(_context(state), name))["spec"]["paused"] is False


async def test_a_trigger_pauses_after_its_stream_stops_syncing(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    retired = await _stream(state, feed, stream="projects")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state), _trigger_manifest(feed, state.conversation_id, when=_on("projects"))
        )
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.source).where(tables.source.c.uid == retired))
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, when=_on("projects"), paused=True),
        )
        assert await _woken(state, feed) == {}


async def test_a_new_trigger_cannot_be_applied_already_paused(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="watches from the moment"):
            await tool.handler(
                _context(state),
                tool.input_model.model_validate(
                    {"manifest": _trigger_manifest(feed, state.conversation_id, paused=True)}
                ),
            )
        assert await _woken(state, feed) == {}


async def test_an_admin_pauses_another_members_trigger_but_never_re_points_it(db: None) -> None:
    """Pausing a trigger is management, which an admin already holds through the delete gate."""
    state = await _workspace()
    feed, _ = await _feed_with_stream(state, provider=GITHUB, stream="pull_requests")
    name = _name(feed, state.conversation_id)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, speaker_id=state.member_id),
            _trigger_manifest(feed, state.conversation_id),
        )
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, paused=True),
        )
        assert await _woken(state, feed) == {}

        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, paused=False),
        )
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}

        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, paused=True),
        )

        with pytest.raises(ValueError, match="delete this one and apply another"):
            await tool.handler(
                _context(state),
                tool.input_model.model_validate(
                    {"manifest": _watch_manifest(feed, state.conversation_id, PR_URL, name=name)}
                ),
            )


async def test_source_trigger_portal_actions_match_the_mutation_gate(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    name = _name(feed, state.conversation_id)
    objects = SourceTriggerObjects()
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state, speaker_id=state.member_id),
            _trigger_manifest(feed, state.conversation_id),
        )
        member = await objects.member_detail(
            context_for(NAME, DECLARED_PROVIDERS),
            name,
            member_id=state.member_id,
            admin=False,
        )
        admin = await objects.member_detail(
            context_for(NAME, DECLARED_PROVIDERS),
            name,
            member_id=state.owner_id,
            admin=True,
        )
        assert member is not None
        assert admin is not None
        assert member.row.fields["pausable"] is True
        assert member.row.fields["resumable"] is False
        assert member.row.fields["deletable"] is True
        assert admin.row.fields["pausable"] is True
        assert admin.row.fields["resumable"] is False
        assert admin.row.fields["deletable"] is True

        page = await objects.member_page(
            context_for(NAME, DECLARED_PROVIDERS),
            member_id=state.owner_id,
            admin=True,
            query=ObjectListQuery(supported_fields=manifest().objects[0].list_fields),
        )
        assert page.rows[0].fields["pausable"] is True
        assert page.rows[0].fields["resumable"] is False
        assert page.rows[0].fields["deletable"] is True

        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, paused=True),
        )
        paused = await objects.member_detail(
            context_for(NAME, DECLARED_PROVIDERS),
            name,
            member_id=state.owner_id,
            admin=True,
        )
        assert paused is not None
        assert paused.row.fields["pausable"] is False
        assert paused.row.fields["resumable"] is True
        assert paused.row.fields["deletable"] is True


async def test_a_member_who_neither_created_nor_administers_cannot_pause(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    stranger = await _stranger(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        with pytest.raises(UnknownObject):
            await tool.handler(
                _context(state, speaker_id=stranger),
                tool.input_model.model_validate(
                    {"manifest": _trigger_manifest(feed, state.conversation_id, paused=True)}
                ),
            )
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}


async def test_stored_delivery_cannot_change_current_trigger_behavior(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    created_at = datetime(2026, 7, 20, tzinfo=UTC)
    stored_trigger = sa.table(
        "source_trigger",
        sa.column("id", sa.Uuid),
        sa.column("workspace_id", sa.Uuid),
        sa.column("conversation_id", sa.Uuid),
        sa.column("agent_id", sa.Uuid),
        sa.column("connection_id", sa.Uuid),
        sa.column("name", sa.Text),
        sa.column("when", sa.Text),
        sa.column("delivery", sa.Text),
        sa.column("created_by_member_id", sa.Uuid),
        sa.column("internet_access", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(stored_trigger).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                conversation_id=state.conversation_id,
                agent_id=state.agent_id,
                connection_id=feed.id,
                name=f"seeded-{uuid4().hex[:8]}",
                when=json.dumps(list(default_clauses(feed.provider))),
                delivery="per_page",
                created_by_member_id=state.owner_id,
                internet_access=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )

    with ws(state.workspace_id):
        (trigger,) = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)

    assert trigger.delivery == "current"
    assert trigger.internet_access is None


async def test_a_trigger_maps_stored_inheritance_to_no_runtime_ceiling(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(source_trigger)
            .where(source_trigger.c.workspace_id == state.workspace_id)
            .values(internet_access=True)
        )
    with ws(state.workspace_id):
        [trigger] = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
    assert trigger.internet_access is None


async def test_a_trigger_requires_an_exact_member_requester(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    unbound = replace(
        _context(state),
        speaker_member_id=None,
        audience=SHARED_AUDIENCE,
        other_members_active=True,
        member_messages_active=True,
    )
    selected = replace(
        _context(state, speaker_id=state.member_id),
        audience=SHARED_AUDIENCE,
        other_members_active=True,
        member_messages_active=True,
    )
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(SpeakerRequired, match="requested_by"):
            await _apply(unbound, _trigger_manifest(feed, state.conversation_id))
        assert await _woken(state, feed) == {}
        watched = await _apply(selected, _trigger_manifest(feed, state.conversation_id))
        assert watched["result"] == "created"
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}
        [trigger] = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
        assert trigger.created_by_member_id == state.member_id


async def test_a_private_connection_cannot_be_watched_and_a_strangers_is_unknown(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state, shared=False, owner=state.member_id)
    stranger = await _stranger(state)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(ValueError, match="syncs privately"):
            await tool.handler(
                _context(state, speaker_id=state.member_id),
                tool.input_model.model_validate(
                    {"manifest": _trigger_manifest(feed, state.conversation_id)}
                ),
            )
        with pytest.raises(UnknownObject, match=feed.name):
            await tool.handler(
                _context(state, speaker_id=stranger),
                tool.input_model.model_validate(
                    {"manifest": _trigger_manifest(feed, state.conversation_id)}
                ),
            )
        assert await _woken(state, feed) == {}


async def test_a_trigger_cannot_land_on_a_connection_disconnected_mid_verb(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trigger names its connection by foreign key, so one written after the disconnect commits
    is refused by the database rather than filed against a feed nobody can reach."""
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    real_create = SourceTriggerStore.create

    async def disconnect_before_create(
        store: SourceTriggerStore,
        conversation_id: UUID,
        connection_id: UUID,
        delivery: str,
        created_by_member_id: UUID,
        name: str,
        when: tuple[str, ...] = (),
        description: str = "",
        internet_access: Literal[False] | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SourceTrigger:
        assert await GrantStore().disconnect(connection_id, actor_member_id=state.owner_id) is True
        return await real_create(
            store,
            conversation_id,
            connection_id,
            delivery,
            created_by_member_id=created_by_member_id,
            name=name,
            when=when,
            description=description,
            internet_access=internet_access,
            requesting_message_ref=requesting_message_ref,
        )

    monkeypatch.setattr(SourceTriggerStore, "create", disconnect_before_create)

    with ws(state.workspace_id), agent(state.agent_id):
        with pytest.raises(IntegrityError):
            await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        assert await _woken(state, feed) == {}
        await on_page_change(
            HookContext(
                ext=context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id)),
                payload=PageChangeBatch(changes=(_change(source_id, "# asana tasks: dropped"),)),
            )
        )
        assert await _turns(state.conversation_id) == []


async def test_page_change_alerts_only_woken_conversations_idempotently(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await ext.invoke(
            state.conversation_id,
            state.agent_id,
            "Existing work.",
            "existing-work",
        )
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        legal = _change(source_id, "# asana tasks: Follow up with legal")
        stray = _change(uuid4(), "# folder: untracked")
        batch = PageChangeBatch(changes=(shipped, legal, stray))
        await on_page_change(HookContext(ext=ext, payload=batch))
        turns = await _turns(state.conversation_id)
        assert len(turns) == 2
        turn = next(row for row in turns if feed.name in row["inbound"])
        assert turn["speaker_member_id"] is None
        assert TurnContext.model_validate(turn["context"]).requesting_message_ref is not None
        assert TurnRuntimeConfig.model_validate(turn["runtime_config"]) == TurnRuntimeConfig()
        assert turn["fired_by_kind"] == SOURCE_TRIGGER_KIND
        assert turn["fired_by_name"] == _name(feed, state.conversation_id)
        assert turn["fired_by_title"].startswith(feed.name)
        assert turn["fired_by_provider"] == ASANA
        assert "tasks: 2 added on connection" in turn["inbound"]
        assert f"{PAGE_KIND}/{shipped.page_id}" in turn["inbound"]
        assert f"{PAGE_KIND}/{legal.page_id}" in turn["inbound"]

        await on_page_change(HookContext(ext=ext, payload=batch))
        assert len(await _turns(state.conversation_id)) == 2


async def test_alert_opens_on_what_changed_and_asks_for_no_member_report(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        legal = _change(source_id, "# asana tasks: Follow up with legal")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(shipped, legal)))
        )

        (turn,) = await _turns(state.conversation_id)
        headline, opened, counts, refs, closing, closed = turn["inbound"].splitlines()
        assert headline == (
            f"{provider_label(ASANA)} update: asana tasks: Ship the launch list; "
            "asana tasks: Follow up with legal"
        )
        assert member_message_text(turn["inbound"]) == headline
        assert (opened, closed) == ("<agent_detail>", "</agent_detail>")
        assert counts.startswith(f"tasks: 2 added on connection {feed.name} ")
        assert refs.endswith(f"{PAGE_KIND}/{shipped.page_id}; {PAGE_KIND}/{legal.page_id}.")
        assert closing == ALERT_CLOSING
        assert "tell the member" not in turn["inbound"]


async def test_a_trigger_narrowed_to_a_stream_wakes_on_that_stream_alone(db: None) -> None:
    state = await _workspace()
    feed, tasks = await _feed_with_stream(state, stream="tasks")
    projects = await _stream(state, feed, stream="projects")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, when=_on("tasks")),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        watched = _change(tasks, "# asana tasks: Ship the launch list", stream="tasks")
        ignored = _change(projects, "# asana projects: Q3 roadmap", stream="projects")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(watched, ignored)))
        )

        (turn,) = await _turns(state.conversation_id)
        assert f"{PAGE_KIND}/{watched.page_id}" in turn["inbound"]
        assert str(ignored.page_id) not in turn["inbound"]


async def test_a_stream_narrowed_trigger_does_not_wake_on_another_stream_at_all(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state, stream="tasks")
    projects = await _stream(state, feed, stream="projects")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, when=_on("tasks")),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(_change(projects, "# asana projects: Q3 roadmap", stream="projects"),)
                ),
            )
        )

        assert await _turns(state.conversation_id) == []


async def test_one_trigger_watches_two_streams_and_not_a_third(db: None) -> None:
    state = await _workspace()
    feed, tasks = await _feed_with_stream(state, stream="tasks")
    projects = await _stream(state, feed, stream="projects")
    goals = await _stream(state, feed, stream="goals")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, when=_on("projects", "tasks")),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        listed = _change(tasks, "# asana tasks: Ship the launch list", stream="tasks")
        roadmap = _change(projects, "# asana projects: Q3 roadmap", stream="projects")
        unwatched = _change(goals, "# asana goals: Grow revenue", stream="goals")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(listed, roadmap, unwatched)))
        )

        (turn,) = await _turns(state.conversation_id)
        assert f"{PAGE_KIND}/{listed.page_id}" in turn["inbound"]
        assert f"{PAGE_KIND}/{roadmap.page_id}" in turn["inbound"]
        assert str(unwatched.page_id) not in turn["inbound"]


async def test_a_stream_narrowed_trigger_reports_the_run_it_woke(db: None) -> None:
    """The listed name spells the streams, so a fire that stamped the turn with any other name would
    leave every stream-narrowed trigger reading as never run, however often it ran."""
    state = await _workspace()
    feed, tasks = await _feed_with_stream(state, stream="tasks")
    narrowed = _name(feed, state.conversation_id, _on("tasks"))
    with ws(state.workspace_id), agent(state.agent_id):
        ctx = _context(state)
        await _apply(ctx, _trigger_manifest(feed, state.conversation_id, when=_on("tasks")))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(_change(tasks, "# asana tasks: Ship the launch list", stream="tasks"),)
                ),
            )
        )

        (turn,) = await _turns(state.conversation_id)
        assert turn["fired_by_name"] == narrowed
        tool = _TOOLS["object_list"]
        result = await tool.handler(
            ctx, tool.input_model.model_validate({"kind": SOURCE_TRIGGER_KIND})
        )
        assert result.is_error is False
        (row,) = json.loads(result.content[0].text)["objects"]

    assert row["name"] == narrowed
    assert row["last_run_at"] == turn["created_at"].replace(tzinfo=UTC).isoformat()


async def test_one_conversation_watches_two_streams_of_one_connection(db: None) -> None:
    state = await _workspace()
    feed, tasks = await _feed_with_stream(state, stream="tasks")
    projects = await _stream(state, feed, stream="projects")
    with ws(state.workspace_id), agent(state.agent_id):
        ctx = _context(state)
        await _apply(ctx, _trigger_manifest(feed, state.conversation_id, when=_on("tasks")))
        await _apply(ctx, _trigger_manifest(feed, state.conversation_id, when=_on("projects")))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(
                        _change(tasks, "# asana tasks: Ship the launch list", stream="tasks"),
                        _change(projects, "# asana projects: Q3 roadmap", stream="projects"),
                    )
                ),
            )
        )

        assert len(await _turns(state.conversation_id)) == 2


async def test_a_stream_narrowed_trigger_reads_back_its_streams_and_re_applies(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state, stream="tasks")
    await _stream(state, feed, stream="projects")
    manifest = _trigger_manifest(feed, state.conversation_id, when=_on("projects", "tasks"))
    with ws(state.workspace_id), agent(state.agent_id):
        ctx = _context(state)
        assert (await _apply(ctx, manifest))["result"] == "created"

        narrowed = _name(feed, state.conversation_id, _on("projects", "tasks"))
        fetched = await _get(ctx, narrowed)
        assert fetched["spec"] == {
            "connection": feed.name,
            "description": "",
            "delivery": "current",
            "paused": False,
            "when": ["stream == 'projects'", "stream == 'tasks'"],
        }
        assert fetched["status"]["when"] == ["stream == 'projects'", "stream == 'tasks'"]

        assert (await _apply(ctx, manifest))["result"] == "updated"
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}


async def test_a_shared_trigger_fires_under_the_agents_connections_without_a_human_principal(
    db: None,
) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    _other, other_source_id = await _feed_with_stream(state, account="acct-two")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.id == state.conversation_id)
            .values(member_id=None, audience=str(SHARED_AUDIENCE))
        )
    ctx = _context(state, speaker_id=state.member_id)
    ctx = replace(
        ctx,
        audience=SHARED_AUDIENCE,
        turn=ctx.turn.model_copy(
            update={"runtime_config": TurnRuntimeConfig(internet_access=False)}
        ),
    )
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _trigger_manifest(feed, state.conversation_id))
        [stored] = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
        assert stored.created_by_member_id == state.member_id
        assert stored.internet_access is False
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(_change(source_id, "# shared"),)),
            )
        )

    [turn] = await _turns(state.conversation_id)
    assert turn["speaker_member_id"] is None
    assert turn["member_id"] == state.member_id
    assert TurnRuntimeConfig.model_validate(turn["runtime_config"]) == TurnRuntimeConfig(
        internet_access=False
    )
    fired = replace(
        _context(state),
        turn=Turn.model_validate(dict(turn)),
        speaker_member_id=None,
        audience=SHARED_AUDIENCE,
        grants=GrantStore(),
    )
    with ws(state.workspace_id), agent(state.agent_id):
        assert set(await fired.connector_accounts(ASANA)) == {ACCOUNT, "acct-two"}
        assert await fired.connector_account(ASANA, account_id="acct-two") == "acct-two"
        assert fired.ext is not None
        readable = await fired.ext.readable_source_ids(fired.source_reader())
        assert readable == frozenset({source_id, other_source_id})
        assert await GrantStore().disconnect(feed.id, actor_member_id=state.owner_id) is True
    replacement = await _connect(state)
    assert replacement.id != feed.id
    replacement_source_id = await _stream(state, replacement)
    with ws(state.workspace_id), agent(state.agent_id):
        assert set(await fired.connector_accounts(ASANA)) == {ACCOUNT, "acct-two"}
        assert fired.ext is not None
        readable = await fired.ext.readable_source_ids(fired.source_reader())
        assert readable == frozenset({other_source_id, replacement_source_id})


async def test_an_unattributed_trigger_is_retired_without_blocking_valid_wakes(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    invalid_conversation = await _conversation_on(state, "cli")
    created_at = datetime(2020, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(source_trigger).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                conversation_id=invalid_conversation,
                agent_id=state.agent_id,
                connection_id=feed.id,
                name=f"seeded-{uuid4().hex[:8]}",
                when=json.dumps(list(default_clauses(feed.provider))),
                delivery="current",
                paused=False,
                created_by_member_id=None,
                internet_access=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(_change(source_id, "# valid"),)),
            )
        )
        remaining = await SourceTriggerStore(ext).waking(feed.id)

    assert await _turns(invalid_conversation) == []
    assert len(await _turns(state.conversation_id)) == 1
    assert [trigger.created_by_member_id for trigger in remaining] == [state.owner_id]


async def test_trigger_capabilities_do_not_depend_on_the_creators_seat(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == state.owner_id)
            .values(seated_at=None)
        )
    with ws(state.workspace_id), agent(state.agent_id):
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(_change(source_id, "# still delivered"),)),
            )
        )

    [turn] = await _turns(state.conversation_id)
    assert turn["status"] == "queued"
    assert "still delivered" in turn["inbound"]


async def test_batches_with_one_timestamp_have_distinct_idempotency_keys(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    changed_at = datetime(2026, 7, 21, tzinfo=UTC)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        first = PageChangeBatch(
            changes=(
                _change(source_id, "# first", changed_at=changed_at, revision=1),
                _change(source_id, "# second", changed_at=changed_at, revision=2),
            )
        )
        second = PageChangeBatch(
            changes=(
                _change(source_id, "# third", changed_at=changed_at, revision=3),
                _change(source_id, "# fourth", changed_at=changed_at, revision=4),
            )
        )
        for batch in (first, first, second, second):
            await on_page_change(HookContext(ext=ext, payload=batch))

    async with workspace_tx() as connection:
        opened = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.conversation)
                .where(
                    tables.conversation.c.workspace_id == state.workspace_id,
                    tables.conversation.c.surface == NAME,
                )
            )
        ).scalar_one()
    turns = await _turns(state.conversation_id)
    assert len(turns) == 2
    assert len({turn["idempotency_key"] for turn in turns}) == 2
    assert any("first" in turn["inbound"] and "second" in turn["inbound"] for turn in turns)
    assert any("third" in turn["inbound"] and "fourth" in turn["inbound"] for turn in turns)
    assert opened == 0


async def _second_agent_conversation(state: _Workspace) -> tuple[UUID, UUID]:
    """A second agent and a conversation bound to it."""
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


async def _revoke(state: _Workspace, agent_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.connector_grant).where(
                tables.connector_grant.c.workspace_id == state.workspace_id,
                tables.connector_grant.c.agent_id == agent_id,
            )
        )


async def test_alert_skips_a_trigger_whose_agent_holds_no_grant(db: None, tmp_path) -> None:
    """Watching is a member act on a shared connection, but reading its pages is the agent's own
    grant, and a grant can go after the trigger is written."""
    state = await _workspace()
    outsider_agent_id, outsider_conversation_id = await _second_agent_conversation(state)
    feed = await _connect(state)
    await _connect(state, agent_id=outsider_agent_id)
    source_id = await _stream(state, feed)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id):
        with agent(state.agent_id):
            await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        with agent(outsider_agent_id):
            base = _context(state, agent_id=outsider_agent_id)
            await _apply(
                replace(
                    base,
                    turn=base.turn.model_copy(update={"conversation_id": outsider_conversation_id}),
                ),
                _trigger_manifest(feed, outsider_conversation_id),
            )
        assert await _woken(state, feed) == {
            state.conversation_id: state.agent_id,
            outsider_conversation_id: outsider_agent_id,
        }

        await _revoke(state, outsider_agent_id)
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        shipped = _change(source_id, "# asana tasks: Ship the launch list")
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(shipped,))))

    (alerted,) = await _turns(state.conversation_id)
    assert f"{PAGE_KIND}/{shipped.page_id}" in alerted["inbound"]
    assert len(await _change_log(sandboxes, state.conversation_id, feed.name)) == 1

    assert await _turns(outsider_conversation_id) == []
    assert not (sandboxes.workspace_root / str(outsider_conversation_id) / CHANGE_LOG_DIR).exists()


async def test_alert_never_surfaces_a_member_private_page(db: None) -> None:
    """A trigger on a shared connection surfaces only shared changes — a member-private page in the
    same batch is neither referenced nor counted, so its existence never leaks."""
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        shared = _change(source_id, "# asana tasks: Ship it")
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(shared, private)))
        )
        (turn,) = await _turns(state.conversation_id)
        assert "tasks: 1 added on connection" in turn["inbound"]
        assert f"{PAGE_KIND}/{shared.page_id}" in turn["inbound"]
        assert str(private.page_id) not in turn["inbound"]


async def test_alert_counts_by_stream_and_never_truncates(db: None, tmp_path) -> None:
    """A batch past the naming bound carries per-stream added/updated counts and the path to the
    change log — never a prefix of page ids and an opaque `+N more`."""
    state = await _workspace()
    feed, tasks_id = await _feed_with_stream(state)
    projects_id = await _stream(state, feed, stream="projects")
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        woke = (
            *(_change(tasks_id, f"# task {n}", stream="tasks") for n in range(3)),
            *(
                _change(tasks_id, f"# stale {n}", stream="tasks", disposition="updated")
                for n in range(4)
            ),
            *(_change(projects_id, f"# kept {n}", stream="projects") for n in range(2)),
        )
        gone = tuple(
            _change(projects_id, f"# gone {n}", stream="projects", disposition="removed")
            for n in range(2)
        )
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(*woke, *gone))))

        (turn,) = await _turns(state.conversation_id)
        headline, *_ = turn["inbound"].splitlines()
        assert headline == f"{provider_label(ASANA)} update: kept 1 and 8 other pages"
        assert "projects: 2 added; tasks: 3 added, 4 updated on connection" in turn["inbound"]
        assert "more" not in turn["inbound"]
        assert not any(str(change.page_id) in turn["inbound"] for change in (*woke, *gone))

        logged = await _change_log(sandboxes, state.conversation_id, feed.name)
        assert (
            f"$UFO_HOME/{RUNTIME_DIRNAME}/{state.conversation_id.hex}/{CHANGE_LOG_DIR}/"
            f"{feed.name}/" in turn["inbound"]
        )
        assert {entry["page"] for entry in logged} == {
            f"{PAGE_KIND}/{change.page_id}" for change in woke
        }
        assert Counter(entry["change"] for entry in logged) == {"added": 5, "updated": 4}
        assert {entry["stream"] for entry in logged} == {"tasks", "projects"}


async def test_change_log_omits_a_member_private_page(db: None, tmp_path) -> None:
    """The shared-only filter governs the log as well as the message — a private page is not
    written to a file a reader of the woken conversation cannot open."""
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        shared = tuple(_change(source_id, f"# task {n}") for n in range(6))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(*shared, private)))
        )

        logged = await _change_log(sandboxes, state.conversation_id, feed.name)
        assert {entry["page"] for entry in logged} == {
            f"{PAGE_KIND}/{change.page_id}" for change in shared
        }


async def test_change_log_failure_propagates_rather_than_degrading(db: None, tmp_path) -> None:
    """A change log that cannot be written fails the batch instead of quietly alerting without
    it."""
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        sandboxes.workspace_root.parent.mkdir(parents=True, exist_ok=True)
        sandboxes.workspace_root.write_text("not a directory")
        changes = tuple(_change(source_id, f"# task {n}") for n in range(6))
        with pytest.raises(OSError):
            await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))

        assert await _turns(state.conversation_id) == []


async def test_alert_skipped_when_only_member_private_changes(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        private = _change(source_id, "# secret", subject=member_subject(state.member_id))
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(private,))))
        assert await _turns(state.conversation_id) == []


def _pull_request_page(number: int, state: str = "MERGED", checks: str | None = None) -> str:
    return _github_page(
        "pull_requests",
        {
            "url": f"https://github.com/acme/ufo/pull/{number}",
            "number": number,
            "state": state,
            "checks": None if checks is None else {"state": checks},
            "title": "Watch the pull request a trigger names",
        },
    )


def _comment_page(identifier: int, path: Literal["issues", "pulls"]) -> str:
    """One comment or review comment, linked to the pull request the way GitHub links it: a comment
    under `issues/<number>`, a review comment under `pulls/<number>`."""
    number = PR_URL.rsplit("/", 1)[1]
    link = "issue_url" if path == "issues" else "pull_request_url"
    return _github_page(
        "comments" if path == "issues" else "review_comments",
        {
            "id": identifier,
            "body": "Looks good.",
            link: f"https://api.github.com/repos/acme/ufo/{path}/{number}",
        },
    )


def _workflow_run_page(identifier: int, *, status: str, conclusion: str | None = None) -> str:
    number = PR_URL.rsplit("/", 1)[1]
    return _github_page(
        "workflow_runs",
        {
            "id": identifier,
            "status": status,
            "conclusion": conclusion,
            "pull_requests": [{"url": f"https://api.github.com/repos/acme/ufo/pulls/{number}"}],
        },
    )


def _github_page(stream: str, record: dict[str, object]) -> str:
    title = record.get("title") or f"{stream}/{record.get('id')}"
    return f"# {GITHUB} {stream}: {title}\n\n{json.dumps(record, sort_keys=True)}"


def _narrowed_manifest(feed: _Feed, conversation_id: UUID, spelled: str) -> str:
    """The manifest the offer on one spelling of a link writes. Every spelling canonicalizes to the
    one resource, so every spelling writes the one clause list and names the one trigger."""
    canonical = canonical_resource(feed.provider, spelled)
    assert canonical is not None
    return _watch_manifest(feed, conversation_id, canonical)


async def _github_feed(state: _Workspace) -> tuple[_Feed, UUID]:
    return await _feed_with_stream(state, provider=GITHUB, stream="pull_requests")


async def test_two_watches_on_one_connection_differ_by_their_clauses_alone(db: None) -> None:
    state = await _workspace()
    feed, _ = await _github_feed(state)
    other = f"{PR_URL.rsplit('/', 1)[0]}/9"
    with ws(state.workspace_id), agent(state.agent_id):
        for resource in (PR_URL, other):
            applied = await _apply(
                _context(state),
                _trigger_manifest(
                    feed,
                    state.conversation_id,
                    when=resource_clauses(GITHUB, resource, _repo(resource)),
                ),
            )
            assert applied["result"] == "created"
        repeated = await _apply(
            _context(state),
            _trigger_manifest(
                feed, state.conversation_id, when=resource_clauses(GITHUB, PR_URL, _repo(PR_URL))
            ),
        )

    assert repeated["result"] == "updated"
    assert await _clauses_of(state, feed) == sorted(
        [
            list(resource_clauses(GITHUB, PR_URL, _repo(PR_URL))),
            list(resource_clauses(GITHUB, other, _repo(other))),
        ]
    )


async def test_a_row_whose_clauses_cannot_compile_pauses_itself(db: None) -> None:
    """A row can reach the hook with clauses that no longer compile — a backfill that narrowed to
    nothing, a revision that wrote a shape this image does not accept."""
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ctx = _context(state)
        second = await _conversation_on(state, "cli")
        elsewhere = replace(ctx, turn=ctx.turn.model_copy(update={"conversation_id": second}))
        await _apply(elsewhere, _trigger_manifest(feed, second))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(source_trigger)
                .where(source_trigger.c.conversation_id == second)
                .values(when="[]")
            )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(
                        _change(
                            pulls,
                            _pull_request_page(1684, state="MERGED"),
                            stream="pull_requests",
                        ),
                    )
                ),
            )
        )
        rows = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).list_reported()
        broken = next(row.trigger for row in rows if row.trigger.conversation_id == second)

    assert broken.paused is True
    assert "at least one clause" in (broken.fault or "")
    assert len(await _turns(state.conversation_id)) == 1


async def test_a_page_that_keeps_meeting_a_clause_wakes_the_conversation_once(db: None) -> None:
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        page_id = uuid4()
        for revision, title in ((1, "green"), (2, "green again"), (3, "and again")):
            await on_page_change(
                HookContext(
                    ext=ext,
                    payload=PageChangeBatch(
                        changes=(
                            _change(
                                pulls,
                                _pull_request_page(1684, state="OPEN", checks="SUCCESS"),
                                stream="pull_requests",
                                page_id=page_id,
                                title=title,
                                revision=revision,
                                disposition="updated",
                            ),
                        )
                    ),
                )
            )

    assert len(await _turns(state.conversation_id)) == 1


async def test_a_page_that_stops_and_starts_meeting_a_clause_wakes_again(db: None) -> None:
    """A push restarts the checks, so the rollup leaves `SUCCESS` and the clause goes unmet; the
    run that follows makes it met again, and that is a second wake."""
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        page_id = uuid4()
        for revision, checks in ((1, "SUCCESS"), (2, "PENDING"), (3, "FAILURE")):
            await on_page_change(
                HookContext(
                    ext=ext,
                    payload=PageChangeBatch(
                        changes=(
                            _change(
                                pulls,
                                _pull_request_page(1684, state="OPEN", checks=checks),
                                stream="pull_requests",
                                page_id=page_id,
                                title=checks,
                                revision=revision,
                                disposition="updated",
                            ),
                        )
                    ),
                )
            )

    assert len(await _turns(state.conversation_id)) == 2


async def test_a_merge_wakes_a_conversation_the_green_checks_already_woke(db: None) -> None:
    """Why the clauses are a list."""
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        page_id = uuid4()
        for revision, pull_state in ((1, "OPEN"), (2, "MERGED")):
            await on_page_change(
                HookContext(
                    ext=ext,
                    payload=PageChangeBatch(
                        changes=(
                            _change(
                                pulls,
                                _pull_request_page(1684, state=pull_state, checks="SUCCESS"),
                                stream="pull_requests",
                                page_id=page_id,
                                title=pull_state,
                                revision=revision,
                                disposition="updated",
                            ),
                        )
                    ),
                )
            )

    turns = await _turns(state.conversation_id)
    assert len(turns) == 2
    assert "MERGED" in turns[1]["inbound"]


async def test_a_tombstone_wakes_nobody_and_drops_what_the_page_met(db: None) -> None:
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    page_id = uuid4()

    def landing(revision: int, *, removed: bool = False) -> PageChange:
        return _change(
            pulls,
            "" if removed else _pull_request_page(1684, state="OPEN", checks="SUCCESS"),
            stream="pull_requests",
            page_id=page_id,
            title="the pull request",
            revision=revision,
            disposition="removed" if removed else "updated",
        )

    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        for change in (landing(1), landing(2, removed=True), landing(3)):
            await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=(change,))))

    assert len(await _turns(state.conversation_id)) == 2


async def test_a_clause_that_raises_pauses_its_own_trigger_and_shows_the_fault(db: None) -> None:
    """A clause that parses can still raise, because JMESPath types its functions at call time
    and a record need not carry the field one reaches for."""
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        bad = _trigger_manifest(
            feed, state.conversation_id, when=("contains(page.labels, 'urgent')",)
        )
        await _apply(_context(state), bad)
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(
                        _change(
                            pulls,
                            _pull_request_page(1684, state="MERGED"),
                            stream="pull_requests",
                        ),
                    )
                ),
            )
        )
        store = SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS))
        rows = await store.list_reported(conversation_id=state.conversation_id)
        paused = next(row.trigger for row in rows if row.trigger.when[0].startswith("contains"))
        assert paused.paused is True
        assert "contains(page.labels, 'urgent')" in (paused.fault or "")
        assert "pull_requests" in (paused.fault or "")

        resumed = await store.amend(paused, paused=False, description=paused.description)
        assert (resumed.paused, resumed.fault) == (False, None)

    assert len(await _turns(state.conversation_id)) == 1


async def test_a_trigger_narrowed_to_a_link_wakes_on_that_resource_alone(db: None) -> None:
    """The agent applies a trigger naming the pull request's link."""
    state = await _workspace()
    feed, source_id = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(
            _context(state),
            _watch_manifest(feed, state.conversation_id, PR_URL, description=WATCH_LINE),
        )
        assert applied["result"] == "created"
        assert await _watches(state, feed) == [
            list(resource_clauses(GITHUB, PR_URL, _repo(PR_URL)))
        ]

        narrowed = _name(
            feed, state.conversation_id, resource_clauses(GITHUB, PR_URL, _repo(PR_URL))
        )
        fetched = await _get(_context(state), narrowed)
        assert fetched["spec"] == {
            "connection": feed.name,
            "description": WATCH_LINE,
            "delivery": "current",
            "paused": False,
            "when": list(resource_clauses(GITHUB, PR_URL, _repo(PR_URL))),
        }
        assert fetched["status"]["when"] == list(resource_clauses(GITHUB, PR_URL, _repo(PR_URL)))

        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        watched = _change(source_id, _pull_request_page(1684), stream="pull_requests")
        other = _change(source_id, _pull_request_page(1685), stream="pull_requests")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(watched, other)))
        )

    [turn] = await _turns(state.conversation_id)
    assert WATCH_LINE in turn["inbound"]
    assert "stream == " not in turn["inbound"]
    assert f"{PAGE_KIND}/{watched.page_id}" in turn["inbound"]
    assert f"{PAGE_KIND}/{other.page_id}" not in turn["inbound"]
    assert "pull_requests: 1 added on " in turn["inbound"]


async def test_a_watch_on_a_pull_request_naming_no_streams_reports_the_minimal_events(
    db: None,
) -> None:
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    comments = await _stream(state, feed, provider=GITHUB, stream="comments")
    review_comments = await _stream(state, feed, provider=GITHUB, stream="review_comments")
    runs = await _stream(state, feed, provider=GITHUB, stream="workflow_runs")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        edited = _change(pulls, _pull_request_page(1684, state="OPEN"), stream="pull_requests")
        queued = _change(runs, _workflow_run_page(11, status="queued"), stream="workflow_runs")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(edited, queued)))
        )
        assert await _turns(state.conversation_id) == []

        commented = _change(comments, _comment_page(21, "issues"), stream="comments")
        reviewed = _change(review_comments, _comment_page(22, "pulls"), stream="review_comments")
        failed = _change(
            runs,
            _workflow_run_page(12, status="completed", conclusion="failure"),
            stream="workflow_runs",
        )
        merged = _change(pulls, _pull_request_page(1684), stream="pull_requests")
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(commented, reviewed, failed, merged)),
            )
        )

    [turn] = await _turns(state.conversation_id)
    for change in (commented, reviewed, failed, merged):
        assert f"{PAGE_KIND}/{change.page_id}" in turn["inbound"]


async def test_a_member_written_clause_hears_what_the_default_set_leaves(db: None) -> None:
    """The provider's set is a default, not a ceiling: a member who wants every edit of a pull
    request writes the clause for it, and hears the edit the default set passes over."""
    state = await _workspace()
    feed, pulls = await _github_feed(state)
    comments = await _stream(state, feed, provider=GITHUB, stream="comments")
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(
                feed,
                state.conversation_id,
                when=(f"stream == 'pull_requests' && page.url == '{PR_URL}'",),
            ),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        edited = _change(pulls, _pull_request_page(1684, state="OPEN"), stream="pull_requests")
        commented = _change(comments, _comment_page(21, "issues"), stream="comments")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(edited, commented)))
        )

    [turn] = await _turns(state.conversation_id)
    assert f"{PAGE_KIND}/{edited.page_id}" in turn["inbound"]
    assert f"{PAGE_KIND}/{commented.page_id}" not in turn["inbound"]


async def test_every_spelling_of_the_link_is_one_trigger(db: None) -> None:
    state = await _workspace()
    feed, _ = await _github_feed(state)
    api_form = "https://api.github.com/repos/acme/ufo/pulls/1684"
    tool = _TOOLS["object_apply"]
    clauses = resource_clauses(GITHUB, PR_URL, _repo(PR_URL))
    with ws(state.workspace_id), agent(state.agent_id):
        for spelled in (
            "https://github.com/Acme/ufo/pull/1684/files",
            api_form,
            PR_URL,
        ):
            applied = await _apply(
                _context(state),
                _narrowed_manifest(feed, state.conversation_id, spelled),
            )
        assert applied["result"] == "updated"
        assert await _watches(state, feed) == [list(clauses)]

        with pytest.raises(ValueError, match="already watches"):
            await tool.handler(
                _context(state),
                tool.input_model.model_validate(
                    {
                        "manifest": _trigger_manifest(
                            feed, state.conversation_id, when=clauses, name="a-second-watch"
                        )
                    }
                ),
            )
        assert await _watches(state, feed) == [list(clauses)]


async def test_a_clause_is_taken_as_written_whatever_the_provider(db: None) -> None:
    state = await _workspace()
    github, _ = await _github_feed(state)
    asana, _ = await _feed_with_stream(state, account="acct-two")
    with ws(state.workspace_id), agent(state.agent_id):
        for feed in (github, asana):
            applied = await _apply(
                _context(state),
                _trigger_manifest(
                    feed,
                    state.conversation_id,
                    when=("page.url == 'https://linear.app/acme/issue/UFO-1'",),
                ),
            )
            assert applied["result"] == "created"
        assert await _watches(state, github) == await _watches(state, asana)


async def test_two_triggers_on_one_connection_keep_their_own_change_logs(
    db: None, tmp_path
) -> None:
    """A conversation holds a whole-feed trigger and one narrowed to a resource of that feed."""
    state = await _workspace()
    feed, source_id = await _github_feed(state)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        watched = _change(source_id, _pull_request_page(1684), stream="pull_requests")
        other = _change(source_id, _pull_request_page(1685), stream="pull_requests")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(watched, other)))
        )
        store = SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS))
        scopes = {
            trigger.when: trigger.id.hex[:TRIGGER_SCOPE_HEX]
            for trigger in await store.waking(feed.id)
        }

    whole = await _change_log(
        sandboxes, state.conversation_id, feed.name, scopes[default_clauses(GITHUB)]
    )
    narrowed = await _change_log(
        sandboxes,
        state.conversation_id,
        feed.name,
        scopes[resource_clauses(GITHUB, PR_URL, _repo(PR_URL))],
    )
    assert {entry["page"] for entry in whole} == {
        f"{PAGE_KIND}/{watched.page_id}",
        f"{PAGE_KIND}/{other.page_id}",
    }
    assert {entry["page"] for entry in narrowed} == {f"{PAGE_KIND}/{watched.page_id}"}
    assert len(await _turns(state.conversation_id)) == 2


class _SpawnInput(BaseModel):
    objective: str = ""


async def _conversation_on(state: _Workspace, surface: str) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=state.workspace_id,
                agent_id=state.agent_id,
                surface=surface,
                queue_key=uuid4().hex,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _seen(
    state: _Workspace,
    payload: UserPromptSubmit | PostToolUse,
    conversation_id: UUID | None = None,
) -> HookOutcome:
    """Show one message or one tool result to the link hook, as the turn it lands on would."""
    turn = _context(state).turn
    if conversation_id is not None:
        turn = turn.model_copy(update={"conversation_id": conversation_id})
    return await on_link_seen(
        HookContext(
            ext=context_for(NAME, DECLARED_PROVIDERS),
            payload=payload,
            turn=turn,
            speaker_member_id=state.owner_id,
        )
    )


async def test_a_link_to_a_synced_resource_is_offered_to_the_conversation(db: None) -> None:
    state = await _workspace()
    feed, _ = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        said = await _seen(state, UserPromptSubmit(text=f"Keep an eye on {PR_URL}/files for me."))
        returned = await _seen(
            state,
            PostToolUse(
                tool_name="message_spawn", tool_input=_SpawnInput(), output=f"Opened {PR_URL}."
            ),
        )

    assert isinstance(said, InjectContext)
    assert isinstance(returned, InjectContext)
    assert said.text == returned.text
    assert "add a `description` of one line" in said.text
    assert said.text.startswith("<watch_offer>")
    assert said.text.endswith("</watch_offer>")
    assert f"{PR_URL} " in said.text
    assert f"{PR_URL}/files" not in said.text
    assert repr(feed.name) in said.text
    offer = said.text.removeprefix("<watch_offer>\n").removesuffix("\n</watch_offer>")
    manifest_text = offer.split("call object_apply with this manifest:\n```yaml\n", 1)[1]
    assert manifest_text.endswith("\n```")
    manifest_text = manifest_text.removesuffix("\n```")
    assert manifest_text.splitlines()[0] == f"kind: {SOURCE_TRIGGER_KIND}"
    clauses = resource_clauses(GITHUB, PR_URL, _repo(PR_URL))
    assert yaml.safe_load(manifest_text) == {
        "kind": SOURCE_TRIGGER_KIND,
        "spec": {
            "connection": feed.name,
            "when": list(clauses),
            "delivery": "current",
        },
    }
    named = manifest_text.replace(
        f"kind: {SOURCE_TRIGGER_KIND}", f"kind: {SOURCE_TRIGGER_KIND}\nname: watch-1684", 1
    )
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(_context(state), named)
        assert applied["result"] == "created"
        assert await _clauses_of(state, feed) == [list(clauses)]


async def test_each_offer_is_its_own_fenced_block(db: None) -> None:
    state = await _workspace()
    await _github_feed(state)
    other = f"{PR_URL.rsplit('/', 1)[0]}/9"
    with ws(state.workspace_id), agent(state.agent_id):
        offered = await _seen(state, UserPromptSubmit(text=f"Watch {PR_URL} and {other}."))

    assert isinstance(offered, InjectContext)
    body = offered.text.removeprefix("<watch_offer>\n").removesuffix("\n</watch_offer>")
    offers = body.split("\n\n")
    assert len(offers) == 2
    assert [offer.split(" ", 1)[0] for offer in offers] == [PR_URL, other]
    manifests = [offer.split("```yaml\n", 1)[1] for offer in offers]
    assert all(manifest.endswith("\n```") for manifest in manifests)
    assert [
        yaml.safe_load(manifest.removesuffix("\n```"))["spec"]["when"] for manifest in manifests
    ] == [
        list(resource_clauses(GITHUB, PR_URL, _repo(PR_URL))),
        list(resource_clauses(GITHUB, other, _repo(other))),
    ]


async def test_two_spellings_of_one_resource_earn_one_offer_spelled_as_its_page(db: None) -> None:
    """GitHub's search payload names a pull request twice — `url` as an API issues link,
    `html_url` as its page — and its comments file it under `issues/<n>`."""
    state = await _workspace()
    await _github_feed(state)
    number = PR_URL.rsplit("/", 1)[1]
    api_issue = f"https://api.github.com/repos/acme/ufo/issues/{number}"
    payload = json.dumps({"url": api_issue, "html_url": PR_URL, "state": "open"})
    with ws(state.workspace_id), agent(state.agent_id):
        offered = await _seen(state, UserPromptSubmit(text=payload))

    assert isinstance(offered, InjectContext)
    body = offered.text.removeprefix("<watch_offer>\n").removesuffix("\n</watch_offer>")
    assert body.count("call object_apply with this manifest:") == 1
    assert body.startswith(f"{PR_URL} is a resource")


async def test_a_message_of_thousands_of_links_costs_one_pass(db: None) -> None:
    state = await _workspace()
    await _github_feed(state)
    links = " ".join(f"https://github.com/acme/ufo/pull/{number}" for number in range(1, 4001))
    with ws(state.workspace_id), agent(state.agent_id):
        started = perf_counter()
        offered = await _seen(state, UserPromptSubmit(text=links))
        elapsed = perf_counter() - started

    assert isinstance(offered, InjectContext)
    assert offered.text.count("call object_apply with this manifest:\n") == WATCH_OFFER_MAX
    assert elapsed < 10


async def test_an_offer_shows_what_the_conversation_already_wakes_on(db: None) -> None:
    state = await _workspace()
    feed, _ = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _watch_manifest(feed, state.conversation_id, PR_URL))
        offered = await _seen(state, UserPromptSubmit(text=f"Any news on {PR_URL}?"))

    assert isinstance(offered, InjectContext)
    for clause in resource_clauses(GITHUB, PR_URL, _repo(PR_URL)):
        assert clause in offered.text
    assert "This conversation already wakes on:" in offered.text


async def test_links_naming_nothing_synced_earn_no_offer(db: None) -> None:
    """A repository link, a link of a provider this workspace does not sync, and a pull request of a
    connection that syncs privately name nothing a conversation can be offered."""
    state = await _workspace()
    await _feed_with_stream(
        state, shared=False, owner=state.owner_id, provider=GITHUB, stream="pull_requests"
    )
    await _feed_with_stream(state, account="acct-two")
    with ws(state.workspace_id), agent(state.agent_id):
        for text in (
            "See https://github.com/acme/ufo for context.",
            f"Look at {PR_URL}.",
            "https://linear.app/acme/issue/UFO-1 is the ticket.",
        ):
            assert await _seen(state, UserPromptSubmit(text=text)) is None


async def test_no_offer_reaches_a_conversation_a_member_does_not_read(db: None) -> None:
    """A spawned child's conversation and a room this extension opened for an alert are machine
    conversations: a trigger applied there would wake nobody a member reads."""
    state = await _workspace()
    await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        spawned = await _conversation_on(state, SUBAGENT_SURFACE)
        room = await _conversation_on(state, NAME)
        for conversation_id in (spawned, room):
            seen = await _seen(
                state, UserPromptSubmit(text=f"Opened {PR_URL}."), conversation_id=conversation_id
            )
            assert seen is None


async def test_an_offer_names_a_bounded_number_of_resources(db: None) -> None:
    """A message that pastes a whole board of links is offered the first few, not a page of
    offers."""
    state = await _workspace()
    await _github_feed(state)
    links = " ".join(
        f"https://github.com/acme/ufo/pull/{number}" for number in range(1, WATCH_OFFER_MAX + 3)
    )
    with ws(state.workspace_id), agent(state.agent_id):
        offered = await _seen(state, UserPromptSubmit(text=links))

    assert isinstance(offered, InjectContext)
    assert offered.text.count("call object_apply with this manifest:\n") == WATCH_OFFER_MAX
    assert f"pull/{WATCH_OFFER_MAX + 1} " not in offered.text


async def test_a_link_is_read_out_of_markdown_and_out_of_an_encoded_payload(db: None) -> None:
    state = await _workspace()
    await _github_feed(state)
    payload = (
        '{"result":"Pull request open: [acme/ufo #1684 \\u2014 test]'
        f"({PR_URL}).\\n\\nBranch `ufo/0283725a-ci-test`, pushed to origin."
        '"}'
    )
    with ws(state.workspace_id), agent(state.agent_id):
        offers = [
            await _seen(
                state,
                PostToolUse(tool_name="spawn", tool_input=_SpawnInput(), output=payload),
            ),
            await _seen(state, UserPromptSubmit(text=f"see <{PR_URL}>, then merge")),
            await _seen(
                state, UserPromptSubmit(text=f"{PR_URL}, {PR_URL}/files and [it]({PR_URL})")
            ),
        ]

    for offered in offers:
        assert isinstance(offered, InjectContext)
        assert offered.text.count("call object_apply with this manifest:\n") == 1
        assert f"{PR_URL} is a resource" in offered.text


async def test_an_empty_trigger_page_reads_no_connections(db: None) -> None:
    state = await _workspace()
    await _connect(state)
    statements: list[str] = []

    def record(
        connection: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            engine = connection.engine
        sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            page = await SourceTriggerObjects().member_page(
                context_for(NAME, DECLARED_PROVIDERS),
                member_id=state.member_id,
                admin=False,
                query=ObjectListQuery(supported_fields=manifest().objects[0].list_fields),
            )
        finally:
            sa.event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert page.rows == ()
    reads = [statement for statement in statements if statement.startswith("SELECT")]
    assert len(reads) == 1
    assert "FROM source_trigger" in reads[0]


async def test_feed_connection_selection_stays_inside_the_workspace(db: None) -> None:
    state = await _workspace()
    other = await _workspace()
    wanted = await _connect(state)
    extra = await _connect(state, account="extra")
    foreign = await _connect(other)
    with ws(state.workspace_id):
        assert {row.id for row in await feed_connections()} == {wanted.id, extra.id}
        assert {row.id for row in await feed_connections({wanted.id, foreign.id})} == {wanted.id}
        assert await feed_connections(()) == ()


async def test_a_trigger_page_reads_only_the_connections_it_uses(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    extra = await _connect(state, account="unused")
    reads: list[tuple[str, str]] = []

    def record(
        connection: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM connection" in statement:
            reads.append((statement, str(parameters)))

    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        async with workspace_tx() as connection:
            engine = connection.engine
        sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            page = await SourceTriggerObjects().member_page(
                context_for(NAME, DECLARED_PROVIDERS),
                member_id=state.owner_id,
                admin=True,
                query=ObjectListQuery(supported_fields=manifest().objects[0].list_fields),
            )
        finally:
            sa.event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert len(page.rows) == 1
    assert page.rows[0].fields["connection"] == feed.name
    assert len(reads) == 1
    statement, parameters = reads[0]
    assert "connection.id IN" in statement
    assert feed.id.hex in parameters.replace("-", "")
    assert extra.id.hex not in parameters.replace("-", "")
