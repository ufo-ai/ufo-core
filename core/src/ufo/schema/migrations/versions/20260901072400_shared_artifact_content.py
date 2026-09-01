"""Record the content and request that a durable artifact identity names."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260901072400"
down_revision: str | None = "20260901040105"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(sa.Column("request_fingerprint", sa.Text(), nullable=True))
        batch.add_column(sa.Column("digest", sa.Text(), nullable=True))
        batch.add_column(sa.Column("is_text", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_column("is_text")
        batch.drop_column("digest")
        batch.drop_column("request_fingerprint")
