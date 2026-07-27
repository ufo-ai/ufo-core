"""bind page derivations to page revisions"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0010"
down_revision: str | None = "memory_0009"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.add_column(sa.Column("created_from_page_revision", sa.BigInteger(), nullable=True))
    with op.batch_alter_table("mem_page") as batch:
        batch.add_column(sa.Column("revision", sa.BigInteger(), nullable=True))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "update memory_item set embedding_digest = null, embedding_claimed_at = null "
            "where created_from_page_id is not null"
        )
    )
    connection.execute(sa.text("delete from mem_page"))
    connection.execute(
        sa.text(
            "delete from ext_store where extension = 'memory' "
            "and key in ('page_change_cursor:index_pages', "
            "'page_change_cursor:derive_facts')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("mem_page") as batch:
        batch.drop_column("revision")
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_column("created_from_page_revision")
