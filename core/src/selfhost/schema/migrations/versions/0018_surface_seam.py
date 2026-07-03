"""surface seam"""

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
    with op.batch_alter_table("surface_identity") as batch:
        batch.drop_constraint("surface_identity_surface", type_="check")
    op.create_table(
        "shared_artifact",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("turn_id", "blob_key"),
        sa.CheckConstraint("size_bytes >= 0", name="shared_artifact_size"),
    )


def downgrade() -> None:
    op.drop_table("shared_artifact")
    with op.batch_alter_table("surface_identity") as batch:
        batch.create_check_constraint(
            "surface_identity_surface", "surface in ('cli', 'slack', 'web')"
        )
    with op.batch_alter_table("conversation") as batch:
        batch.create_check_constraint(
            "conversation_surface", "surface in ('cli', 'subagent', 'slack', 'web')"
        )
