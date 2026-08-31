from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import Sandbox
from ufo.host.ext.loader import turn_tools
from ufo.host.kinds.credential_kind import CREDENTIAL_KIND
from ufo.host.kinds.members import MEMBER_KIND
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.kinds.agents import AGENT_KIND, RestoreApplication, RestoreApplicationInput
from ufo.runtime.object_scope import ObjectActionTarget
from ufo.runtime.tools.context import SpawnResult, TextContent, ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, Agent, CredentialRequest, Turn

ADD_MEMBER_ID = f"action:{MEMBER_KIND}:add_member"
RESTORE_ID = f"action:{AGENT_KIND}:restore_application"
REQUEST_CREDENTIALS_ID = f"action:{CREDENTIAL_KIND}:request_credentials"


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("restore does not spawn")


def test_the_core_actions_register_on_their_kinds_with_their_flags() -> None:
    tools, _ext, verbs = turn_tools(
        (),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )
    add_member = verbs.actions[MEMBER_KIND]["add_member"].action
    assert add_member.canonical_id == ADD_MEMBER_ID
    assert add_member.bound is not None and add_member.bound.binding == "collection"
    assert (add_member.side_effecting, add_member.parallel_safe) == (True, True)
    assert add_member.presentation is not None and add_member.presentation.label == "Add member"
    assert add_member.input_model.model_json_schema()["properties"]["email"]["format"] == "email"

    restore = verbs.actions[AGENT_KIND]["restore_application"].action
    assert restore.canonical_id == RESTORE_ID
    assert restore.bound is not None and restore.bound.binding == "instance"
    assert (restore.side_effecting, restore.parallel_safe) == (True, True)
    assert restore.presentation is not None and restore.presentation.label == "Restore"
    assert set(restore.input_model.model_fields) == {"new_name"}

    request = verbs.actions[CREDENTIAL_KIND]["request_credentials"].action
    assert request.canonical_id == REQUEST_CREDENTIALS_ID
    assert request.bound is not None and request.bound.binding == "collection"
    assert (request.side_effecting, request.parallel_safe) == (False, False)
    assert request.final_act_model is CredentialRequest
    assert request.presentation is not None
    assert {tool.name for tool in tools}.isdisjoint(
        {"add_member", "restore_application", "request_credentials"}
    )


async def test_restore_application_replays_after_its_first_write(db: None) -> None:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    durable_name = f"~archived-{agent_id}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="owner@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=durable_name,
                archived_name="briefer",
                archived_at=sa.func.now(),
                prompt="p",
                model="claude-opus-4-8",
                visibility="private",
                owner_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="restore it",
        admission_source=MEMBER_ADMISSION,
        speaker_member_id=member_id,
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=cast(Sandbox, object()),
        blob=cast(WorkspaceBlobStore, object()),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        target=ObjectActionTarget(
            kind=AGENT_KIND,
            name=durable_name,
            agent=None,
            generation=None,
            expected_generation=None,
        ),
    )
    handler = RestoreApplication().restore
    args = RestoreApplicationInput(new_name="briefer")
    with ws(workspace_id):
        first = await handler(ctx, args)
        replay = await handler(ctx, args)
    assert first == replay
    assert first.content == (TextContent(text="briefer is live again. Its next turn runs it."),)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.agent.c.name,
                    tables.agent.c.archived_at,
                    tables.agent.c.archived_name,
                ).where(tables.agent.c.id == agent_id)
            )
        ).one()
    assert (row.name, row.archived_at, row.archived_name) == ("briefer", None, None)
