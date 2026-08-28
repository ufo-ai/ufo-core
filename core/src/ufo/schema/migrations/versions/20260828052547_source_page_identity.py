import sqlalchemy as sa
from alembic import op

revision: str = "20260828052547"
down_revision: str | None = "20260828045120"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("page", sa.Column("source_identity", sa.Text(), nullable=True))
    op.create_index(
        "page_source_identity",
        "page",
        ["source_id", "source_identity"],
        unique=True,
        postgresql_where=sa.text("source_identity is not null"),
        sqlite_where=sa.text("source_identity is not null"),
    )


def downgrade() -> None:
    op.drop_index("page_source_identity", table_name="page")
    op.drop_column("page", "source_identity")
