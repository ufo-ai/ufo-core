"""enrichment_consent and enrichment_backoff"""

import sqlalchemy as sa
from alembic import op

revision: str = "enrichment_0002"
down_revision: str | None = "enrichment_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "enrichment_consent",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id"),
    )
    op.create_index("enrichment_consent_workspace", "enrichment_consent", ("workspace_id",))
    op.create_table(
        "enrichment_backoff",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("enrichment_backoff")
    op.drop_index("enrichment_consent_workspace", table_name="enrichment_consent")
    op.drop_table("enrichment_consent")
