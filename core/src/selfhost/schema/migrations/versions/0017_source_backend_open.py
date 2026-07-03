"""source backend open to extension registration"""

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_backend", type_="check")


def downgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.create_check_constraint("source_backend", "backend in ('folder')")
