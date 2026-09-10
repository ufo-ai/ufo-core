"""Every row citing a page or a source carries the parent's surrogate id beside the content one.

`20260909062725` gave `source` and `page` a `uid` that survives a re-address. The rows here cite
them by the content-addressed `id`, which is what a re-address rewrites — so each citing column
gains a
`_uid` twin, filled from the parent it already names. Every twin stays nullable through this
revision: a writer that does not yet carry one may still land its row, and unit B, which moves the
joins onto the twins, is where they become required. RFC 0046 unit A.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0018"
down_revision: str | None = "memory_0017"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260909062725"

CITERS = (
    ("memory_item", "source_id", "source_uid", "source"),
    ("memory_item", "created_from_page_id", "created_from_page_uid", "page"),
    ("memory_source", "source_id", "source_uid", "source"),
    ("memory_source", "page_id", "page_uid", "page"),
    ("mem_page", "page_id", "page_uid", "page"),
)


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    # A database built after RFC 0046 unit D has no content id to fill the twins from, and no row.
    content_ids = "id" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("page")
    }
    for table, by, column, parent in CITERS:
        op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
        if not content_ids:
            continue
        if postgres:
            op.execute(
                f"update {table} set {column} = {parent}.uid from {parent} "
                f"where {parent}.id = {table}.{by}"
            )
        else:
            op.execute(
                f"update {table} set {column} = "
                f"(select uid from {parent} where {parent}.id = {table}.{by})"
            )


def downgrade() -> None:
    for table, _by, column, _parent in reversed(CITERS):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
