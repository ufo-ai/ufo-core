"""The `connector` object kind end to end: real grant rows read and revoked through the object
verbs. Seeded rows stand where the OAuth callback writes them; the tests drive list/get/status
through the real tool dispatch, prove create is refused naming `connect_account`, and prove the
revoke gate — the grantor may revoke their own account, an unrelated member may not, the owner
may revoke anyone's."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from ufo_ext_connectors.manifest import manifest
from ufo_ext_connectors.objects import CONNECTOR_KIND

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.grants import GrantStore, grant_summaries
from ufo.objects import OwnerRequired, VerbNotSupported
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

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
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
    await GrantStore().record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider=provider,
        account_id=account_id,
        host=f"api.{provider}.test",
        grantor_member_id=grantor_id,
        conversation_id=conversation_id,
    )


def _tool_context(workspace_id: UUID, speaker_member_id: UUID | None = None) -> ToolContext:
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
            agent_id=uuid4(),
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
    with ws(workspace_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        ctx = _tool_context(workspace_id)
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
        assert fetched["spec"] == {"provider": "gmail", "account_id": "alice@example.com"}
        assert fetched["status"]["grantor_member_id"] == str(grantor_id)
        assert fetched["status"]["host"] == "api.gmail.test"
        assert fetched["status"]["agent"] == "assistant"


async def test_connect_stays_the_only_create_path(db: None) -> None:
    workspace_id, *_ = await _seed()
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
    with ws(workspace_id), pytest.raises(VerbNotSupported, match="connect_account"):
        await apply_tool.handler(_tool_context(workspace_id), args)


async def test_revoke_admits_the_grantor_and_the_owner_only(db: None) -> None:
    workspace_id, agent_id, conversation_id, owner_id, grantor_id, other_id = await _seed()
    delete_tool = _object_tool("object_delete")
    with ws(workspace_id):
        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        args = delete_tool.input_model.model_validate(
            {"kind": CONNECTOR_KIND, "name": "gmail-alice-example-com"}
        )
        with pytest.raises(OwnerRequired, match="grantor or the workspace owner"):
            await delete_tool.handler(_tool_context(workspace_id, other_id), args)
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(_tool_context(workspace_id), args)

        revoked = json.loads(
            await _text(
                delete_tool,
                _tool_context(workspace_id, grantor_id),
                kind=CONNECTOR_KIND,
                name="gmail-alice-example-com",
            )
        )
        assert revoked["deleted"] is True
        assert revoked["spec"]["account_id"] == "alice@example.com"
        assert await grant_summaries(workspace_id) == ()

        await _grant(
            workspace_id, agent_id, conversation_id, grantor_id, "gmail", "alice@example.com"
        )
        await _text(
            delete_tool,
            _tool_context(workspace_id, owner_id),
            kind=CONNECTOR_KIND,
            name="gmail-alice-example-com",
        )
        assert await grant_summaries(workspace_id) == ()
