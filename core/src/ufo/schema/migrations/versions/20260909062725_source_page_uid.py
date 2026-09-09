"""`source` and `page` carry a surrogate id beside the content-addressed one.

Both primary keys embed a parent's id — `source.id` its connection's, `page.id` its source's — so
re-addressing a connection rewrites every row beneath it and every row citing them, which is what
`20260907150257` did with the fleet stopped. `uid` is the identity that survives a re-address: the
writers mint it time-ordered (UUIDv7) so inserts land at the index's right edge; the backfill only
needs distinct values, so historical rows take a random one. Unique with the workspace, which is
the shape a partition key will need. RFC 0046 unit A; nothing reads `uid` until the citers do.
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260909062725"
down_revision: str | None = "20260909042656"
branch_labels: str | None = None
depends_on: str | None = None

TABLES = ("source", "page")

# A SQLite batch recreate drops the table's triggers with the table; page carries two.
PAGE_TRIGGERS = ("page_assign_revision_insert", "page_assign_revision_update")
PAGE_REVISION_INSERT_TRIGGER = """
create trigger page_assign_revision_insert
after insert on page
begin
    update workspace
    set page_revision = page_revision + 1
    where id = new.workspace_id;
    update page
    set revision = (
        select page_revision from workspace where id = new.workspace_id
    )
    where id = new.id;
end
"""

PAGE_REVISION_UPDATE_TRIGGER = """
create trigger page_assign_revision_update
after update of digest, body_ref, subject, tombstone on page
when new.digest is not old.digest
  or new.body_ref is not old.body_ref
  or new.subject is not old.subject
  or new.tombstone is not old.tombstone
begin
    update workspace
    set page_revision = page_revision + 1
    where id = new.workspace_id;
    update page
    set revision = (
        select page_revision from workspace where id = new.workspace_id
    )
    where id = new.id;
end
"""


def _unique(table: str) -> str:
    return f"{table}_workspace_uid"


def _check(table: str) -> str:
    return f"{table}_uid_present"


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    for table in TABLES:
        op.add_column(table, sa.Column("uid", sa.Uuid(), nullable=True))
        if postgres:
            op.execute(f"update {table} set uid = gen_random_uuid() where uid is null")
            # Postgres 12+ lets a validated CHECK stand in for `SET NOT NULL`'s own scan, so the
            # scan runs under SHARE UPDATE EXCLUSIVE instead of the exclusive lock.
            op.execute(
                f"alter table {table} add constraint {_check(table)} "
                f"check (uid is not null) not valid"
            )
            op.execute(f"alter table {table} validate constraint {_check(table)}")
            op.alter_column(table, "uid", existing_type=sa.Uuid(), nullable=False)
            op.execute(f"alter table {table} drop constraint {_check(table)}")
            # In-transaction on purpose: CONCURRENTLY waits for every open transaction in the
            # database to end, and one idle session can hold it forever.
            op.create_unique_constraint(_unique(table), table, ["workspace_id", "uid"])
        else:
            rows = sa.table(table, sa.column("id", sa.Uuid()))
            for (row_id,) in bind.execute(sa.select(rows.c.id)).all():
                bind.execute(
                    sa.text(f"update {table} set uid = :uid where id = :id"),
                    {"uid": uuid4().hex, "id": row_id.hex if hasattr(row_id, "hex") else row_id},
                )
            if table == "page":
                for trigger in PAGE_TRIGGERS:
                    op.execute(f"drop trigger {trigger}")
            with op.batch_alter_table(table) as batch:
                batch.alter_column("uid", existing_type=sa.Uuid(), nullable=False)
            if table == "page":
                op.execute(PAGE_REVISION_INSERT_TRIGGER)
                op.execute(PAGE_REVISION_UPDATE_TRIGGER)
            op.create_index(_unique(table), table, ["workspace_id", "uid"], unique=True)


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table in reversed(TABLES):
        if postgres:
            op.drop_constraint(_unique(table), table, type_="unique")
        else:
            op.drop_index(_unique(table), table_name=table)
        if not postgres and table == "page":
            for trigger in PAGE_TRIGGERS:
                op.execute(f"drop trigger {trigger}")
        with op.batch_alter_table(table) as batch:
            batch.drop_column("uid")
        if not postgres and table == "page":
            op.execute(PAGE_REVISION_INSERT_TRIGGER)
            op.execute(PAGE_REVISION_UPDATE_TRIGGER)
