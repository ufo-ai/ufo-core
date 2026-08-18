import sqlalchemy as sa
from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "surface_listener_claim",
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_token", sa.Uuid(), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("surface <> ''", name="surface_listener_claim_surface_nonempty"),
        sa.ForeignKeyConstraint(["owner_id"], ["runtime_instance.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("surface"),
    )


def downgrade() -> None:
    op.drop_table("surface_listener_claim")
