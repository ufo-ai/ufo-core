"""The `source_trigger` object kind end to end: a conversation's standing interest in one shared
connection's feed, applied and deleted through the object verbs, delivered by the `page_change`
hook, and offered by the link hooks.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable trigger rows, the turns the alert admits, and the change
logs it writes. Covered here: a trigger wakes its own conversation until it is deleted; a private
connection cannot be watched and a stranger's is unknown; the alert reaches only woken
conversations, only with shared pages, only for agents that still hold the grant, and only once per
batch; per-page delivery keeps one conversation per page; a narrowed trigger wakes on its resource
alone under every spelling of the link; and a link a conversation does not watch earns exactly one
offer."""

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import CONNECTION_OBJECT_KIND, PAGE_KIND, PAGE_OBJECT
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.resources import resource_digest
from ufo_ext_sources.tools import (
    ALERT_CLOSING,
    CHANGE_LOG_DIR,
    SOURCE_TRIGGER_KIND,
    WATCH_OFFER_MAX,
    on_link_seen,
    on_page_change,
    trigger_name,
)
from ufo_ext_sources.triggers import SourceTrigger, SourceTriggerStore

from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import RUNTIME_DIRNAME, ProxyEndpoint
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.authority import MemberAuthority
from ufo.runtime.ext.context import context_for
from ufo.runtime.object_name import validate_object_name
from ufo.runtime.objects import UnknownObject
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import SUBAGENT_SURFACE
from ufo.sdk.grants import account_object_name
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    InjectContext,
    PageChangeBatch,
    PostToolUse,
    UserPromptSubmit,
)
from ufo.sdk.sources import ConnectorSourceConfig, PageChange
from ufo.sdk.tools import ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ASANA = "asana"
GITHUB = "github"
ACCOUNT = "acct-one"
PR_URL = "https://github.com/metalcraftai/ufo/pull/1684"
DECLARED_PROVIDERS = frozenset(CONNECTORS)


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class _Feed:
    """One connection as a test names it: the id its triggers point at, and the `connection` object
    name a manifest spells it with."""

    id: UUID
    name: str


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
            inbound="watch this feed",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker,
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
    connection that already exists, so this sets one up without the OAuth dance. Recording grants
    the bound agent, which is what lets that agent read the feed."""
    with ws(state.workspace_id), agent(agent_id or state.agent_id):
        connection_id = await GrantStore().record(
            provider=provider,
            account_id=account,
            host=f"api.{provider}.test",
            grantor_member_id=owner or state.owner_id,
            shared=shared,
        )
    return _Feed(id=connection_id, name=account_object_name(provider, account))


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


async def _watches(state: _Workspace, feed: _Feed) -> list[str]:
    with ws(state.workspace_id):
        triggers = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
    return sorted(row.resource for row in triggers)


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


def _trigger_manifest(
    feed: _Feed, conversation_id: UUID, *, delivery: str = "current", resource: str = ""
) -> str:
    return yaml.safe_dump(
        {
            "kind": SOURCE_TRIGGER_KIND,
            "name": trigger_name(feed.name, conversation_id, resource),
            "spec": {"connection": feed.name, "delivery": delivery, "resource": resource},
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
    sandboxes: ConversationSandbox, conversation_id: UUID, name: str
) -> list[dict]:
    """Every line of the one change log written for this trigger's scope."""
    carrier = sandboxes.carrier
    assert isinstance(carrier, LocalCarrier)
    log_dir = carrier.ufo_home / RUNTIME_DIRNAME / conversation_id.hex / CHANGE_LOG_DIR / name
    (entry,) = sorted(path for path in log_dir.iterdir() if path.is_file())
    return [json.loads(line) for line in entry.read_text().splitlines()]


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
    assert {slot.name for slot in declared.credentials} == {
        name for name, connector in CONNECTORS.items() if not connector.key_headers
    } | {slot for connector in CONNECTORS.values() for slot in connector.key_headers.values()}
    assert {source.backend for source in declared.sources} == set(CONNECTORS)


def test_a_trigger_name_leads_with_its_connection_and_stays_within_the_grammar() -> None:
    """A member reading a list of trigger names sees which feed each one watches, because the
    connection's own name leads the trigger's — and the digest of the whole triple qualifies it, so
    an account name long enough to fill the grammar by itself still leaves the digest whole and
    every one of the three parts still separates two triggers."""
    conversation, elsewhere = uuid4(), uuid4()
    short = account_object_name(ASANA, ACCOUNT)
    longest = account_object_name(ASANA, "a" * 512)
    for connection in (short, longest):
        for resource in ("", PR_URL):
            validate_object_name(trigger_name(connection, conversation, resource))

    assert trigger_name(short, conversation).startswith(f"{short}-")
    assert trigger_name(short, conversation) == trigger_name(short, conversation)
    assert (
        len(
            {
                trigger_name(short, conversation),
                trigger_name(longest, conversation),
                trigger_name(short, elsewhere),
                trigger_name(short, conversation, PR_URL),
            }
        )
        == 4
    )


async def test_a_trigger_wakes_its_own_conversation_until_it_is_deleted(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    name = trigger_name(feed.name, state.conversation_id)
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
            "delivery": "current",
            "resource": "",
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


async def test_a_non_owner_may_watch_a_shared_connection(db: None) -> None:
    state = await _workspace()
    feed, _ = await _feed_with_stream(state)
    with ws(state.workspace_id), agent(state.agent_id):
        watched = await _apply(
            _context(state, speaker_id=state.member_id),
            _trigger_manifest(feed, state.conversation_id),
        )
        assert watched["result"] == "created"
        assert await _woken(state, feed) == {state.conversation_id: state.agent_id}


async def test_a_private_connection_cannot_be_watched_and_a_strangers_is_unknown(db: None) -> None:
    """A connection private to member M discloses its pages to M alone, so its changes could wake
    nobody: M is told exactly that, while a stranger is told the connection does not exist at all —
    the refusal a guessed name earns."""
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
    """A trigger names its connection by foreign key, so one written after the disconnect commits is
    refused by the database rather than filed against a feed nobody can reach. Nothing sweeps it
    afterwards, because nothing has to."""
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    real_create = SourceTriggerStore.create

    async def disconnect_before_create(
        store: SourceTriggerStore,
        conversation_id: UUID,
        connection_id: UUID,
        delivery: str,
        created_by_member_id: UUID | None = None,
        resource: str = "",
    ) -> SourceTrigger:
        assert await GrantStore().disconnect(connection_id, actor_member_id=state.owner_id) is True
        return await real_create(
            store,
            conversation_id,
            connection_id,
            delivery,
            created_by_member_id=created_by_member_id,
            resource=resource,
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
            authority=MemberAuthority(state.owner_id),
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
        assert turn["on_behalf_of_member_id"] == state.owner_id
        assert turn["fired_by_kind"] == SOURCE_TRIGGER_KIND
        assert turn["fired_by_name"] == trigger_name(feed.name, state.conversation_id)
        assert turn["fired_by_title"].startswith(feed.name)
        assert "tasks: 2 added" in turn["inbound"]
        assert f"{PAGE_KIND}/{shipped.page_id}" in turn["inbound"]
        assert f"{PAGE_KIND}/{legal.page_id}" in turn["inbound"]

        await on_page_change(HookContext(ext=ext, payload=batch))
        assert len(await _turns(state.conversation_id)) == 2


async def test_alert_opens_on_what_changed_and_asks_for_no_member_report(db: None) -> None:
    """The first line names the provider, the counts and the changed pages' own titles — a member
    reading a conversation list sees the alert's opening characters and nothing else, and the
    connection name, the refs and the machine detail follow underneath. Nothing on the woken turn
    asks for a report: no member is reading it."""
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
        headline, connection_line, refs, closing = turn["inbound"].splitlines()
        assert headline == (
            f"{ASANA}: asana tasks: Ship the launch list; asana tasks: Follow up with legal "
            "— tasks: 2 added."
        )
        assert connection_line.startswith(f"Connection {feed.name} ")
        assert refs.endswith(f"{PAGE_KIND}/{shipped.page_id}; {PAGE_KIND}/{legal.page_id}.")
        assert closing == ALERT_CLOSING
        assert "tell the member" not in turn["inbound"]


async def test_shared_trigger_conversation_carries_no_member_authority(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.id == state.conversation_id)
            .values(member_id=None, audience=str(SHARED_AUDIENCE))
        )
    ctx = replace(_context(state), audience=SHARED_AUDIENCE)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _trigger_manifest(feed, state.conversation_id))
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(_change(source_id, "# shared"),)),
            )
        )

    [turn] = await _turns(state.conversation_id)
    assert turn["speaker_member_id"] is None
    assert turn["on_behalf_of_member_id"] is None


async def test_existing_per_page_conversation_keeps_creator_authority_when_unseated(
    db: None,
) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    page_id = uuid4()
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, delivery="per_page"),
        )
        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(changes=(_change(source_id, "# first", page_id=page_id),)),
            )
        )
    async with workspace_tx() as connection:
        [conversation_id] = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == state.workspace_id,
                    tables.conversation.c.surface == NAME,
                )
            )
        ).scalars()
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.conversation_id == conversation_id)
            .values(status="done", terminal={"status": "done", "text": "Handled."})
        )
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == state.owner_id)
            .values(seated_at=None)
        )
    with ws(state.workspace_id), agent(state.agent_id):
        await on_page_change(
            HookContext(
                ext=ext,
                payload=PageChangeBatch(
                    changes=(
                        _change(
                            source_id,
                            "# still delivered",
                            changed_at=datetime(2026, 7, 21, tzinfo=UTC),
                            disposition="updated",
                            page_id=page_id,
                            revision=2,
                        ),
                    )
                ),
            )
        )

    turns = await _turns(conversation_id)
    assert len(turns) == 2
    turn = turns[-1]
    assert turn["status"] == "parked"
    assert turn["on_behalf_of_member_id"] == state.owner_id
    assert "still delivered" in turn["inbound"]


async def test_per_page_delivery_keeps_one_conversation_per_page(db: None) -> None:
    state = await _workspace()
    feed, source_id = await _feed_with_stream(state)
    first_page, second_page = uuid4(), uuid4()
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state),
            _trigger_manifest(feed, state.conversation_id, delivery="per_page"),
        )
        [trigger] = await SourceTriggerStore(context_for(NAME, DECLARED_PROVIDERS)).waking(feed.id)
        assert trigger.delivery == "per_page"
        fetched = await _get(_context(state), trigger_name(feed.name, state.conversation_id))
        assert fetched["spec"] == {
            "connection": feed.name,
            "delivery": "per_page",
            "resource": "",
        }
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
        assert conversation["member_id"] == state.owner_id
        assert conversation["audience"] == str(conversation_audience(state.owner_id))
        [turn] = await _turns(conversation["id"])
        assert turn["speaker_member_id"] is None
        assert turn["on_behalf_of_member_id"] == state.owner_id
        if str(first_page) in turn["inbound"]:
            turns_by_page[first_page] = turn
        if str(second_page) in turn["inbound"]:
            turns_by_page[second_page] = turn
    assert set(turns_by_page) == {first_page, second_page}
    assert turns_by_page[first_page]["idempotency_key"] == (
        f"source-trigger:{trigger.id.hex}:{state.owner_id.hex}:{first_page.hex}:1"
    )
    assert turns_by_page[second_page]["idempotency_key"] == (
        f"source-trigger:{trigger.id.hex}:{state.owner_id.hex}:{second_page.hex}:1"
    )
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
    grant, and a grant can go after the trigger is written. A conversation bound to an agent whose
    grant is gone is alerted about nothing: no turn, no change log, no page ids anywhere — while the
    granted trigger in the same batch is alerted in full, so the suppression is per-trigger and not
    a dropped batch."""
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
        assert "— tasks added." in turn["inbound"]
        assert f"{PAGE_KIND}/{shared.page_id}" in turn["inbound"]
        assert str(private.page_id) not in turn["inbound"]


async def test_alert_counts_by_stream_and_never_truncates(db: None, tmp_path) -> None:
    """A batch past the naming bound carries per-stream added/updated/removed counts and the path
    to the change log — never a prefix of page ids and an opaque `+N more`. Every changed page is
    in the log, so nothing the agent needs is dropped."""
    state = await _workspace()
    feed, tasks_id = await _feed_with_stream(state)
    projects_id = await _stream(state, feed, stream="projects")
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
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
        headline, *_ = turn["inbound"].splitlines()
        assert headline == (
            f"{ASANA}: gone 1 and 8 other pages changed "
            "— projects: 2 removed; tasks: 3 added, 4 updated."
        )
        assert "more" not in turn["inbound"]
        assert not any(str(change.page_id) in turn["inbound"] for change in changes)

        logged = await _change_log(sandboxes, state.conversation_id, feed.name)
        assert (
            f"$UFO_HOME/{RUNTIME_DIRNAME}/{state.conversation_id.hex}/{CHANGE_LOG_DIR}/{feed.name}/"
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
    """A change log that cannot be written fails the batch instead of quietly alerting without it.
    Here the workspace root is a file, so the carrier cannot make the conversation's directory —
    internal state, not external flakiness — so it raises, the cursor stays put for the next tick,
    and no alert claims a delta whose detail was dropped."""
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


def _pull_request_page(number: int) -> str:
    """One GitHub record as the connector lands it — the provider's own JSON, carrying the URL the
    resource is matched on."""
    return json.dumps(
        {
            "html_url": f"https://github.com/metalcraftai/ufo/pull/{number}",
            "number": number,
            "title": "Watch the pull request a trigger names",
        }
    )


def _narrowed_manifest(feed: _Feed, conversation_id: UUID, spelled: str, stored: str) -> str:
    """A trigger manifest whose spec spells the link one way while its name derives from the
    canonical form the row stores."""
    return yaml.safe_dump(
        {
            "kind": SOURCE_TRIGGER_KIND,
            "name": trigger_name(feed.name, conversation_id, stored),
            "spec": {"connection": feed.name, "delivery": "current", "resource": spelled},
        }
    )


async def _github_feed(state: _Workspace) -> tuple[_Feed, UUID]:
    return await _feed_with_stream(state, provider=GITHUB, stream="pull_requests")


async def test_a_trigger_narrowed_to_a_link_wakes_on_that_resource_alone(db: None) -> None:
    """The agent applies a trigger naming the pull request's link. The conversation then hears about
    that pull request and about nothing else the connection carries, the alert names the link, and
    the trigger reads back with it."""
    state = await _workspace()
    feed, source_id = await _github_feed(state)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(
            _context(state), _trigger_manifest(feed, state.conversation_id, resource=PR_URL)
        )
        assert applied["result"] == "created"
        assert await _watches(state, feed) == [PR_URL]

        narrowed = trigger_name(feed.name, state.conversation_id, PR_URL)
        fetched = await _get(_context(state), narrowed)
        assert fetched["spec"] == {
            "connection": feed.name,
            "delivery": "current",
            "resource": PR_URL,
        }
        assert fetched["status"]["resource"] == PR_URL

        ext = context_for(NAME, DECLARED_PROVIDERS, invoker=_admitting(state.workspace_id))
        watched = _change(source_id, _pull_request_page(1684), stream="pull_requests")
        other = _change(source_id, _pull_request_page(1685), stream="pull_requests")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(watched, other)))
        )

    [turn] = await _turns(state.conversation_id)
    assert PR_URL in turn["inbound"]
    assert f"{PAGE_KIND}/{watched.page_id}" in turn["inbound"]
    assert f"{PAGE_KIND}/{other.page_id}" not in turn["inbound"]
    assert "— pull_requests added." in turn["inbound"]


async def test_every_spelling_of_the_link_is_one_trigger(db: None) -> None:
    """The row stores the canonical link, so the repository's own capitalization, a sub-page of the
    pull request and the API form all name the trigger that exists: re-applying under any of them is
    a no-op, and a name derived from another spelling is refused with the one to use."""
    state = await _workspace()
    feed, _ = await _github_feed(state)
    api_form = "https://api.github.com/repos/metalcraftai/ufo/pulls/1684"
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        for spelled in (
            "https://github.com/MetalCraftAI/ufo/pull/1684/files",
            api_form,
            PR_URL,
        ):
            applied = await _apply(
                _context(state),
                _narrowed_manifest(feed, state.conversation_id, spelled, PR_URL),
            )
        assert applied["result"] == "updated"
        assert await _watches(state, feed) == [PR_URL]

        narrowed = trigger_name(feed.name, state.conversation_id, PR_URL)
        with pytest.raises(ValueError, match=narrowed):
            await tool.handler(
                _context(state),
                tool.input_model.model_validate(
                    {
                        "manifest": _narrowed_manifest(
                            feed, state.conversation_id, api_form, api_form
                        )
                    }
                ),
            )
        assert await _watches(state, feed) == [PR_URL]


async def test_a_link_that_names_no_resource_of_the_connection_is_refused(db: None) -> None:
    """A repository link and a link into another provider name nothing a GitHub connection narrows
    to, and a pull request link names nothing of a connection whose provider reads no links."""
    state = await _workspace()
    github, _ = await _github_feed(state)
    asana, _ = await _feed_with_stream(state, account="acct-two")
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        for feed, link in (
            (github, "https://github.com/metalcraftai/ufo"),
            (github, "https://linear.app/metalcraft/issue/UFO-1"),
            (asana, PR_URL),
        ):
            with pytest.raises(ValueError, match="not the link of a resource"):
                await tool.handler(
                    _context(state),
                    tool.input_model.model_validate(
                        {"manifest": _trigger_manifest(feed, state.conversation_id, resource=link)}
                    ),
                )
        assert await _watches(state, github) == []
        assert await _watches(state, asana) == []


async def test_two_triggers_on_one_connection_keep_their_own_change_logs(
    db: None, tmp_path
) -> None:
    """A conversation holds a whole-feed trigger and one narrowed to a resource of that feed. One
    landing stamps every page with the same `changed_at`, so a log named for the connection and that
    stamp alone would be written twice and the whole-feed alert would point at the narrowed
    trigger's one line. Each trigger logs under its own directory."""
    state = await _workspace()
    feed, source_id = await _github_feed(state)
    sandboxes = _sandboxes(tmp_path)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state), _trigger_manifest(feed, state.conversation_id))
        await _apply(
            _context(state), _trigger_manifest(feed, state.conversation_id, resource=PR_URL)
        )
        ext = context_for(
            NAME, DECLARED_PROVIDERS, sandboxes=sandboxes, invoker=_admitting(state.workspace_id)
        )
        watched = _change(source_id, _pull_request_page(1684), stream="pull_requests")
        other = _change(source_id, _pull_request_page(1685), stream="pull_requests")
        await on_page_change(
            HookContext(ext=ext, payload=PageChangeBatch(changes=(watched, other)))
        )

    whole = await _change_log(sandboxes, state.conversation_id, feed.name)
    narrowed = await _change_log(
        sandboxes, state.conversation_id, f"{feed.name}/{resource_digest(PR_URL)}"
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
    """A message or a tool result naming a pull request of a connection this workspace syncs earns
    one offer: the canonical link, the connection it belongs to, and the one call that takes it —
    the `object_apply` manifest on one line, which lands verbatim. The offer is the same whichever
    way the link arrived."""
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
    assert yaml.safe_load(manifest_text) == {
        "kind": SOURCE_TRIGGER_KIND,
        "name": trigger_name(feed.name, state.conversation_id, PR_URL),
        "spec": {"connection": feed.name, "resource": PR_URL},
    }
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(_context(state), manifest_text)
        assert applied["result"] == "created"
        assert await _watches(state, feed) == [PR_URL]


async def test_each_offer_is_its_own_fenced_block(db: None) -> None:
    """Two links earn two offers a reader tells apart: each manifest sits in its own yaml fence and
    a blank line separates one offer from the next, so the second link's prose never reads as a
    continuation of the first manifest."""
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
        yaml.safe_load(manifest.removesuffix("\n```"))["spec"]["resource"] for manifest in manifests
    ] == [PR_URL, other]


async def test_two_spellings_of_one_resource_earn_one_offer_spelled_as_its_page(db: None) -> None:
    """GitHub's search payload names a pull request twice — `url` as an API issues link, `html_url`
    as its page — and its comments file it under `issues/<n>`. One resource is one offer, and the
    offer spells it the way the text spells its page, whichever spelling came first."""
    state = await _workspace()
    await _github_feed(state)
    number = PR_URL.rsplit("/", 1)[1]
    api_issue = f"https://api.github.com/repos/metalcraftai/ufo/issues/{number}"
    payload = json.dumps({"url": api_issue, "html_url": PR_URL, "state": "open"})
    with ws(state.workspace_id), agent(state.agent_id):
        offered = await _seen(state, UserPromptSubmit(text=payload))

    assert isinstance(offered, InjectContext)
    body = offered.text.removeprefix("<watch_offer>\n").removesuffix("\n</watch_offer>")
    assert body.count("call object_apply with this manifest:") == 1
    assert body.startswith(f"{PR_URL} is a resource")


async def test_a_message_of_thousands_of_links_costs_one_pass(db: None) -> None:
    """The web surface admits a message holding thousands of links, and the hook body runs with no
    await, so a duplicate check that compared each link with every link before it stalled the whole
    event loop for tens of seconds. Each link costs a bounded set lookup, so the pass stays well
    inside the hook's own timeout."""
    state = await _workspace()
    await _github_feed(state)
    links = " ".join(
        f"https://github.com/metalcraftai/ufo/pull/{number}" for number in range(1, 4001)
    )
    with ws(state.workspace_id), agent(state.agent_id):
        started = perf_counter()
        offered = await _seen(state, UserPromptSubmit(text=links))
        elapsed = perf_counter() - started

    assert isinstance(offered, InjectContext)
    assert offered.text.count("call object_apply with this manifest:\n") == WATCH_OFFER_MAX
    assert elapsed < 10


async def test_no_offer_repeats_for_a_resource_the_conversation_watches(db: None) -> None:
    """Neither the link itself nor another spelling of the same number — the API issues form a
    comment record carries for a pull request — is offered again."""
    state = await _workspace()
    feed, _ = await _github_feed(state)
    number = PR_URL.rsplit("/", 1)[1]
    api_issue = f"https://api.github.com/repos/metalcraftai/ufo/issues/{number}"
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(
            _context(state), _trigger_manifest(feed, state.conversation_id, resource=PR_URL)
        )
        assert await _seen(state, UserPromptSubmit(text=f"Any news on {PR_URL}?")) is None
        assert await _seen(state, UserPromptSubmit(text=f"Comment filed at {api_issue}")) is None


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
            "See https://github.com/metalcraftai/ufo for context.",
            f"Look at {PR_URL}.",
            "https://linear.app/metalcraft/issue/UFO-1 is the ticket.",
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
        f"https://github.com/metalcraftai/ufo/pull/{number}"
        for number in range(1, WATCH_OFFER_MAX + 3)
    )
    with ws(state.workspace_id), agent(state.agent_id):
        offered = await _seen(state, UserPromptSubmit(text=links))

    assert isinstance(offered, InjectContext)
    assert offered.text.count("call object_apply with this manifest:\n") == WATCH_OFFER_MAX
    assert f"pull/{WATCH_OFFER_MAX + 1} " not in offered.text


async def test_a_link_is_read_out_of_markdown_and_out_of_an_encoded_payload(db: None) -> None:
    """A coding child reports its pull request as a markdown link inside a JSON-encoded payload, so
    the link the hook reads is followed by a closing parenthesis and an escaped newline written as
    two characters, and a member's message wraps a link in angle brackets or ends it with a comma.
    Each of those is the same one resource."""
    state = await _workspace()
    await _github_feed(state)
    payload = (
        '{"result":"Pull request open: [metalcraftai/ufo #1684 \\u2014 test]'
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
        assert f"resource: {PR_URL}\n" in offered.text
