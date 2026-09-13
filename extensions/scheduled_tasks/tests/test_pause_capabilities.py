from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ufo_ext_scheduled_tasks.manifest import NAME
from ufo_ext_scheduled_tasks.pauses import PauseStore
from ufo_ext_scheduled_tasks.pauses import pause as pause_table

from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite", "postgresql"], indirect=True),
]


async def test_pause_generation_preserves_only_capabilities_written_by_its_arm(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id, outgoing_conversation_id = (
        uuid4() for _ in range(5)
    )
    no_active_conversation_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="speaker@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
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
                queue_key="pause-capability-test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation),
            [
                {
                    "id": outgoing_conversation_id,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "surface": "cli",
                    "queue_key": "pause-outgoing-test",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": no_active_conversation_id,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "surface": "cli",
                    "queue_key": "pause-no-active-test",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.turn),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "seq": 1,
                    "status": "running",
                    "inbound": "wait with no internet",
                    "admission_source": "member",
                    "speaker_member_id": member_id,
                    "runtime_config": {"internet_access": False},
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "conversation_id": outgoing_conversation_id,
                    "agent_id": agent_id,
                    "seq": 1,
                    "status": "running",
                    "inbound": "wait",
                    "admission_source": "member",
                    "speaker_member_id": member_id,
                    "runtime_config": None,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=2,
                status="running",
                inbound="newer unrestricted automatic work",
                admission_source="scheduled",
                speaker_member_id=None,
                runtime_config=None,
                created_at=now,
                updated_at=now,
            )
        )

    store = PauseStore(context_for(NAME, frozenset()))
    with ws(workspace_id), agent(agent_id):
        unrestricted = await store.arm(
            conversation_id=conversation_id,
            agent_id=agent_id,
            resume_at=now - timedelta(seconds=1),
            origin_seq=1,
            origin_arrival_seq=1,
            prompt="resume",
            internet_access=None,
        )
        [claimed] = await store.claim_due(now, 300)
        assert claimed.id == unrestricted.id
        assert claimed.internet_access is None

        replaced = await store.arm(
            conversation_id=conversation_id,
            agent_id=agent_id,
            resume_at=now + timedelta(minutes=1),
            origin_seq=2,
            origin_arrival_seq=2,
            prompt="resume again",
            internet_access=False,
        )
        assert replaced.id != unrestricted.id
        assert replaced.internet_access is False

        baseline = await store.arm(
            conversation_id=conversation_id,
            agent_id=agent_id,
            resume_at=now + timedelta(minutes=1),
            origin_seq=2,
            origin_arrival_seq=2,
            prompt="resume without a ceiling",
            internet_access=None,
        )

        outgoing = sa.Table(
            "pause",
            sa.MetaData(),
            sa.Column("id", sa.Uuid),
            sa.Column("workspace_id", sa.Uuid),
            sa.Column("conversation_id", sa.Uuid),
            sa.Column("agent_id", sa.Uuid),
            sa.Column("resume_at", sa.DateTime(timezone=True)),
            sa.Column("origin_seq", sa.Integer),
            sa.Column("origin_arrival_seq", sa.Integer),
            sa.Column("prompt", sa.Text),
            sa.Column("user_description", sa.Text),
            sa.Column("created_by_member_id", sa.Uuid),
            sa.Column("connections", sa.JSON(none_as_null=True)),
            sa.Column("claimed_by", sa.Text),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
        next_id = uuid4()
        armed = {
            "id": next_id,
            "agent_id": agent_id,
            "resume_at": now + timedelta(minutes=2),
            "origin_seq": 3,
            "origin_arrival_seq": 3,
            "prompt": "outgoing arm",
            "user_description": "outgoing arm",
            "created_by_member_id": member_id,
            "connections": [str(uuid4())],
            "claimed_by": None,
            "claim_expires_at": None,
            "updated_at": now,
        }
        async with workspace_tx() as connection:
            postgres = connection.dialect.name == "postgresql"
            if not postgres:
                rows = (
                    await connection.execute(
                        sa.select(pause_table.c.id, pause_table.c.internet_access).where(
                            pause_table.c.workspace_id == workspace_id,
                        )
                    )
                ).all()
            else:
                insert = pg_insert
                await connection.execute(
                    insert(outgoing)
                    .values(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        created_at=now,
                        **armed,
                    )
                    .on_conflict_do_update(
                        index_elements=[outgoing.c.workspace_id, outgoing.c.conversation_id],
                        set_=armed,
                    )
                )
                outgoing_id = uuid4()
                await connection.execute(
                    insert(outgoing).values(
                        workspace_id=workspace_id,
                        conversation_id=outgoing_conversation_id,
                        created_at=now,
                        **{**armed, "id": outgoing_id},
                    )
                )
                no_active_id = uuid4()
                await connection.execute(
                    insert(outgoing).values(
                        workspace_id=workspace_id,
                        conversation_id=no_active_conversation_id,
                        created_at=now,
                        **{**armed, "id": no_active_id},
                    )
                )
                rows = (
                    await connection.execute(
                        sa.select(pause_table.c.id, pause_table.c.internet_access).where(
                            pause_table.c.workspace_id == workspace_id,
                        )
                    )
                ).all()
        if postgres:
            with pytest.raises(sa.exc.DBAPIError):
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(outgoing)
                        .where(outgoing.c.id == outgoing_id)
                        .values(
                            claimed_by="outgoing-runner",
                            claim_expires_at=now + timedelta(minutes=5),
                        )
                    )
        claimed = await store.claim_due(now + timedelta(minutes=3), 300)
    if postgres:
        assert set(rows) == {(next_id, False), (outgoing_id, True), (no_active_id, False)}
        assert {row.id: row.internet_access for row in claimed} == {
            next_id: False,
            outgoing_id: None,
            no_active_id: False,
        }
    else:
        assert set(rows) == {(baseline.id, True)}
        assert {row.id: row.internet_access for row in claimed} == {baseline.id: None}
