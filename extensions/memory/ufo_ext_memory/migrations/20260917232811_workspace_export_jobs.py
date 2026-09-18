import sqlalchemy as sa
from alembic import op

revision: str = "20260917232811"
down_revision: str | None = "memory_0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_export",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("active", sa.Integer()),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "active", name="workspace_export_active"),
        sa.CheckConstraint("active IS NULL OR active = 1", name="workspace_export_active_value"),
        sa.CheckConstraint(
            "status IN ('queued', 'preparing', 'ready', 'failed', 'expired')",
            name="workspace_export_status",
        ),
    )
    op.create_index("workspace_export_created", "workspace_export", ["workspace_id", "created_at"])
    op.create_index("workspace_export_expiry", "workspace_export", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_table("workspace_export")
