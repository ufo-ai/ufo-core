"""Connection and connector-grant objects through the real object verbs."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from ufo_ext_connectors.manifest import manifest
from ufo_ext_connectors.objects import (
    CONNECTION_KIND,
    CONNECTOR_GRANT_KIND,
    ConnectionObjects,
    ConnectorGrantObjects,
    ConnectorGrantSpec,
)

from ufo.access.grants import (
    ConnectionPermissionDenied,
    GrantStore,
    account_object_name,
    connection_summaries,
    grant_summaries,
    workspace_grant_summaries,
)
from ufo.agent_scope import agent
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.kinds.agents import AGENT_KIND
from ufo.object_name import OBJECT_NAME_MAX_LENGTH, ObjectRef
from ufo.objects import (
    AdminRequired,
    GeneratedObjectOwner,
    ObjectDetail,
    ObjectListQuery,
    UnknownObject,
    VerbNotSupported,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

TOOL_NARRATION = "checking their connected accounts"
ADMIN_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
GRANTOR_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)
OTHER_CREATED_AT = datetime(2026, 7, 3, tzinfo=UTC)
SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"
GMAIL_ALICE_NAME = "gmail-alice-example-com-84f6ad00"
GMAIL_COLLISION_ACCOUNT = "alice-example.com"
GMAIL_COLLISION_NAME = "gmail-alice-example-com-209de93f"
GMAIL_BOB_NAME = "gmail-bob-example-com-6057022f"
ASANA_BOB_NAME = "asana-bob-example-com-5f89e6c5"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object verbs must not spawn a subagent")


async def _seed() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    admin_id, grantor_id, other_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member_id, created_at in (
            (admin_id, ADMIN_CREATED_AT),
            (grantor_id, GRANTOR_CREATED_AT),
            (other_id, OTHER_CREATED_AT),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=f"{member_id.hex[:8]}@x.test",
                    is_admin=member_id == admin_id,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=admin_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id, admin_id, grantor_id, other_id


async def _grant(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    grantor_id: UUID,
    provider: str,
    account_id: str,
) -> None:
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider=provider,
            account_id=account_id,
            host=f"api.{provider}.test",
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=False,
        )


def _tool_context(
    workspace_id: UUID,
    agent_id: UUID,
    speaker_member_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=Path()),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id or uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=GrantStore(),
    )


def _object_tool(name: str) -> ToolDef:
    tools, _ = turn_tools((manifest(),), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )
    assert result.is_error is False
    return result.content[0].text


async def test_granted_accounts_list_and_read_through_the_verbs(db: None) -> None:
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.connector_grant).values(
                    created_at=datetime(2026, 7, 10, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 11, tzinfo=UTC),
                )
            )
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        grant_listing = json.loads(
            await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_GRANT_KIND)
        )
        connection_listing = json.loads(
            await _text(_object_tool("object_list"), ctx, kind=CONNECTION_KIND)
        )
        assert [row["name"] for row in grant_listing["objects"]] == [GMAIL_ALICE_NAME]
        assert [row["name"] for row in connection_listing["objects"]] == [GMAIL_ALICE_NAME]
        assert "alice@example.com" in grant_listing["objects"][0]["summary"]

        fetched = yaml.safe_load(
            await _text(
                _object_tool("object_get"),
                ctx,
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        )
        assert fetched["spec"] == {
            "provider": "gmail",
            "account_id": "alice@example.com",
            "shared": False,
        }
        assert fetched["status"]["owner_member_id"] == str(grantor_id)
        assert fetched["status"]["host"] == "api.gmail.test"
        assert fetched["status"]["agent"] == "assistant"
        assert datetime.fromisoformat(fetched["created_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 10, tzinfo=UTC
        )
        assert datetime.fromisoformat(fetched["updated_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 11, tzinfo=UTC
        )


async def test_a_grant_links_to_its_agent_and_the_connection_it_opens(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, _other = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        fetched = yaml.safe_load(
            await _text(
                _object_tool("object_get"),
                ctx,
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        )
        assert fetched["links"] == [
            {"relation": "scoped_to", "target": {"kind": AGENT_KIND, "name": "assistant"}},
            {
                "relation": "access_to",
                "target": {
                    "kind": CONNECTION_KIND,
                    "name": account_object_name("gmail", "alice@example.com"),
                },
            },
        ]
        _scope, link = fetched["links"]
        opened = yaml.safe_load(
            await _text(
                _object_tool("object_get"),
                ctx,
                kind=link["target"]["kind"],
                name=link["target"]["name"],
            )
        )
        assert opened["spec"] == {"provider": "gmail", "account_id": "alice@example.com"}
        assert opened["links"] == []


async def test_a_shared_grant_drops_the_link_to_its_owner_only_connection(db: None) -> None:
    """A shared grant is readable by every member while its connection stays owner-or-admin, so the
    `access_to` edge would point at narrower visibility — spec.md demands equal-or-wider. Sharing
    withdraws the edge and making the grant private again restores it."""
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, other_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        owner = _tool_context(workspace_id, agent_id, grantor_id)

        async def relations(ctx) -> list[str]:
            fetched = yaml.safe_load(
                await _text(
                    _object_tool("object_get"),
                    ctx,
                    kind=CONNECTOR_GRANT_KIND,
                    name=GMAIL_ALICE_NAME,
                )
            )
            return [link["relation"] for link in fetched["links"]]

        assert await relations(owner) == ["scoped_to", "access_to"]

        await _text(
            _object_tool("object_apply"),
            owner,
            manifest=yaml.safe_dump(
                {
                    "kind": CONNECTOR_GRANT_KIND,
                    "name": GMAIL_ALICE_NAME,
                    "spec": {
                        "provider": "gmail",
                        "account_id": "alice@example.com",
                        "shared": True,
                    },
                }
            ),
        )
        assert await relations(owner) == ["scoped_to"]
        assert await relations(_tool_context(workspace_id, agent_id, other_id)) == ["scoped_to"]

        await _text(
            _object_tool("object_apply"),
            owner,
            manifest=yaml.safe_dump(
                {
                    "kind": CONNECTOR_GRANT_KIND,
                    "name": GMAIL_ALICE_NAME,
                    "spec": {
                        "provider": "gmail",
                        "account_id": "alice@example.com",
                        "shared": False,
                    },
                }
            ),
        )
        assert await relations(owner) == ["scoped_to", "access_to"]


LONG_ACCOUNT = "anna.rodriguez-fernandez@platform-engineering.example.com"


async def test_a_long_account_still_names_a_connection_a_link_can_express(db: None) -> None:
    """`account_id` is unbounded, so an account whose slug overruns the object-name limit would make
    the grant's `access_to` ref raise out of `object_get`. The name is bounded by construction, and
    the read that renders the link is what proves it."""
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    name = account_object_name("gmail", LONG_ACCOUNT)
    assert len(name) == OBJECT_NAME_MAX_LENGTH
    assert ObjectRef(kind=CONNECTION_KIND, name=name).name == name

    with ws(workspace_id), agent(agent_id):
        await _grant(workspace_id, agent_id, conversation_id, grantor_id, "gmail", LONG_ACCOUNT)
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        fetched = yaml.safe_load(
            await _text(_object_tool("object_get"), ctx, kind=CONNECTOR_GRANT_KIND, name=name)
        )
        opened = yaml.safe_load(
            await _text(_object_tool("object_get"), ctx, kind=CONNECTION_KIND, name=name)
        )
    assert {
        "relation": "access_to",
        "target": {"kind": CONNECTION_KIND, "name": name},
    } in fetched["links"]
    assert opened["spec"] == {"provider": "gmail", "account_id": LONG_ACCOUNT}


def test_two_accounts_sharing_a_truncated_head_stay_distinct() -> None:
    """Truncation drops slug characters, so the digest is the only thing left separating two
    accounts identical up to the cut — it stays whole at the end of the name."""
    first = account_object_name("gmail", f"{LONG_ACCOUNT}.extra1")
    second = account_object_name("gmail", f"{LONG_ACCOUNT}.extra2")
    assert first != second
    for name in (first, second):
        assert len(name) == OBJECT_NAME_MAX_LENGTH
        assert ObjectRef(kind=CONNECTION_KIND, name=name).name == name
    assert first.rsplit("-", 1)[0] == second.rsplit("-", 1)[0], "the heads are identical"


async def test_colliding_account_slugs_never_rename_objects(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    ctx = _tool_context(workspace_id, agent_id, grantor_id)

    async def names(kind: str) -> set[str]:
        listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=kind))
        return {row["name"] for row in listing["objects"]}

    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        for kind in (CONNECTION_KIND, CONNECTOR_GRANT_KIND):
            assert await names(kind) == {GMAIL_ALICE_NAME}

        await _grant(
            workspace_id,
            agent_id,
            conversation_id,
            grantor_id,
            "gmail",
            GMAIL_COLLISION_ACCOUNT,
        )
        for kind in (CONNECTION_KIND, CONNECTOR_GRANT_KIND):
            assert await names(kind) == {GMAIL_ALICE_NAME, GMAIL_COLLISION_NAME}

        collision = next(
            row for row in await connection_summaries() if row.account_id == GMAIL_COLLISION_ACCOUNT
        )
        assert await GrantStore().disconnect(collision.id, actor_member_id=grantor_id) is True
        for kind in (CONNECTION_KIND, CONNECTOR_GRANT_KIND):
            assert await names(kind) == {GMAIL_ALICE_NAME}


@pytest.mark.parametrize(
    ("kind", "spec", "raises", "match"),
    (
        (
            CONNECTION_KIND,
            {"provider": "gmail", "account_id": "alice"},
            VerbNotSupported,
            "connect_account",
        ),
        (
            CONNECTOR_GRANT_KIND,
            {"provider": "gmail", "account_id": "alice", "shared": False},
            ValueError,
            "not available",
        ),
    ),
)
async def test_connect_stays_the_only_create_path(
    kind: str,
    spec: dict[str, object],
    raises: type[Exception],
    match: str,
    db: None,
) -> None:
    """Apply never creates a connection: the connection kind refuses outright, and a grant apply
    only attaches a connection the pool already holds — one that does not exist is refused."""
    workspace_id, agent_id, _conversation_id, _owner, grantor_id, _other = await _seed()
    apply_tool = _object_tool("object_apply")
    args = apply_tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": yaml.safe_dump(
                {
                    "kind": kind,
                    "name": "gmail-alice-705e9556",
                    "spec": spec,
                }
            ),
        }
    )
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(raises, match=match),
    ):
        await apply_tool.handler(_tool_context(workspace_id, agent_id, grantor_id), args)


async def test_explain_limits_admins_to_narrowing_or_revoking(db: None) -> None:
    workspace_id, agent_id, *_ = await _seed()
    with ws(workspace_id), agent(agent_id):
        connection = json.loads(
            await _text(
                _object_tool("object_explain"),
                _tool_context(workspace_id, agent_id),
                kind=CONNECTION_KIND,
            )
        )
        grant = json.loads(
            await _text(
                _object_tool("object_explain"),
                _tool_context(workspace_id, agent_id),
                kind=CONNECTOR_GRANT_KIND,
            )
        )
    assert "owner or a workspace admin may delete" in connection["guidance"]
    assert "owner may share or make it private" in grant["guidance"]
    assert "admin may only make it private" in grant["guidance"]
    assert connection["agent_target_verbs"] == []
    assert grant["agent_target_verbs"] == ["create", "update"]


async def test_object_verbs_touch_only_the_turn_agents_binding(db: None) -> None:
    """Two agents each bound to the same provider account: through agent A's turn the kind lists
    one object, flipping `shared` shares the connection every attached agent reads, and revoking
    deletes A's binding alone — agent B's binding survives, still reading the shared connection."""
    workspace_id, agent_a, conversation_id, _owner, grantor_id, _other = await _seed()
    agent_b = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_b,
                workspace_id=workspace_id,
                name="exec",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        for agent_id in (agent_a, agent_b):
            await _grant(
                workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
            )
        with agent(agent_a):
            ctx = _tool_context(workspace_id, agent_a, grantor_id)
            listing = json.loads(
                await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_GRANT_KIND)
            )
            assert [row["name"] for row in listing["objects"]] == [GMAIL_ALICE_NAME]

            apply_tool = _object_tool("object_apply")
            await apply_tool.handler(
                ctx,
                apply_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": yaml.safe_dump(
                            {
                                "kind": CONNECTOR_GRANT_KIND,
                                "name": GMAIL_ALICE_NAME,
                                "spec": {
                                    "provider": "gmail",
                                    "account_id": "alice@example.com",
                                    "shared": True,
                                },
                            }
                        ),
                    }
                ),
            )
        flipped = {
            summary.agent: summary.shared
            for summary in await workspace_grant_summaries(workspace_id)
        }
        assert flipped == {"assistant": True, "exec": True}

        with agent(agent_a):
            delete_tool = _object_tool("object_delete")
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": CONNECTOR_GRANT_KIND,
                        "name": GMAIL_ALICE_NAME,
                    }
                ),
            )
        survivors = await workspace_grant_summaries(workspace_id)
        assert [grant.agent for grant in survivors] == ["exec"]
        assert survivors[0].shared is True


async def _second_agent(workspace_id: UUID, name: str) -> UUID:
    second = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second,
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-4-8",
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return second


async def _mark_main(agent_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).values(is_main=True).where(tables.agent.c.id == agent_id)
        )


async def test_main_agent_attaches_an_existing_connection_to_another_agent(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, _other = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _mark_main(agent_id)
        await _second_agent(workspace_id, "pr-babysitter")
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        result = json.loads(
            await _text(
                _object_tool("object_apply"),
                _tool_context(workspace_id, agent_id, grantor_id, conversation_id),
                manifest=_share_manifest("alice@example.com", False),
                agent="pr-babysitter",
            )
        )
        (summary,) = await connection_summaries()
    assert result == {
        "kind": CONNECTOR_GRANT_KIND,
        "name": GMAIL_ALICE_NAME,
        "result": "created",
        "agent": "pr-babysitter",
    }
    assert summary.agents == ("assistant", "pr-babysitter")


async def test_cross_agent_attach_admits_the_owner_or_a_shared_connection_only(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, other_id = await _seed()
    apply_tool = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _mark_main(agent_id)
        await _second_agent(workspace_id, "pr-babysitter")
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        args = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("alice@example.com", False),
                "agent": "pr-babysitter",
            }
        )
        with pytest.raises(ConnectionPermissionDenied, match="cannot attach"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)
    edges = await workspace_grant_summaries(workspace_id)
    assert [edge.agent for edge in edges] == ["assistant"]


async def test_main_agent_flips_sharing_through_another_agents_edge(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, _other = await _seed()
    with ws(workspace_id):
        await _mark_main(agent_id)
        target = await _second_agent(workspace_id, "pr-babysitter")
        await _grant(
            workspace_id, target, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
    with ws(workspace_id), agent(agent_id):
        result = json.loads(
            await _text(
                _object_tool("object_apply"),
                _tool_context(workspace_id, agent_id, grantor_id),
                manifest=_share_manifest("alice@example.com", True),
                agent="pr-babysitter",
            )
        )
    assert result["result"] == "updated"
    assert result["agent"] == "pr-babysitter"
    (edge,) = await workspace_grant_summaries(workspace_id)
    assert edge.agent == "pr-babysitter"
    assert edge.shared is True


async def test_cross_agent_reattach_updates_the_one_existing_edge(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, _other = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _mark_main(agent_id)
        await _second_agent(workspace_id, "pr-babysitter")
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        ctx = _tool_context(workspace_id, agent_id, grantor_id, conversation_id)
        for expected in ("created", "updated"):
            result = json.loads(
                await _text(
                    _object_tool("object_apply"),
                    ctx,
                    manifest=_share_manifest("alice@example.com", False),
                    agent="pr-babysitter",
                )
            )
            assert result["result"] == expected
    edges = await workspace_grant_summaries(workspace_id)
    assert sorted(edge.agent for edge in edges) == ["assistant", "pr-babysitter"]


async def test_only_the_main_agent_attaches_for_another_agent(db: None) -> None:
    workspace_id, agent_id, conversation_id, _admin, grantor_id, _other = await _seed()
    apply_tool = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _second_agent(workspace_id, "pr-babysitter")
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        args = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("alice@example.com", False),
                "agent": "pr-babysitter",
            }
        )
        with pytest.raises(ValueError, match="only the workspace main agent"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, grantor_id), args)


async def test_apply_compares_with_the_current_grant_generation(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    original_get = ConnectorGrantObjects.get
    replacement: UUID | None = None

    async def get_then_regrant(
        self: ConnectorGrantObjects,
        ctx: ToolContext,
        name: str,
    ) -> ObjectDetail[ConnectorGrantSpec] | None:
        nonlocal replacement
        detail = await original_get(self, ctx, name)
        if replacement is None:
            if ctx.grants is None:
                raise RuntimeError("grants unavailable")
            (stale,) = await ctx.grants.active_grants()
            assert await ctx.grants.revoke(stale.id, actor_member_id=grantor_id) is True
            await ctx.grants.record(
                provider="gmail",
                account_id="alice@example.com",
                host="api.gmail.test",
                grantor_member_id=grantor_id,
                conversation_id=conversation_id,
                shared=True,
            )
            (current,) = await ctx.grants.active_grants()
            replacement = current.id
        return detail

    monkeypatch.setattr(ConnectorGrantObjects, "get", get_then_regrant)
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        before = (await grant_summaries())[0].id
        with pytest.raises(ValueError, match="changed while editing"):
            await _text(
                _object_tool("object_apply"),
                _tool_context(workspace_id, agent_id, grantor_id),
                manifest=_share_manifest("alice@example.com", False),
            )
        current = (await grant_summaries())[0]
    assert current.id == replacement
    assert current.id != before
    assert current.shared is True


async def test_get_refuses_a_grant_made_private_after_its_detail_snapshot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, other_id = await _seed()
    real_status = ConnectorGrantObjects.status

    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        grant_id = (await grant_summaries())[0].id
        grants = GrantStore()
        assert await grants.set_shared(grant_id, True, actor_member_id=grantor_id) is True

        async def privatize_before_status(
            store: ConnectorGrantObjects,
            ctx: ToolContext,
            name: str,
            *,
            expected_generation: UUID | None,
        ):
            assert await grants.set_shared(grant_id, False, actor_member_id=grantor_id) is True
            return await real_status(store, ctx, name, expected_generation=expected_generation)

        monkeypatch.setattr(ConnectorGrantObjects, "status", privatize_before_status)
        with pytest.raises(UnknownObject):
            await _text(
                _object_tool("object_get"),
                _tool_context(workspace_id, agent_id, other_id),
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        current = (await grant_summaries())[0]

    assert current.id == grant_id
    assert current.shared is False


async def test_connection_get_and_status_refuse_same_named_replacement(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, other_id = await _seed()
    original_owner = ConnectionObjects._owner

    async def reown_after_authorizing(
        self: ConnectionObjects,
        ctx: ToolContext,
        name: str,
    ) -> GeneratedObjectOwner | None:
        owner = await original_owner(self, ctx, name)
        if owner is None:
            raise AssertionError("connection owner and generation required")
        if ctx.grants is None:
            raise AssertionError("grants required")
        replacement_owner = other_id if owner.member_id == grantor_id else grantor_id
        assert (
            await ctx.grants.disconnect(
                owner.generation,
                actor_member_id=owner.member_id,
            )
            is True
        )
        await ctx.grants.record(
            provider="gmail",
            account_id="alice@example.com",
            host="api.gmail.test",
            grantor_member_id=replacement_owner,
            conversation_id=conversation_id,
            shared=False,
        )
        return owner

    monkeypatch.setattr(ConnectionObjects, "_owner", reown_after_authorizing)
    store = ConnectionObjects()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        name = GMAIL_ALICE_NAME
        read = await original_owner(store, _tool_context(workspace_id, agent_id, grantor_id), name)
        assert read is not None
        assert await store.get(_tool_context(workspace_id, agent_id, grantor_id), name) is None
        assert (await grant_summaries())[0].owner_member_id == other_id
        with pytest.raises(ValueError, match="changed while reading"):
            await store.status(
                _tool_context(workspace_id, agent_id, other_id),
                name,
                expected_generation=read.generation,
            )
        assert (await grant_summaries())[0].owner_member_id == grantor_id


async def test_grant_apply_fails_if_its_generation_is_replaced(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    original_set_shared = GrantStore.set_shared

    async def regrant_then_set_shared(
        self: GrantStore,
        grant_id: UUID,
        shared: bool,
        *,
        actor_member_id: UUID,
    ) -> bool:
        assert await self.revoke(grant_id, actor_member_id=actor_member_id) is True
        await self.record(
            provider="gmail",
            account_id="alice@example.com",
            host="api.gmail.test",
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=False,
        )
        return await original_set_shared(
            self,
            grant_id,
            shared,
            actor_member_id=actor_member_id,
        )

    monkeypatch.setattr(GrantStore, "set_shared", regrant_then_set_shared)
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        stale = (await grant_summaries())[0].id
        with pytest.raises(ValueError, match="changed while editing"):
            await _text(
                _object_tool("object_apply"),
                _tool_context(workspace_id, agent_id, grantor_id),
                manifest=_share_manifest("alice@example.com", True),
            )
        (current,) = await grant_summaries()
    assert current.id != stale
    assert current.shared is False


async def test_grant_apply_fails_if_replaced_before_current_lookup(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    original_owner = ConnectorGrantObjects._owner
    replacement: UUID | None = None

    async def regrant_after_snapshot(
        self: ConnectorGrantObjects,
        ctx: ToolContext,
        name: str,
    ) -> GeneratedObjectOwner | None:
        nonlocal replacement
        owner = await original_owner(self, ctx, name)
        if owner is None:
            raise AssertionError("connector grant owner and generation required")
        if ctx.grants is None:
            raise AssertionError("grants required")
        assert await ctx.grants.revoke(owner.generation, actor_member_id=grantor_id) is True
        await ctx.grants.record(
            provider="gmail",
            account_id="alice@example.com",
            host="api.gmail.test",
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=False,
        )
        (current,) = await ctx.grants.active_grants()
        replacement = current.id
        return owner

    monkeypatch.setattr(ConnectorGrantObjects, "_owner", regrant_after_snapshot)
    store = ConnectorGrantObjects()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        stale = (await grant_summaries())[0].id
        current_spec = ConnectorGrantSpec(
            provider="gmail",
            account_id="alice@example.com",
            shared=False,
        )
        with pytest.raises(ValueError, match="changed while editing"):
            await store.apply(
                _tool_context(workspace_id, agent_id, grantor_id),
                GMAIL_ALICE_NAME,
                current_spec.model_copy(update={"shared": True}),
                current_spec,
                expected_generation=stale,
            )
        (current,) = await grant_summaries()
    assert current.id == replacement
    assert current.id != stale
    assert current.shared is False


async def test_connection_delete_fails_if_its_generation_is_replaced(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    original_disconnect = GrantStore.disconnect

    async def reconnect_then_disconnect(
        self: GrantStore,
        connection_id: UUID,
        *,
        actor_member_id: UUID,
    ) -> bool:
        assert (
            await original_disconnect(
                self,
                connection_id,
                actor_member_id=actor_member_id,
            )
            is True
        )
        await self.record(
            provider="gmail",
            account_id="alice@example.com",
            host="api.gmail.test",
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=False,
        )
        return await original_disconnect(
            self,
            connection_id,
            actor_member_id=actor_member_id,
        )

    monkeypatch.setattr(GrantStore, "disconnect", reconnect_then_disconnect)
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        stale = (await connection_summaries())[0].id
        with pytest.raises(ValueError, match="changed while disconnecting"):
            await _text(
                _object_tool("object_delete"),
                _tool_context(workspace_id, agent_id, grantor_id),
                kind=CONNECTION_KIND,
                name=GMAIL_ALICE_NAME,
            )
        (current,) = await connection_summaries()
        listing = json.loads(
            await _text(
                _object_tool("object_list"),
                _tool_context(workspace_id, agent_id, grantor_id),
                kind=CONNECTION_KIND,
            )
        )
    assert current.id != stale
    assert [row["name"] for row in listing["objects"]] == [GMAIL_ALICE_NAME]


async def test_grant_delete_fails_if_its_generation_is_replaced(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, _admin_id, grantor_id, _other_id = await _seed()
    original_revoke = GrantStore.revoke

    async def regrant_then_revoke(
        self: GrantStore,
        grant_id: UUID,
        *,
        actor_member_id: UUID,
    ) -> bool:
        assert (
            await original_revoke(
                self,
                grant_id,
                actor_member_id=actor_member_id,
            )
            is True
        )
        await self.record(
            provider="gmail",
            account_id="alice@example.com",
            host="api.gmail.test",
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=False,
        )
        return await original_revoke(
            self,
            grant_id,
            actor_member_id=actor_member_id,
        )

    monkeypatch.setattr(GrantStore, "revoke", regrant_then_revoke)
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        stale = (await grant_summaries())[0].id
        with pytest.raises(ValueError, match="changed while revoking"):
            await _text(
                _object_tool("object_delete"),
                _tool_context(workspace_id, agent_id, grantor_id),
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        (current,) = await grant_summaries()
    assert current.id != stale


async def test_connection_is_one_member_owned_object_and_disconnects_every_agent(
    db: None,
) -> None:
    workspace_id, agent_a, conversation_id, owner_id, grantor_id, other_id = await _seed()
    agent_b = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_b,
                workspace_id=workspace_id,
                name="exec",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        for agent_id in (agent_a, agent_b):
            await _grant(
                workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
            )
        with agent(agent_a):
            for member_id, expected in ((other_id, []), (owner_id, [GMAIL_ALICE_NAME])):
                visible = json.loads(
                    await _text(
                        _object_tool("object_list"),
                        _tool_context(workspace_id, agent_a, member_id),
                        kind=CONNECTION_KIND,
                    )
                )
                assert [row["name"] for row in visible["objects"]] == expected
            ctx = _tool_context(workspace_id, agent_a, grantor_id)
            listing = json.loads(
                await _text(_object_tool("object_list"), ctx, kind=CONNECTION_KIND)
            )
            assert [row["name"] for row in listing["objects"]] == [GMAIL_ALICE_NAME]
            fetched = yaml.safe_load(
                await _text(
                    _object_tool("object_get"),
                    ctx,
                    kind=CONNECTION_KIND,
                    name=GMAIL_ALICE_NAME,
                )
            )
            assert fetched["spec"] == {
                "provider": "gmail",
                "account_id": "alice@example.com",
            }
            assert fetched["status"]["agents"] == ["assistant", "exec"]
            await _text(
                _object_tool("object_delete"),
                ctx,
                kind=CONNECTION_KIND,
                name=GMAIL_ALICE_NAME,
            )
        async with workspace_tx() as connection:
            connection_count = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.connection))
            ).scalar_one()
            edge_count = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.connector_grant)
                )
            ).scalar_one()
        assert (connection_count, edge_count) == (0, 0)


async def test_revoke_admits_the_grantor_and_the_owner_only(db: None) -> None:
    workspace_id, agent_id, conversation_id, admin_id, grantor_id, other_id = await _seed()
    delete_tool = _object_tool("object_delete")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        args = delete_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "kind": CONNECTOR_GRANT_KIND,
                "name": GMAIL_ALICE_NAME,
            }
        )
        with pytest.raises(UnknownObject):
            await delete_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)
        with pytest.raises(UnknownObject):
            await delete_tool.handler(_tool_context(workspace_id, agent_id), args)

        await _text(
            _object_tool("object_apply"),
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=_share_manifest("alice@example.com", True),
        )
        with pytest.raises(AdminRequired, match="connection owner or a workspace admin"):
            await delete_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)

        revoked = json.loads(
            await _text(
                delete_tool,
                _tool_context(workspace_id, agent_id, grantor_id),
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        )
        assert revoked["deleted"] is True
        assert revoked["spec"]["account_id"] == "alice@example.com"
        assert await grant_summaries() == ()

        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        await _text(
            delete_tool,
            _tool_context(workspace_id, agent_id, admin_id),
            kind=CONNECTOR_GRANT_KIND,
            name=GMAIL_ALICE_NAME,
        )
        assert await grant_summaries() == ()


def _share_manifest(account_id: str, shared: bool, name: str = GMAIL_ALICE_NAME) -> str:
    return yaml.safe_dump(
        {
            "kind": CONNECTOR_GRANT_KIND,
            "name": name,
            "spec": {"provider": "gmail", "account_id": account_id, "shared": shared},
        }
    )


async def test_the_grantor_shares_their_account_and_get_reflects_it(db: None) -> None:
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    apply_tool = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        await _text(
            apply_tool,
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=_share_manifest("alice@example.com", True),
        )

        summaries = await grant_summaries()
        assert summaries[0].shared is True

        fetched = yaml.safe_load(
            await _text(
                _object_tool("object_get"),
                _tool_context(workspace_id, agent_id),
                kind=CONNECTOR_GRANT_KIND,
                name=GMAIL_ALICE_NAME,
            )
        )
        assert fetched["spec"]["shared"] is True


async def test_only_the_grantor_may_widen_and_an_admin_may_narrow(db: None) -> None:
    workspace_id, agent_id, conversation_id, admin_id, grantor_id, other_id = await _seed()
    apply_tool = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        widen = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("alice@example.com", True),
            }
        )
        with pytest.raises(AdminRequired, match="only the connection owner may share"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, admin_id), widen)
        assert (await grant_summaries())[0].shared is False
        await _text(
            apply_tool,
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=_share_manifest("alice@example.com", True),
        )
        args = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("alice@example.com", False),
            }
        )
        with pytest.raises(AdminRequired, match="owner or a workspace admin"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)

        await _text(
            apply_tool,
            _tool_context(workspace_id, agent_id, admin_id),
            manifest=_share_manifest("alice@example.com", False),
        )
        assert (await grant_summaries())[0].shared is False


async def test_apply_still_refuses_everything_but_the_shared_flip(db: None) -> None:
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    apply_tool = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        different_account = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("bob@example.com", False),
            }
        )
        with pytest.raises(VerbNotSupported, match="connect_account"):
            await apply_tool.handler(
                _tool_context(workspace_id, agent_id, grantor_id), different_account
            )

        create = apply_tool.input_model.model_validate(
            {
                "user_description": TOOL_NARRATION,
                "manifest": _share_manifest("bob@example.com", False, name=GMAIL_BOB_NAME),
            }
        )
        with pytest.raises(ValueError, match="not available"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, grantor_id), create)


async def test_list_summary_names_the_owner_and_sharing(db: None) -> None:
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    grantor_email = f"{grantor_id.hex[:8]}@x.test"
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        listing = json.loads(
            await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_GRANT_KIND)
        )
        assert f"({grantor_email}, private)" in listing["objects"][0]["summary"]
        connection_listing = json.loads(
            await _text(_object_tool("object_list"), ctx, kind=CONNECTION_KIND)
        )
        assert f"({grantor_email}, private)" in connection_listing["objects"][0]["summary"]

        await _text(
            _object_tool("object_apply"),
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=_share_manifest("alice@example.com", True),
        )
        listing = json.loads(
            await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_GRANT_KIND)
        )
        assert f"({grantor_email}, shared)" in listing["objects"][0]["summary"]


async def test_read_verbs_hide_other_members_private_connectors(db: None) -> None:
    workspace_id, agent_id, conversation_id, admin_id, grantor_id, other_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "asana", "bob@example.com"
        )
        await _text(
            _object_tool("object_apply"),
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=yaml.safe_dump(
                {
                    "kind": CONNECTOR_GRANT_KIND,
                    "name": ASANA_BOB_NAME,
                    "spec": {"provider": "asana", "account_id": "bob@example.com", "shared": True},
                }
            ),
        )
        private_name, shared_name = GMAIL_ALICE_NAME, ASANA_BOB_NAME
        get_tool = _object_tool("object_get")

        stranger_ctx = _tool_context(workspace_id, agent_id, other_id)
        listing = json.loads(
            await _text(_object_tool("object_list"), stranger_ctx, kind=CONNECTOR_GRANT_KIND)
        )
        assert [row["name"] for row in listing["objects"]] == [shared_name]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                stranger_ctx,
                get_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": CONNECTOR_GRANT_KIND,
                        "name": private_name,
                    }
                ),
            )

        for ctx in (
            _tool_context(workspace_id, agent_id, grantor_id),
            _tool_context(workspace_id, agent_id, admin_id),
        ):
            listing = json.loads(
                await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_GRANT_KIND)
            )
            assert {row["name"] for row in listing["objects"]} == {private_name, shared_name}
            fetched = yaml.safe_load(
                await _text(get_tool, ctx, kind=CONNECTOR_GRANT_KIND, name=private_name)
            )
            assert fetched["spec"]["account_id"] == "alice@example.com"
            assert fetched["status"]["owner_member_id"] == str(grantor_id)


async def test_portal_reads_hide_other_members_private_connectors(db: None) -> None:
    """The portal answers connections and grants through the same owner gate the verbs do: another
    member's index carries only the shared grant and their detail read of the private one and of
    its connection answers nothing, while the grantor and a workspace admin read both."""
    workspace_id, agent_id, conversation_id, admin_id, grantor_id, other_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "asana", "bob@example.com"
        )
        await _text(
            _object_tool("object_apply"),
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=yaml.safe_dump(
                {
                    "kind": CONNECTOR_GRANT_KIND,
                    "name": ASANA_BOB_NAME,
                    "spec": {"provider": "asana", "account_id": "bob@example.com", "shared": True},
                }
            ),
        )
        grants, connections = ConnectorGrantObjects(), ConnectionObjects()
        stranger = await grants.member_page(
            None, member_id=other_id, admin=False, query=ObjectListQuery()
        )
        assert [row.name for row in stranger.rows] == [ASANA_BOB_NAME]
        assert (
            await grants.member_detail(None, GMAIL_ALICE_NAME, member_id=other_id, admin=False)
        ) is None
        assert (
            await connections.member_detail(None, GMAIL_ALICE_NAME, member_id=other_id, admin=False)
        ) is None
        assert [
            row.name
            for row in (
                await connections.member_page(
                    None, member_id=other_id, admin=False, query=ObjectListQuery()
                )
            ).rows
        ] == []
        for member_id, admin in ((grantor_id, False), (admin_id, True)):
            listed = await grants.member_page(
                None, member_id=member_id, admin=admin, query=ObjectListQuery()
            )
            assert {row.name for row in listed.rows} == {GMAIL_ALICE_NAME, ASANA_BOB_NAME}
            read = await grants.member_detail(
                None, GMAIL_ALICE_NAME, member_id=member_id, admin=admin
            )
            assert read is not None
            assert read.detail.spec.account_id == "alice@example.com"
            opened = await connections.member_detail(
                None, GMAIL_ALICE_NAME, member_id=member_id, admin=admin
            )
            assert opened is not None
            assert opened.detail.spec.provider == "gmail"


async def test_reshare_and_revoke_need_a_live_speaker(db: None) -> None:
    """Resharing (disclosure) and revoking (destructive) a connector are grant acts: a speakerless
    scheduled/subagent turn acting on behalf of the grantor cannot perform them, even though it may
    USE the grantor's private connections. Granting stays speaker-only (RFC 0012)."""
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    apply_tool = _object_tool("object_apply")
    delete_tool = _object_tool("object_delete")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        speakerless = replace(
            _tool_context(workspace_id, agent_id), on_behalf_of_member_id=grantor_id
        )
        with pytest.raises(AdminRequired):
            await apply_tool.handler(
                speakerless,
                apply_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _share_manifest("alice@example.com", True),
                    }
                ),
            )
        with pytest.raises(AdminRequired):
            await delete_tool.handler(
                speakerless,
                delete_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": CONNECTOR_GRANT_KIND,
                        "name": GMAIL_ALICE_NAME,
                    }
                ),
            )
        summaries = await grant_summaries()
    assert len(summaries) == 1
    assert summaries[0].shared is False
