import sqlalchemy as sa
from alembic import op

revision: str = "20260906070956"
down_revision: str | None = "20260904064451"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(sa.Column("role", sa.Text(), nullable=False, server_default="file"))
        batch.create_check_constraint("shared_artifact_role", "role in ('file', 'details')")


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_constraint("shared_artifact_role", type_="check")
        batch.drop_column("role")
