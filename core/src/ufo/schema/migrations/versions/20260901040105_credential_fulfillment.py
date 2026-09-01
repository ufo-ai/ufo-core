"""record each sealed credential fulfillment once"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260901040105"
down_revision: str | None = "20260830013444"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "credential_fulfillment",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_id"],
            ["member.workspace_id", "member.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "request_id", "slot"),
    )


def downgrade() -> None:
    op.drop_table("credential_fulfillment")
