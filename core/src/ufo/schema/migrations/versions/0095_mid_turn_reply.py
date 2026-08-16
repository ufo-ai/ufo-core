"""one delivery record per reply a turn speaks mid-flight

A durable surface could be written to once per turn, from the terminal writeback. A turn that
answers a member before it ends needs a record per reply instead: the row is the claim a poller
delivers under, and its span identity — the turn, the run attempt, the round, and the position
inside it, derived into the primary key the way a ledger row's is — is what makes a replayed turn, a
racing replica, and a redelivered event post it exactly once.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mid_turn_reply",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=False),
        sa.Column("round_index", sa.Integer, nullable=False),
        sa.Column("span_index", sa.Integer, nullable=False),
        sa.Column("message_ref", sa.Uuid, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reply_ref", sa.Text, nullable=True),
        sa.Column("claimed_by", sa.Text, nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'claimed', 'delivered', 'failed')", name="mid_turn_reply_status"
        ),
    )
    op.create_index(
        "mid_turn_reply_due",
        "mid_turn_reply",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("status in ('pending', 'claimed')"),
        sqlite_where=sa.text("status in ('pending', 'claimed')"),
    )


def downgrade() -> None:
    op.drop_index("mid_turn_reply_due", table_name="mid_turn_reply")
    op.drop_table("mid_turn_reply")
