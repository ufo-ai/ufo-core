"""shared artifact row identity"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | None = None
depends_on: str | None = None

ARTIFACT = sa.table(
    "shared_artifact",
    sa.column("id", sa.Uuid),
    sa.column("turn_id", sa.Uuid),
    sa.column("blob_key", sa.Text),
)


def upgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(sa.Column("id", sa.Uuid, nullable=True))
    connection = op.get_bind()
    shared = connection.execute(sa.select(ARTIFACT.c.turn_id, ARTIFACT.c.blob_key)).all()
    for turn_id, blob_key in shared:
        connection.execute(
            sa.update(ARTIFACT)
            .where(ARTIFACT.c.turn_id == turn_id, ARTIFACT.c.blob_key == blob_key)
            .values(id=uuid4())
        )
    with op.batch_alter_table("shared_artifact") as batch:
        batch.alter_column("id", nullable=False)
        batch.create_unique_constraint("shared_artifact_id", ["id"])


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_constraint("shared_artifact_id", type_="unique")
        batch.drop_column("id")
