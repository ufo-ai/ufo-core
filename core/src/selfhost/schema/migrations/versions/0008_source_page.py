"""source + page"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("backend in ('folder')", name="source_backend"),
    )
    op.create_index("source_due", "source", ["next_sync_at"])
    op.create_table(
        "page",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("body_ref", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("embedding_digest", sa.Text(), nullable=True),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["source.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "subject = 'shared' or subject like 'member:%'", name="page_subject"
        ),
    )
    op.create_index("page_due", "page", ["embedding_digest"])
    op.create_index("page_source", "page", ["source_id"])


def downgrade() -> None:
    op.drop_index("page_source", "page")
    op.drop_index("page_due", "page")
    op.drop_table("page")
    op.drop_index("source_due", "source")
    op.drop_table("source")
