import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import turn_tools
from ufo.host.kinds.member_permissions import MEMBER_PERMISSION_KIND, MEMBER_PERMISSION_OBJECT
from ufo.runtime.objects import MemberListable, ObjectListQuery, VerbNotSupported
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError


async def _seed() -> tuple[UUID, UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, alice_id, bob_id, permission_id = (uuid4() for _ in range(5))
    conversation_id, granted_by = uuid4(), uuid4()
    permission_digest = uuid4().hex
    permission_scope_digest = uuid4().hex
    permission_scope = {
        "provider": "gmail",
        "account_id": "alice@example.com",
        "operation": "GMAIL_SEND_EMAIL",
        "access": "write",
    }
    granting_effect = {
        "call": "send_email",
        "arguments": {"to": "team@example.com"},
        "target": None,
    }
    now = datetime(2026, 9, 13, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="Operator",
                prompt="p",
                model="gpt-5.6-terra",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": email,
                    "created_at": now,
                    "updated_at": now,
                }
                for member_id, email in (
                    (alice_id, "alice@example.com"),
                    (bob_id, "bob@example.com"),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                queue_key="channel:C1",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.member_authorization).values(
                id=uuid4(),
                workspace_id=workspace_id,
                member_id=alice_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                call="send_email",
                effect_digest=permission_digest,
                effect=granting_effect,
                scope_digest=permission_scope_digest,
                scope=permission_scope,
                binding_digest=uuid4().hex,
                request_key="grant-request",
                decision_key="grant-request",
                requested_by=granted_by,
                decided_by=granted_by,
                decision="always",
                basis="selected_message",
                evidence="Always send this email",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.member_permission),
            [
                {
                    "id": permission_id,
                    "workspace_id": workspace_id,
                    "member_id": alice_id,
                    "agent_id": agent_id,
                    "call": "send_email",
                    "effect_digest": permission_digest,
                    "effect": granting_effect,
                    "scope_digest": permission_scope_digest,
                    "scope": permission_scope,
                    "granted_by": granted_by,
                    "revoked_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "member_id": bob_id,
                    "agent_id": agent_id,
                    "call": "send_email",
                    "effect_digest": uuid4().hex,
                    "effect": {"call": "send_email", "arguments": {}},
                    "scope_digest": uuid4().hex,
                    "scope": {
                        "provider": "gmail",
                        "account_id": "bob@example.com",
                        "operation": "GMAIL_SEND_EMAIL",
                        "access": "write",
                    },
                    "granted_by": uuid4(),
                    "revoked_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "member_id": alice_id,
                    "agent_id": agent_id,
                    "call": "publish",
                    "effect_digest": uuid4().hex,
                    "effect": {"call": "publish", "arguments": {}},
                    "scope_digest": uuid4().hex,
                    "scope": {
                        "provider": "web",
                        "account_id": "example.com",
                        "operation": "publish",
                        "access": "write",
                    },
                    "granted_by": uuid4(),
                    "revoked_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "member_id": alice_id,
                    "agent_id": agent_id,
                    "call": "send_email",
                    "effect_digest": uuid4().hex,
                    "effect": {
                        "call": "send_email",
                        "arguments": {"to": "old@example.com"},
                        "target": None,
                    },
                    "scope_digest": None,
                    "scope": None,
                    "granted_by": uuid4(),
                    "revoked_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    return workspace_id, agent_id, alice_id, bob_id, permission_id


def _context(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> ToolContext:
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
            inbound="show my permissions",
            created_at=datetime(2026, 9, 13, tzinfo=UTC),
        ),
        agent=Agent(name="Operator", prompt="p", model="gpt-5.6-terra"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
    )


def _tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
    assert result.is_error is False
    return result.content[0].text


async def test_member_reads_only_their_active_account_permissions(db: None) -> None:
    workspace_id, agent_id, alice_id, bob_id, permission_id = await _seed()
    with ws(workspace_id):
        alice = _context(workspace_id, agent_id, alice_id)
        listing = json.loads(await _text(_tool("object_list"), alice, kind=MEMBER_PERMISSION_KIND))
        assert listing["objects"] == [
            {
                "ref": f"permission/{permission_id}",
                "name": str(permission_id),
                "summary": "Operator: send_email",
                "agent": "Operator",
                "call": "send_email",
            }
        ]

        detail = yaml.safe_load(
            await _text(
                _tool("object_get"),
                alice,
                ref=f"{MEMBER_PERMISSION_KIND}/{permission_id}",
            )
        )
        assert detail["spec"] == {
            "agent": "Operator",
            "call": "send_email",
            "scope": {
                "provider": "gmail",
                "account_id": "alice@example.com",
                "operation": "GMAIL_SEND_EMAIL",
                "access": "write",
            },
            "basis": "selected_message",
            "evidence": "Always send this email",
        }
        assert detail["status"] == {"active": True, "call": "send_email"}

        store = MEMBER_PERMISSION_OBJECT.store
        assert isinstance(store, MemberListable)
        page = await store.member_page(
            None,
            member_id=alice_id,
            admin=False,
            query=ObjectListQuery(supported_fields=MEMBER_PERMISSION_OBJECT.list_fields),
        )
        assert [row.name for row in page.rows] == [str(permission_id)]
        projected = await store.member_detail(
            None, str(permission_id), member_id=alice_id, admin=False
        )
        assert projected is not None
        assert projected.detail.spec == MEMBER_PERMISSION_OBJECT.spec_model.model_validate(
            detail["spec"]
        )

        bob_listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, agent_id, bob_id),
                kind=MEMBER_PERMISSION_KIND,
            )
        )
        assert len(bob_listing["objects"]) == 1
        assert bob_listing["objects"][0]["name"] != str(permission_id)


async def test_member_reads_the_decision_that_granted_a_permission(db: None) -> None:
    workspace_id, agent_id, alice_id, _, permission_id = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member_authorization).values(
                id=uuid4(),
                workspace_id=workspace_id,
                member_id=alice_id,
                agent_id=agent_id,
                conversation_id=(
                    await connection.execute(
                        sa.select(tables.conversation.c.id).where(
                            tables.conversation.c.workspace_id == workspace_id
                        )
                    )
                ).scalar_one(),
                call="send_email",
                effect_digest=uuid4().hex,
                effect={"call": "send_email", "arguments": {"to": "new@example.com"}},
                request_key="standing-use",
                decision_key="standing-use",
                requested_by=uuid4(),
                decided_by=uuid4(),
                decision="allow",
                basis="standing",
                evidence="",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        detail = yaml.safe_load(
            await _text(
                _tool("object_get"),
                _context(workspace_id, agent_id, alice_id),
                ref=f"{MEMBER_PERMISSION_KIND}/{permission_id}",
            )
        )
    assert detail["spec"]["basis"] == "selected_message"
    assert detail["spec"]["evidence"] == "Always send this email"


async def test_member_revokes_but_cannot_create_a_permission(db: None) -> None:
    workspace_id, agent_id, alice_id, _, permission_id = await _seed()
    with ws(workspace_id):
        alice = _context(workspace_id, agent_id, alice_id)
        deleted = json.loads(
            await _text(
                _tool("object_delete"),
                alice,
                kind=MEMBER_PERMISSION_KIND,
                name=str(permission_id),
            )
        )
        assert deleted == {
            "kind": MEMBER_PERMISSION_KIND,
            "name": str(permission_id),
            "deleted": True,
            "spec": {
                "agent": "Operator",
                "call": "send_email",
                "scope": {
                    "provider": "gmail",
                    "account_id": "alice@example.com",
                    "operation": "GMAIL_SEND_EMAIL",
                    "access": "write",
                },
                "basis": "selected_message",
                "evidence": "Always send this email",
            },
        }

        listing = json.loads(await _text(_tool("object_list"), alice, kind=MEMBER_PERMISSION_KIND))
        assert listing["objects"] == []

        apply = _tool("object_apply")
        manifest = yaml.safe_dump(
            {
                "kind": MEMBER_PERMISSION_KIND,
                "name": str(uuid4()),
                "spec": {
                    "agent": "Operator",
                    "call": "send_email",
                    "scope": {
                        "provider": "gmail",
                        "account_id": "alice@example.com",
                        "operation": "GMAIL_SEND_EMAIL",
                        "access": "write",
                    },
                    "basis": "selected_message",
                    "evidence": "Always send this email",
                },
            }
        )
        try:
            await apply.handler(alice, apply.input_model.model_validate({"manifest": manifest}))
        except VerbNotSupported as error:
            assert str(error) == (
                "permissions are granted in conversation, not through object manifests"
            )
        else:
            raise AssertionError("permission creation was admitted")

    async with workspace_tx() as connection:
        revoked_at = (
            await connection.execute(
                sa.select(tables.member_permission.c.revoked_at).where(
                    tables.member_permission.c.id == permission_id
                )
            )
        ).scalar_one()
    assert revoked_at is not None
