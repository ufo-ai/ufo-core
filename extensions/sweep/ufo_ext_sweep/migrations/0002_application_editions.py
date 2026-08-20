"""Daily Brief application edition state."""

import sqlalchemy as sa
from alembic import op

revision: str = "sweep_0002"
down_revision: str | None = "sweep_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

SWEEP_EXTENSION = "sweep"
DAILY_BRIEF_AGENT = "daily-brief"

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid()),
    sa.column("provisioned_by", sa.Text()),
    sa.column("provisioned_name", sa.Text()),
)
conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
)
edition = sa.table(
    "sweep_edition",
    sa.column("conversation_id", sa.Uuid()),
    sa.column("turn_id", sa.Uuid()),
)
ledger = sa.table("ledger", sa.column("turn_id", sa.Uuid()))
inbound_message = sa.table(
    "inbound_message",
    sa.column("conversation_id", sa.Uuid()),
    sa.column("admitted_turn_id", sa.Uuid()),
    sa.column("consumed_turn_id", sa.Uuid()),
)
shared_artifact = sa.table("shared_artifact", sa.column("turn_id", sa.Uuid()))
writeback = sa.table("writeback", sa.column("turn_id", sa.Uuid()))
connection = sa.table("connection", sa.column("conversation_id", sa.Uuid()))
connector_grant = sa.table(
    "connector_grant",
    sa.column("agent_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
)
conversation_change = sa.table("conversation_change", sa.column("conversation_id", sa.Uuid()))
proposal = sa.table("proposal", sa.column("agent_id", sa.Uuid()))
scheduled_task = sa.table(
    "scheduled_task",
    sa.column("agent_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
)
source_grant = sa.table("source_grant", sa.column("agent_id", sa.Uuid()))
surface_installation = sa.table("surface_installation", sa.column("agent_id", sa.Uuid()))
transcript_access = sa.table("transcript_access", sa.column("conversation_id", sa.Uuid()))
ext_store = sa.table(
    "ext_store",
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
)


def upgrade() -> None:
    agent_ids = sa.select(agent.c.id).where(
        agent.c.provisioned_by == SWEEP_EXTENSION,
        agent.c.provisioned_name == DAILY_BRIEF_AGENT,
    )
    conversation_ids = sa.select(conversation.c.id).where(conversation.c.agent_id.in_(agent_ids))
    turn_ids = sa.select(turn.c.id).where(
        sa.or_(turn.c.conversation_id.in_(conversation_ids), turn.c.agent_id.in_(agent_ids))
    )
    inspector = sa.inspect(op.get_bind())
    normalized_key = sa.func.replace(ext_store.c.key, "-", "")
    normalized_agent_id = sa.func.replace(sa.cast(agent.c.id, sa.Text()), "-", "")
    normalized_conversation_id = sa.func.replace(sa.cast(conversation.c.id, sa.Text()), "-", "")
    todo_keys = sa.select(sa.literal("todo/") + normalized_conversation_id).where(
        conversation.c.agent_id.in_(agent_ids)
    )
    chat_keys = sa.select(sa.literal("chat/") + normalized_conversation_id).where(
        conversation.c.agent_id.in_(agent_ids)
    )
    web_agent_key = sa.exists(
        sa.select(agent.c.id).where(
            agent.c.provisioned_by == SWEEP_EXTENSION,
            agent.c.provisioned_name == DAILY_BRIEF_AGENT,
            sa.or_(
                normalized_key == sa.literal("homepageseed/") + normalized_agent_id,
                normalized_key.like(sa.literal("audience/") + normalized_agent_id + "/%"),
            ),
        )
    ).correlate(ext_store)
    op.execute(
        sa.update(edition)
        .where(
            sa.or_(edition.c.conversation_id.in_(conversation_ids), edition.c.turn_id.in_(turn_ids))
        )
        .values(conversation_id=None, turn_id=None)
    )
    op.execute(sa.update(ledger).where(ledger.c.turn_id.in_(turn_ids)).values(turn_id=None))
    op.execute(
        sa.delete(inbound_message).where(
            sa.or_(
                inbound_message.c.conversation_id.in_(conversation_ids),
                inbound_message.c.admitted_turn_id.in_(turn_ids),
                inbound_message.c.consumed_turn_id.in_(turn_ids),
            )
        )
    )
    op.execute(sa.delete(shared_artifact).where(shared_artifact.c.turn_id.in_(turn_ids)))
    op.execute(sa.delete(writeback).where(writeback.c.turn_id.in_(turn_ids)))
    if inspector.has_table("mid_turn_reply"):
        mid_turn_reply = sa.table("mid_turn_reply", sa.column("turn_id", sa.Uuid()))
        op.execute(sa.delete(mid_turn_reply).where(mid_turn_reply.c.turn_id.in_(turn_ids)))
    op.execute(sa.delete(connection).where(connection.c.conversation_id.in_(conversation_ids)))
    op.execute(
        sa.delete(connector_grant).where(
            sa.or_(
                connector_grant.c.agent_id.in_(agent_ids),
                connector_grant.c.conversation_id.in_(conversation_ids),
            )
        )
    )
    op.execute(
        sa.delete(conversation_change).where(
            conversation_change.c.conversation_id.in_(conversation_ids)
        )
    )
    op.execute(sa.delete(proposal).where(proposal.c.agent_id.in_(agent_ids)))
    op.execute(
        sa.delete(scheduled_task).where(
            sa.or_(
                scheduled_task.c.agent_id.in_(agent_ids),
                scheduled_task.c.conversation_id.in_(conversation_ids),
            )
        )
    )
    op.execute(sa.delete(source_grant).where(source_grant.c.agent_id.in_(agent_ids)))
    op.execute(
        sa.delete(surface_installation).where(surface_installation.c.agent_id.in_(agent_ids))
    )
    op.execute(
        sa.delete(transcript_access).where(
            transcript_access.c.conversation_id.in_(conversation_ids)
        )
    )
    op.execute(
        sa.delete(ext_store).where(
            sa.or_(
                sa.and_(ext_store.c.extension == "todos", normalized_key.in_(todo_keys)),
                sa.and_(
                    ext_store.c.extension == "web",
                    sa.or_(normalized_key.in_(chat_keys), web_agent_key),
                ),
            )
        )
    )
    if inspector.has_table("user_skill") and "agent_id" in {
        column["name"] for column in inspector.get_columns("user_skill")
    }:
        user_skill = sa.table("user_skill", sa.column("agent_id", sa.Uuid()))
        op.execute(sa.delete(user_skill).where(user_skill.c.agent_id.in_(agent_ids)))
    if inspector.has_table("hosted_site") and "homepage_agent_id" in {
        column["name"] for column in inspector.get_columns("hosted_site")
    }:
        hosted_site = sa.table("hosted_site", sa.column("homepage_agent_id", sa.Uuid()))
        op.execute(
            sa.update(hosted_site)
            .where(hosted_site.c.homepage_agent_id.in_(agent_ids))
            .values(homepage_agent_id=None)
        )
    op.execute(sa.delete(turn).where(turn.c.id.in_(turn_ids)))
    op.execute(sa.delete(conversation).where(conversation.c.id.in_(conversation_ids)))
    op.execute(sa.delete(agent).where(agent.c.id.in_(agent_ids)))
    with op.batch_alter_table("sweep_edition") as batch:
        batch.drop_column("attempt")
        batch.drop_column("conversation_id")
    op.create_table(
        "sweep_application",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_id"],
            ["member.workspace_id", "member.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id"),
        sa.UniqueConstraint("workspace_id", "agent_id", name="sweep_application_agent"),
    )


def downgrade() -> None:
    op.drop_table("sweep_application")
    with op.batch_alter_table("sweep_edition") as batch:
        batch.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
