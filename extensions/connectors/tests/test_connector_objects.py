"""The `connector` object kind end to end: real grant rows read and revoked through the object
verbs. Seeded rows stand where the OAuth callback writes them; the tests drive list/get/status
through the real tool dispatch, prove create is refused naming `connect_account`, and prove the
revoke gate — the grantor may revoke their own account, an unrelated member may not, the owner
may revoke anyone's."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from ufo_ext_connectors.manifest import manifest
from ufo_ext_connectors.objects import CONNECTOR_KIND

from ufo.agent_scope import agent
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.grants import GrantStore, grant_summaries, workspace_grant_summaries
from ufo.objects import OwnerRequired, UnknownObject, VerbNotSupported
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

OWNER_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
GRANTOR_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)
OTHER_CREATED_AT = datetime(2026, 7, 3, tzinfo=UTC)
SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object verbs must not spawn a subagent")


async def _seed() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    owner_id, grantor_id, other_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member_id, created_at in (
            (owner_id, OWNER_CREATED_AT),
            (grantor_id, GRANTOR_CREATED_AT),
            (other_id, OTHER_CREATED_AT),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=f"{member_id.hex[:8]}@x.test",
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
                member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id, owner_id, grantor_id, other_id


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
    workspace_id: UUID, agent_id: UUID, speaker_member_id: UUID | None = None
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
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience_member_id=None,
        artifact_token_secret="",
        grants=GrantStore(),
    )


def _object_tool(name: str) -> ToolDef:
    tools, _ = turn_tools((manifest(),), None)
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
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
                sa.update(tables.grant).values(
                    created_at=datetime(2026, 7, 10, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 11, tzinfo=UTC),
                )
            )
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_KIND))
        assert [row["name"] for row in listing["objects"]] == ["gmail-alice-example-com"]
        assert "alice@example.com" in listing["objects"][0]["summary"]

        fetched = yaml.safe_load(
            await _text(
                _object_tool("object_get"),
                ctx,
                kind=CONNECTOR_KIND,
                name="gmail-alice-example-com",
            )
        )
        assert fetched["spec"] == {
            "provider": "gmail",
            "account_id": "alice@example.com",
            "shared": False,
        }
        assert fetched["status"]["grantor_member_id"] == str(grantor_id)
        assert fetched["status"]["host"] == "api.gmail.test"
        assert fetched["status"]["agent"] == "assistant"
        assert datetime.fromisoformat(fetched["created_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 10, tzinfo=UTC
        )
        assert datetime.fromisoformat(fetched["updated_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 11, tzinfo=UTC
        )


async def test_connect_stays_the_only_create_path(db: None) -> None:
    workspace_id, agent_id, *_ = await _seed()
    apply_tool = _object_tool("object_apply")
    args = apply_tool.input_model.model_validate(
        {
            "manifest": yaml.safe_dump(
                {
                    "kind": CONNECTOR_KIND,
                    "name": "gmail-alice",
                    "spec": {"provider": "gmail", "account_id": "alice"},
                }
            )
        }
    )
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(VerbNotSupported, match="connect_account"),
    ):
        await apply_tool.handler(_tool_context(workspace_id, agent_id), args)


async def test_object_verbs_touch_only_the_turn_agents_binding(db: None) -> None:
    """Two agents each bound to the same provider account: through agent A's turn the kind lists
    one object, flipping `shared` flips A's row alone, and revoking deletes A's binding alone —
    agent B's row keeps its own disclosure and survives, so no verb crosses the wall."""
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
            listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_KIND))
            assert [row["name"] for row in listing["objects"]] == ["gmail-alice-example-com"]

            apply_tool = _object_tool("object_apply")
            await apply_tool.handler(
                ctx,
                apply_tool.input_model.model_validate(
                    {
                        "manifest": yaml.safe_dump(
                            {
                                "kind": CONNECTOR_KIND,
                                "name": "gmail-alice-example-com",
                                "spec": {
                                    "provider": "gmail",
                                    "account_id": "alice@example.com",
                                    "shared": True,
                                },
                            }
                        )
                    }
                ),
            )
        flipped = {
            grant.agent: grant.shared for grant in await workspace_grant_summaries(workspace_id)
        }
        assert flipped == {"assistant": True, "exec": False}

        with agent(agent_a):
            delete_tool = _object_tool("object_delete")
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate(
                    {"kind": CONNECTOR_KIND, "name": "gmail-alice-example-com"}
                ),
            )
        survivors = await workspace_grant_summaries(workspace_id)
        assert [grant.agent for grant in survivors] == ["exec"]
        assert survivors[0].shared is False


async def test_revoke_admits_the_grantor_and_the_owner_only(db: None) -> None:
    workspace_id, agent_id, conversation_id, owner_id, grantor_id, other_id = await _seed()
    delete_tool = _object_tool("object_delete")
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        args = delete_tool.input_model.model_validate(
            {"kind": CONNECTOR_KIND, "name": "gmail-alice-example-com"}
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
        with pytest.raises(OwnerRequired, match="grantor or the workspace owner"):
            await delete_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)

        revoked = json.loads(
            await _text(
                delete_tool,
                _tool_context(workspace_id, agent_id, grantor_id),
                kind=CONNECTOR_KIND,
                name="gmail-alice-example-com",
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
            _tool_context(workspace_id, agent_id, owner_id),
            kind=CONNECTOR_KIND,
            name="gmail-alice-example-com",
        )
        assert await grant_summaries() == ()


def _share_manifest(account_id: str, shared: bool, name: str = "gmail-alice-example-com") -> str:
    return yaml.safe_dump(
        {
            "kind": CONNECTOR_KIND,
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
                kind=CONNECTOR_KIND,
                name="gmail-alice-example-com",
            )
        )
        assert fetched["spec"]["shared"] is True


async def test_an_unrelated_member_may_not_flip_sharing_but_the_owner_may(db: None) -> None:
    workspace_id, agent_id, conversation_id, owner_id, grantor_id, other_id = await _seed()
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
        args = apply_tool.input_model.model_validate(
            {"manifest": _share_manifest("alice@example.com", False)}
        )
        with pytest.raises(OwnerRequired, match="grantor or the workspace owner"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, other_id), args)

        await _text(
            apply_tool,
            _tool_context(workspace_id, agent_id, owner_id),
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
            {"manifest": _share_manifest("bob@example.com", False)}
        )
        with pytest.raises(VerbNotSupported, match="connect_account"):
            await apply_tool.handler(
                _tool_context(workspace_id, agent_id, grantor_id), different_account
            )

        create = apply_tool.input_model.model_validate(
            {"manifest": _share_manifest("bob@example.com", False, name="gmail-bob-example-com")}
        )
        with pytest.raises(VerbNotSupported, match="connect_account"):
            await apply_tool.handler(_tool_context(workspace_id, agent_id, grantor_id), create)


async def test_list_summary_tags_private_and_shared_accounts(db: None) -> None:
    workspace_id, agent_id, conversation_id, _owner, grantor_id, _other = await _seed()
    with ws(workspace_id), agent(agent_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        ctx = _tool_context(workspace_id, agent_id, grantor_id)
        listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_KIND))
        assert "(private)" in listing["objects"][0]["summary"]

        await _text(
            _object_tool("object_apply"),
            _tool_context(workspace_id, agent_id, grantor_id),
            manifest=_share_manifest("alice@example.com", True),
        )
        listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_KIND))
        assert "(shared)" in listing["objects"][0]["summary"]


async def test_read_verbs_hide_other_members_private_connectors(db: None) -> None:
    workspace_id, agent_id, conversation_id, owner_id, grantor_id, other_id = await _seed()
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
                    "kind": CONNECTOR_KIND,
                    "name": "asana-bob-example-com",
                    "spec": {"provider": "asana", "account_id": "bob@example.com", "shared": True},
                }
            ),
        )
        private_name, shared_name = "gmail-alice-example-com", "asana-bob-example-com"
        get_tool = _object_tool("object_get")

        stranger_ctx = _tool_context(workspace_id, agent_id, other_id)
        listing = json.loads(
            await _text(_object_tool("object_list"), stranger_ctx, kind=CONNECTOR_KIND)
        )
        assert [row["name"] for row in listing["objects"]] == [shared_name]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                stranger_ctx,
                get_tool.input_model.model_validate({"kind": CONNECTOR_KIND, "name": private_name}),
            )

        for ctx in (
            _tool_context(workspace_id, agent_id, grantor_id),
            _tool_context(workspace_id, agent_id, owner_id),
        ):
            listing = json.loads(await _text(_object_tool("object_list"), ctx, kind=CONNECTOR_KIND))
            assert {row["name"] for row in listing["objects"]} == {private_name, shared_name}
            fetched = yaml.safe_load(
                await _text(get_tool, ctx, kind=CONNECTOR_KIND, name=private_name)
            )
            assert fetched["spec"]["account_id"] == "alice@example.com"
            assert fetched["status"]["grantor_member_id"] == str(grantor_id)


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
        with pytest.raises(OwnerRequired):
            await apply_tool.handler(
                speakerless,
                apply_tool.input_model.model_validate(
                    {"manifest": _share_manifest("alice@example.com", True)}
                ),
            )
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(
                speakerless,
                delete_tool.input_model.model_validate(
                    {"kind": CONNECTOR_KIND, "name": "gmail-alice-example-com"}
                ),
            )
        summaries = await grant_summaries()
    assert len(summaries) == 1
    assert summaries[0].shared is False
