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


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.delete(mem_page).where(mem_page.c.page_id.not_in(sa.select(page.c.id))))
    connection.execute(sa.delete(mem_page).where(mem_page.c.revision.is_(None)))
    with op.batch_alter_table("mem_page") as batch:
        batch.alter_column("revision", existing_type=sa.BigInteger(), nullable=False)
        batch.create_foreign_key(MEM_PAGE_FK, "page", ["page_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("mem_page") as batch:
        batch.drop_constraint(MEM_PAGE_FK, type_="foreignkey")
        batch.alter_column("revision", existing_type=sa.BigInteger(), nullable=True)
