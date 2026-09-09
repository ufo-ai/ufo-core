"""a page's mirror row goes with the page, and always names the revision it mirrors.

`mem_page` mirrors one synced page's subject and revision so the page pass can join what it already
holds. It is derived state, and its referent is a core `page` row that a disconnect now deletes
outright — its connection's source rows and their pages follow the connection by cascade, with no
tombstone in between. Without a foreign key the mirror row survives the page it mirrors, and nothing
else will ever collect it: the page pass reads mirror rows and can no longer find that page, and no
event names it. The key carries the deletion instead, so removing a page removes its mirror in the
same statement.

`revision` becomes NOT NULL, which is what it has always been in fact: `page.revision` is NOT NULL
with a server default, and the one writer of a mirror row copies it from there. A null could
therefore never be written — and never be read either, since `search_sources` admits a page only
where the mirror's revision equals the live one. The revision is the claim that the indexed chunks
belong to that revision of the body, so a row that cannot make the claim is a row nothing may serve.

Both cleanups run before their constraints, which cannot be created over the rows they exclude. A
mirror carrying no revision is dropped rather than backfilled from the live page: backfilling would
assert a correspondence between the indexed chunks and the current body that nothing here has
checked, which is the stale disclosure `search_sources` exists to refuse. The page pass writes the
row again on that page's next change, exactly as it does for a page it has never seen.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0017"
down_revision: str | None = "memory_0016"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

MEM_PAGE_FK = "mem_page_page_id_fkey"

mem_page = sa.table(
    "mem_page", sa.column("page_id", sa.Uuid()), sa.column("revision", sa.BigInteger())
)
page = sa.table("page", sa.column("id", sa.Uuid()))

# `page_id NOT IN (SELECT page.id …)` is the same set — `page.id` is the key and never null — but
# Postgres will not read it as an anti-join: it materializes `page` and rescans it per mirror row.
STALE_MIRROR = ~sa.exists(sa.select(sa.literal(1)).where(page.c.id == mem_page.c.page_id))


def upgrade() -> None:
    connection = op.get_bind()
    # Read the strays, then delete them by key. Postgres parallelizes no DML, so the anti-join runs
    # once here over two workers and an index-only scan rather than serially inside the delete.
    stale = connection.scalars(sa.select(mem_page.c.page_id).where(STALE_MIRROR)).all()
    if stale:
        connection.execute(sa.delete(mem_page).where(mem_page.c.page_id.in_(stale)))
    connection.execute(sa.delete(mem_page).where(mem_page.c.revision.is_(None)))
    # A database built after RFC 0046 unit C keys `page` by `(workspace_id, uid)`, and no key can
    # name `page (id)` there; `memory_0019` carries the cascade on the new key for every database.
    page_keyed_by_id = connection.dialect.name != "postgresql" or connection.scalar(
        sa.text(
            "select count(*) from pg_constraint where conrelid = 'page'::regclass "
            "and contype = 'p' and pg_get_constraintdef(oid) = 'PRIMARY KEY (id)'"
        )
    )
    with op.batch_alter_table("mem_page") as batch:
        batch.alter_column("revision", existing_type=sa.BigInteger(), nullable=False)
        if page_keyed_by_id:
            batch.create_foreign_key(MEM_PAGE_FK, "page", ["page_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"alter table mem_page drop constraint if exists {MEM_PAGE_FK}")
        op.alter_column("mem_page", "revision", existing_type=sa.BigInteger(), nullable=True)
        return
    with op.batch_alter_table("mem_page") as batch:
        batch.drop_constraint(MEM_PAGE_FK, type_="foreignkey")
        batch.alter_column("revision", existing_type=sa.BigInteger(), nullable=True)
