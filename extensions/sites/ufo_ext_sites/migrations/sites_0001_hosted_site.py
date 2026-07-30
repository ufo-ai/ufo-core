"""hosted_site"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("sites",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "hosted_site",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("creator_member_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "name"),
        sa.CheckConstraint(
            "visibility in ('private', 'workspace', 'public')", name="hosted_site_visibility"
        ),
    )
    op.create_index(
        "hosted_site_origin",
        "hosted_site",
        ["workspace_id", "conversation_id", "port"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("hosted_site_origin", "hosted_site")
    op.drop_table("hosted_site")
