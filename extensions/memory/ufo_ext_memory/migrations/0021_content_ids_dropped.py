"""The content-id columns leave the memory tables — RFC 0046 unit D, contract half.

`memory_0019` moved every key onto the `_uid` twins and stopped writing the `_id` columns, keeping
`mem_page.page_id` as the marker for chunks still filed under a content id; `memory_0020` refused
to land while a marker remained. The five columns and the two id-keyed unique indexes go here, once
core's `20260909233938` has dropped the ids they cited. The downgrade brings them back nullable,
which is the shape `memory_0019`'s downgrade restores its keys over.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0021"
down_revision: str | None = "memory_0020"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260909233938"

DROPPED = (
    ("memory_item", ("created_from_page_id", "source_id"), ()),
    (
        "memory_source",
        ("source_id", "page_id"),
        (("memory_source_item_page_id", ("memory_item_id", "page_id")),),
    ),
    ("mem_page", ("page_id",), (("mem_page_page_id", ("page_id",)),)),
)


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, columns, indexes in DROPPED:
        if postgres:
            for name, _key in indexes:
                op.drop_index(name, table_name=table)
            for column in columns:
                op.drop_column(table, column)
            continue
        with op.batch_alter_table(table) as batch:
            for name, _key in indexes:
                batch.drop_constraint(name, type_="unique")
            for column in columns:
                batch.drop_column(column)


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, columns, indexes in DROPPED:
        if postgres:
            for column in columns:
                op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
            for name, key in indexes:
                op.create_index(name, table, list(key), unique=True)
            continue
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.add_column(sa.Column(column, sa.Uuid(), nullable=True))
            for name, key in indexes:
                batch.create_unique_constraint(name, list(key))
