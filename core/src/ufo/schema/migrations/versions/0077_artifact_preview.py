"""carry a shared document's rendered first page beside its bytes"""

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The columns land first and the constraint second: a batch that adds a column and constrains it
    # depends on columns that batch is still introducing, which SQLite cannot order.
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(sa.Column("preview_blob_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("preview_media_type", sa.Text(), nullable=True))
        batch.add_column(sa.Column("preview_size_bytes", sa.BigInteger(), nullable=True))
    with op.batch_alter_table("shared_artifact") as batch:
        batch.create_check_constraint(
            "shared_artifact_preview",
            "(preview_blob_key is null) = (preview_media_type is null) "
            "and (preview_blob_key is null) = (preview_size_bytes is null) "
            "and (preview_size_bytes is null or preview_size_bytes >= 0)",
        )


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_constraint("shared_artifact_preview", type_="check")
        batch.drop_column("preview_size_bytes")
        batch.drop_column("preview_media_type")
        batch.drop_column("preview_blob_key")
