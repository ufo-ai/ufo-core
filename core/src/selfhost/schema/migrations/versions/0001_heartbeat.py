"""heartbeat"""

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "workspace",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name"),
    )
    op.create_table(
        "member",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "email"),
    )
    op.create_table(
        "conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("queue_key", sa.Text(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("surface in ('cli')", name="conversation_surface"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("surface", "queue_key"),
    )
    op.create_table(
        "surface_identity",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("surface in ('cli')", name="surface_identity_surface"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("surface", "external_id"),
    )
    op.create_table(
        "turn",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("inbound", sa.Text(), nullable=False),
        sa.Column("terminal", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status in ('queued', 'running')) = (terminal is null)", name="turn_terminal"
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed', 'cancelled')", name="turn_status"
        ),
        sa.CheckConstraint("seq >= 1", name="turn_seq"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "seq"),
    )
    op.create_table(
        "ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("priced_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dimension in ('tokens')", name="ledger_dimension"),
        sa.CheckConstraint("amount > 0", name="ledger_amount"),
        sa.CheckConstraint("priced_micro_usd >= 0", name="ledger_priced"),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ledger_turn", "ledger", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ledger_turn", "ledger")
    op.drop_table("ledger")
    op.drop_table("turn")
    op.drop_table("surface_identity")
    op.drop_table("conversation")
    op.drop_table("member")
    op.drop_table("agent")
    op.drop_table("workspace")
