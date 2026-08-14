"""source_trigger"""

from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("sources",)
depends_on: str | None = "0006"

SUBSCRIBERS_PREFIX = "subscribers:"


def upgrade() -> None:
    op.create_table(
        "source_trigger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("binding", sa.Text(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "conversation_id", "binding", name="source_trigger_conversation"
        ),
    )
    op.create_index("source_trigger_binding", "source_trigger", ["workspace_id", "binding"])
    _carry_subscriptions()


def _carry_subscriptions() -> None:
    """Every live subscription becomes the trigger row that now carries it. This extension kept
    them as one `{conversation: agent}` map per binding under `subscribers:<binding>`; each entry
    is one row here. The conversation answers for the agent and the creating member, because the
    row it names is the authority on both and the map only ever cached the agent: a conversation
    that has since gone takes its subscription with it rather than stranding a row no foreign key
    would accept, and a member's own conversation names that member so the trigger stays visible to
    the one person who could have subscribed it. Only once every entry is carried across does the
    map go — nothing else would ever restore it."""
    ext_store = sa.table(
        "ext_store",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("extension", sa.Text()),
        sa.column("key", sa.Text()),
        sa.column("value", sa.JSON()),
    )
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("member_id", sa.Uuid()),
    )
    connection = op.get_bind()
    subscriptions = (
        connection.execute(
            sa.select(ext_store.c.workspace_id, ext_store.c.key, ext_store.c.value).where(
                ext_store.c.extension == "sources",
                ext_store.c.key.like(f"{SUBSCRIBERS_PREFIX}%"),
            )
        )
        .mappings()
        .all()
    )
    carried = []
    for row in subscriptions:
        binding = str(row["key"]).removeprefix(SUBSCRIBERS_PREFIX)
        stored = row["value"]
        if not isinstance(stored, dict):
            continue
        for raw_conversation in stored:
            reported = (
                connection.execute(
                    sa.select(
                        conversation.c.id, conversation.c.agent_id, conversation.c.member_id
                    ).where(
                        conversation.c.id == UUID(raw_conversation),
                        conversation.c.workspace_id == row["workspace_id"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            if reported is None:
                continue
            carried.append(
                {
                    "id": uuid4(),
                    "workspace_id": row["workspace_id"],
                    "conversation_id": reported["id"],
                    "agent_id": reported["agent_id"],
                    "binding": binding,
                    "created_by_member_id": reported["member_id"],
                }
            )
    if carried:
        rows = sa.table(
            "source_trigger",
            sa.column("id", sa.Uuid()),
            sa.column("workspace_id", sa.Uuid()),
            sa.column("conversation_id", sa.Uuid()),
            sa.column("agent_id", sa.Uuid()),
            sa.column("binding", sa.Text()),
            sa.column("created_by_member_id", sa.Uuid()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        connection.execute(
            sa.insert(rows).values(created_at=sa.func.now(), updated_at=sa.func.now()), carried
        )
    connection.execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == "sources",
            ext_store.c.key.like(f"{SUBSCRIBERS_PREFIX}%"),
        )
    )


def downgrade() -> None:
    op.drop_index("source_trigger_binding", "source_trigger")
    op.drop_table("source_trigger")
