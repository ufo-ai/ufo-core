"""slack"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_index(
        "turn_idempotency_key", "turn", ["workspace_id", "idempotency_key"], unique=True
    )
    with op.batch_alter_table("conversation") as batch:
        batch.alter_column("member_id", existing_type=sa.Uuid(), nullable=True)
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint(
            "conversation_surface", "surface in ('cli', 'subagent', 'slack')"
        )
    with op.batch_alter_table("surface_identity") as batch:
        batch.drop_constraint("surface_identity_surface", type_="check")
        batch.create_check_constraint("surface_identity_surface", "surface in ('cli', 'slack')")
    op.create_table(
        "writeback",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reply_ref", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.CheckConstraint(
            "status in ('pending', 'claimed', 'delivered', 'failed')", name="writeback_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("writeback")
    with op.batch_alter_table("surface_identity") as batch:
        batch.drop_constraint("surface_identity_surface", type_="check")
        batch.create_check_constraint("surface_identity_surface", "surface in ('cli')")
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint("conversation_surface", "surface in ('cli', 'subagent')")
        batch.alter_column("member_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("turn_idempotency_key", "turn")
    op.drop_column("turn", "idempotency_key")
