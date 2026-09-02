"""enrichment_profile"""

import sqlalchemy as sa
from alembic import op

revision: str = "enrichment_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("enrichment",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "enrichment_profile",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("person", sa.JSON(), nullable=True),
        sa.Column("company", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('matched', 'no_match')", name="enrichment_profile_status"),
        sa.CheckConstraint("source in ('pdl', 'recorded')", name="enrichment_profile_source"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id"),
    )
    op.create_index("enrichment_profile_workspace", "enrichment_profile", ("workspace_id",))


def downgrade() -> None:
    op.drop_index("enrichment_profile_workspace", table_name="enrichment_profile")
    op.drop_table("enrichment_profile")
