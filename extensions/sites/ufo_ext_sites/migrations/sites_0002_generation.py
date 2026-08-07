"""hosted site generations"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0002"
down_revision: str | None = "sites_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hosted_site", sa.Column("generation", sa.Uuid(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("select workspace_id, conversation_id, name from hosted_site")
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                "update hosted_site set generation = :generation "
                "where workspace_id = :workspace_id and conversation_id = :conversation_id "
                "and name = :name"
            ),
            {
                "workspace_id": str(row["workspace_id"]),
                "conversation_id": str(row["conversation_id"]),
                "name": row["name"],
                "generation": str(uuid4()),
            },
        )
    with op.batch_alter_table("hosted_site") as batch:
        batch.alter_column("generation", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("generation")
