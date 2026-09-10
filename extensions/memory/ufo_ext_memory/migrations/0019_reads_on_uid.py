"""The memory rows are keyed by the page and source ids their citers now read: the `_uid` twins.

`memory_0018` gave every citing column a nullable `_uid` twin filled from its parent. Here the twins
become the keys — required, primary, and the join `live_page_link` and the reach fence run on — and
the content-addressed `_id` columns stop being written, so they become nullable and keep only the
unique indexes the release being replaced still names as `ON CONFLICT` arbiters. `mem_page` follows
its page by `(workspace_id, page_uid)` exactly as it did by `page_id`, and the key on `page_id` goes
with the last release that wrote the column. RFC 0046 unit B.

Two kinds of row cannot make the move. A twin still null after the join backfill names a parent
that no longer exists: a `memory_source` link to a deleted page or source retracts nothing and is
dropped; a `memory_item` whose page or source is gone is retired — the page pass's own verdict on a
fact whose page moved on — and its whole binding cleared, ids and twins alike, so it satisfies the
check in either form and stops being served (every serving read fences on `retired_at`). The twins
are re-synced from the ids first: the replaced release re-points a row's ids on re-derivation
without touching the twins it never read.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0019"
down_revision: str | None = "memory_0018"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TWINS = (
    ("memory_item", "source_id", "source_uid", "source"),
    ("memory_item", "created_from_page_id", "created_from_page_uid", "page"),
    ("memory_source", "source_id", "source_uid", "source"),
    ("memory_source", "page_id", "page_uid", "page"),
)
PAGE_SOURCE_CHECK = "memory_item_page_source"
CHECK_ON_IDS = (
    "(created_from_page_id is null and created_from_page_revision is null "
    "and source_id is null) or (created_from_page_id is not null "
    "and created_from_page_revision is not null and source_id is not null)"
)
CHECK_ON_UIDS = (
    "(created_from_page_uid is null and created_from_page_revision is null "
    "and source_uid is null) or (created_from_page_uid is not null "
    "and created_from_page_revision is not null and source_uid is not null)"
)

memory_item = sa.table(
    "memory_item",
    sa.column("created_from_page_id", sa.Uuid()),
    sa.column("created_from_page_uid", sa.Uuid()),
    sa.column("created_from_page_revision", sa.BigInteger()),
    sa.column("source_id", sa.Uuid()),
    sa.column("source_uid", sa.Uuid()),
    sa.column("embedding_digest", sa.Text()),
    sa.column("embedding_claimed_at", sa.DateTime(timezone=True)),
    sa.column("retired_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
memory_source = sa.table(
    "memory_source",
    sa.column("source_uid", sa.Uuid()),
    sa.column("page_id", sa.Uuid()),
    sa.column("page_uid", sa.Uuid()),
)
mem_page = sa.table("mem_page", sa.column("page_id", sa.Uuid()))


def _resync_twins() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    # A database built after RFC 0046 unit D has no content id to re-sync from, and no row.
    if "id" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("page")}:
        return
    for table, by, column, parent in TWINS:
        if postgres:
            op.execute(
                f"update {table} set {column} = {parent}.uid from {parent} "
                f"where {parent}.id = {table}.{by} "
                f"and {table}.{column} is distinct from {parent}.uid"
            )
        else:
            op.execute(
                f"update {table} set {column} = "
                f"(select uid from {parent} where {parent}.id = {table}.{by}) "
                f"where {by} is not null"
            )


def _settle_orphans() -> None:
    connection = op.get_bind()
    orphaned_links = connection.execute(
        sa.delete(memory_source).where(
            sa.or_(memory_source.c.page_uid.is_(None), memory_source.c.source_uid.is_(None))
        )
    ).rowcount
    orphaned_items = connection.execute(
        sa.update(memory_item)
        .where(
            memory_item.c.created_from_page_id.is_not(None),
            sa.or_(
                memory_item.c.created_from_page_uid.is_(None), memory_item.c.source_uid.is_(None)
            ),
        )
        .values(
            created_from_page_id=None,
            created_from_page_uid=None,
            created_from_page_revision=None,
            source_id=None,
            source_uid=None,
            embedding_digest=None,
            embedding_claimed_at=None,
            retired_at=sa.func.coalesce(memory_item.c.retired_at, sa.func.now()),
            updated_at=sa.func.now(),
        )
    ).rowcount
    print(f"memory_0019: {orphaned_links} links and {orphaned_items} facts had lost their page")


def upgrade() -> None:
    _resync_twins()
    _settle_orphans()
    if op.get_bind().dialect.name == "postgresql":
        _upgrade_postgres()
    else:
        _upgrade_sqlite()


def _settle_uid_only_rows() -> None:
    """A row this release wrote carries no content id, so the shape being restored cannot hold it:
    its links and mirror go, and the fact is retired with its binding cleared, as an orphan is."""
    connection = op.get_bind()
    connection.execute(sa.delete(memory_source).where(memory_source.c.page_id.is_(None)))
    connection.execute(sa.delete(mem_page).where(mem_page.c.page_id.is_(None)))
    connection.execute(
        sa.update(memory_item)
        .where(
            memory_item.c.created_from_page_id.is_(None),
            memory_item.c.created_from_page_uid.is_not(None),
        )
        .values(
            created_from_page_uid=None,
            created_from_page_revision=None,
            source_uid=None,
            embedding_digest=None,
            embedding_claimed_at=None,
            retired_at=sa.func.coalesce(memory_item.c.retired_at, sa.func.now()),
            updated_at=sa.func.now(),
        )
    )


def _upgrade_postgres() -> None:
    op.drop_constraint(PAGE_SOURCE_CHECK, "memory_item", type_="check")
    op.create_check_constraint(PAGE_SOURCE_CHECK, "memory_item", CHECK_ON_UIDS)

    # The replaced release still names (memory_item_id, page_id) as its ON CONFLICT arbiter, so
    # that key survives as a unique index while the primary key moves onto the uid twin.
    op.create_index(
        "memory_source_item_page_id", "memory_source", ["memory_item_id", "page_id"], unique=True
    )
    op.create_index(
        "memory_source_item_page_uid", "memory_source", ["memory_item_id", "page_uid"], unique=True
    )
    op.execute(
        "alter table memory_source drop constraint memory_source_pkey, "
        "add constraint memory_source_pkey primary key using index memory_source_item_page_uid"
    )
    op.alter_column("memory_source", "page_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("memory_source", "source_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("memory_source", "source_uid", existing_type=sa.Uuid(), nullable=False)

    op.create_index("mem_page_page_id", "mem_page", ["page_id"], unique=True)
    op.alter_column("mem_page", "page_uid", existing_type=sa.Uuid(), nullable=False)
    op.execute(
        "alter table mem_page drop constraint mem_page_pkey, "
        "add constraint mem_page_pkey primary key (page_uid)"
    )
    op.alter_column("mem_page", "page_id", existing_type=sa.Uuid(), nullable=True)
    op.execute("alter table mem_page drop constraint if exists mem_page_page_id_fkey")
    op.create_foreign_key(
        "mem_page_page_uid_fkey",
        "mem_page",
        "page",
        ["workspace_id", "page_uid"],
        ["workspace_id", "uid"],
        ondelete="CASCADE",
    )


def _upgrade_sqlite() -> None:
    # SQLite cannot alter a PRIMARY KEY in place and alembic's batch mode drops an unnamed one on
    # the floor, so each table is rebuilt in its final shape and its rows copied across.
    with op.batch_alter_table("memory_item", recreate="always") as batch:
        batch.drop_constraint(PAGE_SOURCE_CHECK, type_="check")
        batch.create_check_constraint(PAGE_SOURCE_CHECK, CHECK_ON_UIDS)
    _rebuild_sqlite(
        "memory_source",
        """CREATE TABLE memory_source (
            workspace_id UUID NOT NULL,
            memory_item_id UUID NOT NULL,
            source_id UUID,
            source_uid UUID NOT NULL,
            page_id UUID,
            page_uid UUID NOT NULL,
            revision BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            CONSTRAINT memory_source_pkey PRIMARY KEY (memory_item_id, page_uid),
            CONSTRAINT memory_source_item_page_id UNIQUE (memory_item_id, page_id),
            CONSTRAINT memory_source_memory_item_id_fkey FOREIGN KEY(memory_item_id) """
        """REFERENCES memory_item (id) ON DELETE CASCADE
        )""",
        "workspace_id, memory_item_id, source_id, source_uid, page_id, page_uid, revision, "
        "created_at, updated_at",
    )
    _rebuild_sqlite(
        "mem_page",
        """CREATE TABLE mem_page (
            page_uid UUID NOT NULL,
            page_id UUID,
            workspace_id UUID NOT NULL,
            subject TEXT NOT NULL,
            revision BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            CONSTRAINT mem_page_pkey PRIMARY KEY (page_uid),
            CONSTRAINT mem_page_page_id UNIQUE (page_id),
            CONSTRAINT mem_page_workspace_id_fkey FOREIGN KEY(workspace_id) """
        """REFERENCES workspace (id) ON DELETE CASCADE,
            CONSTRAINT mem_page_page_uid_fkey FOREIGN KEY(workspace_id, page_uid) """
        """REFERENCES page (workspace_id, uid) ON DELETE CASCADE
        )""",
        "page_uid, page_id, workspace_id, subject, revision, created_at",
    )


def _rebuild_sqlite(table: str, create: str, columns: str) -> None:
    op.execute(f"alter table {table} rename to {table}__old")
    op.execute(create)
    op.execute(f"insert into {table} ({columns}) select {columns} from {table}__old")
    op.execute(f"drop table {table}__old")


def downgrade() -> None:
    _settle_uid_only_rows()
    if op.get_bind().dialect.name != "postgresql":
        _downgrade_sqlite()
        return
    op.drop_constraint("mem_page_page_uid_fkey", "mem_page", type_="foreignkey")
    op.execute(
        "alter table mem_page drop constraint mem_page_pkey, "
        "add constraint mem_page_pkey primary key (page_id)"
    )
    op.drop_index("mem_page_page_id", table_name="mem_page")
    op.alter_column("mem_page", "page_id", existing_type=sa.Uuid(), nullable=False)
    # The key on the content id comes back only where `page` is still keyed by it; a `page` unit C
    # has partitioned never carried it, since memory_0017 skips it there as well.
    page_keyed_by_id = op.get_bind().scalar(
        sa.text(
            "select count(*) from pg_constraint where conrelid = 'page'::regclass "
            "and contype = 'p' and pg_get_constraintdef(oid) = 'PRIMARY KEY (id)'"
        )
    )
    if page_keyed_by_id:
        op.create_foreign_key(
            "mem_page_page_id_fkey", "mem_page", "page", ["page_id"], ["id"], ondelete="CASCADE"
        )
    op.alter_column("mem_page", "page_uid", existing_type=sa.Uuid(), nullable=True)
    # Adopting an index as the primary key renames it to the constraint, so dropping the constraint
    # is what drops the uid-keyed index here, and the id-keyed one takes the constraint's name.
    op.execute(
        "alter table memory_source drop constraint memory_source_pkey, "
        "add constraint memory_source_pkey primary key using index memory_source_item_page_id"
    )
    op.alter_column("memory_source", "page_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("memory_source", "source_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("memory_source", "page_uid", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("memory_source", "source_uid", existing_type=sa.Uuid(), nullable=True)
    op.drop_constraint(PAGE_SOURCE_CHECK, "memory_item", type_="check")
    op.create_check_constraint(PAGE_SOURCE_CHECK, "memory_item", CHECK_ON_IDS)


def _downgrade_sqlite() -> None:
    with op.batch_alter_table("memory_item", recreate="always") as batch:
        batch.drop_constraint(PAGE_SOURCE_CHECK, type_="check")
        batch.create_check_constraint(PAGE_SOURCE_CHECK, CHECK_ON_IDS)
    _rebuild_sqlite(
        "memory_source",
        """CREATE TABLE memory_source (
            workspace_id UUID NOT NULL,
            memory_item_id UUID NOT NULL,
            source_id UUID NOT NULL,
            page_id UUID NOT NULL,
            revision BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            source_uid UUID,
            page_uid UUID,
            CONSTRAINT memory_source_pkey PRIMARY KEY (memory_item_id, page_id),
            CONSTRAINT memory_source_memory_item_id_fkey FOREIGN KEY(memory_item_id) """
        """REFERENCES memory_item (id) ON DELETE CASCADE
        )""",
        "workspace_id, memory_item_id, source_id, source_uid, page_id, page_uid, revision, "
        "created_at, updated_at",
    )
    _rebuild_sqlite(
        "mem_page",
        """CREATE TABLE mem_page (
            page_id UUID NOT NULL,
            subject TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            workspace_id UUID NOT NULL,
            revision BIGINT NOT NULL,
            page_uid UUID,
            PRIMARY KEY (page_id),
            CONSTRAINT mem_page_workspace_id_fkey FOREIGN KEY(workspace_id) """
        """REFERENCES workspace (id) ON DELETE CASCADE,
            CONSTRAINT mem_page_page_id_fkey FOREIGN KEY(page_id) """
        """REFERENCES page (id) ON DELETE CASCADE
        )""",
        "page_uid, page_id, workspace_id, subject, revision, created_at",
    )
