"""Index the workspace file projection used by artifact listings."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913112228"
down_revision: str | None = "20260913095851"
branch_labels: str | None = None
depends_on: str | None = None

INDEX = "shared_artifact_files"
COLUMNS = ["workspace_id", "filename", "created_at", "blob_key"]
WHERE = "role = 'file'"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                INDEX,
                "shared_artifact",
                COLUMNS,
                postgresql_where=sa.text(WHERE),
                postgresql_concurrently=True,
            )
        return
    op.create_index(
        INDEX,
        "shared_artifact",
        COLUMNS,
        sqlite_where=sa.text(WHERE),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(INDEX, table_name="shared_artifact", postgresql_concurrently=True)
        return
    op.drop_index(INDEX, table_name="shared_artifact")
