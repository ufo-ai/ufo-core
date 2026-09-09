"""Cut `source` and `page` over to their partitioned shadows — the contract half of RFC 0046 unit C.

Everything happens in one transaction under exclusive locks on the two live tables: the mirror
triggers go, the live tables step aside as `source_old` and `page_old`, the shadows take their
names (partitions, indexes and constraints renamed to the canonical ones the old tables held), the
revision trigger is recreated on the new `page`, and every foreign key another table held on the
old tables is re-created against the new ones from its own definition. A rename is a catalog
write, so the fleet's statements — already conflicting on `(workspace_id, id)` — resume against
the new tables without a beat. The old tables stay until unit D drops them.

SQLite carries no partitions, so it rebuilds the two tables in the same logical shape — primary key
`(workspace_id, uid)`, `page → source` by `(workspace_id, source_uid)` — and recreates the two
revision triggers a rebuild drops. `legacy_alter_table` keeps `mem_page`'s foreign key text
pointing at `page` while the live table is renamed out from under it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260909195911"
down_revision: str | None = "20260909193737"
branch_labels: str | None = None
depends_on: str | None = None

TABLES = ("source", "page")
PARTITIONS = 16
KEY = ("workspace_id", "uid")
RENAMES = {
    "source": {
        "source_new_pkey": "source_pkey",
        "source_new_workspace_identity": "source_workspace_identity",
        "source_new_feed_handle": "source_feed_handle",
        "source_new_workspace_id_fkey": "source_workspace_id_fkey",
        "source_new_authority_fkey": "source_authority_fkey",
        "source_new_due": "source_due",
        "source_new_authority": "source_authority",
        "source_new_id": "source_id",
    },
    "page": {
        "page_new_pkey": "page_pkey",
        "page_new_workspace_identity": "page_workspace_identity",
        "page_new_workspace_id_fkey": "page_workspace_id_fkey",
        "page_new_source_fkey": "page_source_fkey",
        "page_new_feed": "page_feed",
        "page_new_source": "page_source",
        "page_new_source_id": "page_source_id",
        "page_new_id": "page_id",
        "page_new_source_identity": "page_source_identity",
    },
}
REVISION_TRIGGER = """
create trigger page_assign_revision
before insert or update of digest, body_ref, subject, tombstone on page
for each row execute function assign_page_revision()
"""
SQLITE_SOURCE = "\n".join(
    (
        "CREATE TABLE source (",
        "    id CHAR(32) NOT NULL,",
        "    uid CHAR(32) NOT NULL,",
        "    workspace_id CHAR(32) NOT NULL,",
        "    backend TEXT NOT NULL,",
        "    config JSON NOT NULL,",
        "    feed_handle TEXT NOT NULL,",
        "    connection_id CHAR(32) NOT NULL,",
        "    cursor TEXT,",
        "    next_sync_at DATETIME NOT NULL,",
        "    consecutive_errors INTEGER DEFAULT '0' NOT NULL,",
        "    consecutive_refusals INTEGER DEFAULT '0' NOT NULL,",
        "    consecutive_empty INTEGER DEFAULT '0' NOT NULL,",
        "    parked_at DATETIME,",
        "    parked_reason TEXT,",
        "    claimed_by TEXT,",
        "    claim_expires_at DATETIME,",
        "    created_at DATETIME NOT NULL,",
        "    updated_at DATETIME NOT NULL,",
        "    CONSTRAINT source_pkey PRIMARY KEY (workspace_id, uid),",
        "    CONSTRAINT source_workspace_identity UNIQUE (workspace_id, id),",
        "    CONSTRAINT source_feed_handle "
        "UNIQUE (workspace_id, connection_id, backend, feed_handle),",
        "    CONSTRAINT source_workspace_id_fkey FOREIGN KEY(workspace_id) "
        "REFERENCES workspace (id),",
        "    CONSTRAINT source_authority_fkey FOREIGN KEY(workspace_id, connection_id) "
        "REFERENCES connection (workspace_id, id) ON DELETE CASCADE",
        ")",
    )
)
SQLITE_SOURCE_COLUMNS = (
    "id, uid, workspace_id, backend, config, feed_handle, connection_id, cursor, next_sync_at, "
    "consecutive_errors, consecutive_refusals, consecutive_empty, parked_at, parked_reason, "
    "claimed_by, claim_expires_at, created_at, updated_at"
)
SQLITE_PAGE = "\n".join(
    (
        "CREATE TABLE page (",
        "    id CHAR(32) NOT NULL,",
        "    uid CHAR(32) NOT NULL,",
        "    workspace_id CHAR(32) NOT NULL,",
        "    source_id CHAR(32) NOT NULL,",
        "    source_uid CHAR(32) NOT NULL,",
        "    source_identity TEXT,",
        "    digest TEXT NOT NULL,",
        "    body_ref TEXT NOT NULL,",
        "    stream TEXT DEFAULT ('') NOT NULL,",
        "    title TEXT DEFAULT ('') NOT NULL,",
        "    record_created_at TEXT,",
        "    record_updated_at TEXT,",
        "    subject TEXT NOT NULL,",
        "    revision BIGINT DEFAULT '0' NOT NULL,",
        "    tombstone BOOLEAN NOT NULL,",
        "    created_at DATETIME NOT NULL,",
        "    updated_at DATETIME NOT NULL,",
        "    CONSTRAINT page_pkey PRIMARY KEY (workspace_id, uid),",
        "    CONSTRAINT page_workspace_identity UNIQUE (workspace_id, id),",
        "    CONSTRAINT page_subject CHECK (subject = 'shared' or subject like 'member:%'),",
        "    CONSTRAINT page_workspace_id_fkey FOREIGN KEY(workspace_id) "
        "REFERENCES workspace (id),",
        "    CONSTRAINT page_source_fkey FOREIGN KEY(workspace_id, source_uid) "
        "REFERENCES source (workspace_id, uid) ON DELETE CASCADE",
        ")",
    )
)
SQLITE_PAGE_COLUMNS = (
    "id, uid, workspace_id, source_id, source_uid, source_identity, digest, body_ref, stream, "
    "title, record_created_at, record_updated_at, subject, revision, tombstone, created_at, "
    "updated_at"
)
SQLITE_INDEXES = (
    "create index source_due on source (next_sync_at)",
    "create index source_authority on source (workspace_id, connection_id)",
    "create index page_feed on page (workspace_id, revision, uid)",
    "create index page_source on page (workspace_id, source_uid)",
    "create index page_source_id on page (source_id)",
    "create unique index page_source_identity on page (workspace_id, source_uid, source_identity) "
    "where source_identity is not null",
)
SQLITE_REVISION_TRIGGERS = (
    """
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
    where workspace_id = new.workspace_id and uid = new.uid;
end
""",
    """
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
    where workspace_id = new.workspace_id and uid = new.uid;
end
""",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        _upgrade_sqlite()
        return
    op.execute("lock table source, page in access exclusive mode")
    for table in TABLES:
        op.execute(f"drop trigger {table}_mirror on {table}")
        op.execute(f"drop function mirror_{table}_new()")
    inbound = _inbound_keys()
    for table, name, _definition in inbound:
        op.execute(f"alter table {table} drop constraint {name}")
    for table in TABLES:
        for name in _live_indexes(table):
            op.execute(f"alter index if exists {name} rename to {name}_old")
        for name in _constraints_of(table):
            op.execute(f"alter table {table} rename constraint {name} to {name}_old")
        op.execute(f"alter table {table} rename to {table}_old")
        op.execute(f"alter table {table}_new rename to {table}")
        for remainder in range(PARTITIONS):
            op.execute(
                f"alter table {table}_new_p{remainder:02d} rename to {table}_p{remainder:02d}"
            )
        for shadow_name, canonical in RENAMES[table].items():
            if shadow_name in _constraints_of(table):
                op.execute(f"alter table {table} rename constraint {shadow_name} to {canonical}")
            else:
                op.execute(f"alter index {shadow_name} rename to {canonical}")
    op.execute(REVISION_TRIGGER)
    for table, name, definition in inbound:
        op.execute(f"alter table {table} add constraint {name} {definition}")


def _inbound_keys() -> list[tuple[str, str, str]]:
    """Every foreign key another table holds on the two live tables, as (table, name, definition):
    dropped before the rename and re-created after it. A partition's inherited copy of its parent's
    key is not another table's and travels with the parent."""
    rows = op.get_bind().execute(
        sa.text(
            "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "from pg_constraint where contype = 'f' and coninhcount = 0 "
            "and confrelid in ('source'::regclass, 'page'::regclass) "
            "and conrelid not in ('source'::regclass, 'page'::regclass)"
        )
    )
    return [(row[0], row[1], row[2]) for row in rows]


def _constraints_of(table: str) -> set[str]:
    return set(
        op.get_bind()
        .execute(
            sa.text("select conname from pg_constraint where conrelid = cast(:table as regclass)"),
            {"table": table},
        )
        .scalars()
    )


def _live_indexes(table: str) -> list[str]:
    return list(
        op.get_bind()
        .execute(
            sa.text(
                "select indexname from pg_indexes where schemaname = 'public' "
                "and tablename = :table"
            ),
            {"table": table},
        )
        .scalars()
    )


def _columns(table: str) -> list[str]:
    return list(
        op.get_bind()
        .execute(
            sa.text(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = :table order by ordinal_position"
            ),
            {"table": table},
        )
        .scalars()
    )


def _mirror_function(live: str, shadow: str) -> str:
    columns = _columns(live)
    values = ", ".join(
        "coalesce(new.source_uid, (select uid from source where id = new.source_id))"
        if column == "source_uid"
        else f"new.{column}"
        for column in columns
    )
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in columns if column not in KEY
    )
    return f"""
create function mirror_{shadow}() returns trigger as $$
begin
    if tg_op = 'DELETE' then
        delete from {shadow} where workspace_id = old.workspace_id and uid = old.uid;
        return old;
    end if;
    insert into {shadow} ({", ".join(columns)}) values ({values})
    on conflict (workspace_id, uid) do update set {assignments};
    return new;
end;
$$ language plpgsql
"""


def _upgrade_sqlite() -> None:
    op.execute("pragma legacy_alter_table = on")
    for trigger in ("page_assign_revision_insert", "page_assign_revision_update"):
        op.execute(f"drop trigger {trigger}")
    for table, create, columns in (
        ("source", SQLITE_SOURCE, SQLITE_SOURCE_COLUMNS),
        ("page", SQLITE_PAGE, SQLITE_PAGE_COLUMNS),
    ):
        op.execute(f"alter table {table} rename to {table}__old")
        op.execute(create)
        projected = columns.replace(
            "source_uid",
            "coalesce(source_uid, (select uid from source where source.id = page__old.source_id))",
        )
        op.execute(f"insert into {table} ({columns}) select {projected} from {table}__old")
        op.execute(f"drop table {table}__old")
    for statement in (*SQLITE_INDEXES, *SQLITE_REVISION_TRIGGERS):
        op.execute(statement)
    op.execute("pragma legacy_alter_table = off")


SQLITE_SOURCE_BEFORE = "\n".join(
    (
        "CREATE TABLE source (",
        "    id CHAR(32) NOT NULL,",
        "    uid CHAR(32) NOT NULL,",
        "    workspace_id CHAR(32) NOT NULL,",
        "    backend TEXT NOT NULL,",
        "    config JSON NOT NULL,",
        "    feed_handle TEXT NOT NULL,",
        "    connection_id CHAR(32) NOT NULL,",
        "    cursor TEXT,",
        "    next_sync_at DATETIME NOT NULL,",
        "    consecutive_errors INTEGER DEFAULT '0' NOT NULL,",
        "    consecutive_refusals INTEGER DEFAULT '0' NOT NULL,",
        "    consecutive_empty INTEGER DEFAULT '0' NOT NULL,",
        "    parked_at DATETIME,",
        "    parked_reason TEXT,",
        "    claimed_by TEXT,",
        "    claim_expires_at DATETIME,",
        "    created_at DATETIME NOT NULL,",
        "    updated_at DATETIME NOT NULL,",
        "    PRIMARY KEY (id),",
        "    CONSTRAINT source_workspace_identity UNIQUE (workspace_id, id),",
        "    CONSTRAINT source_authority_fkey FOREIGN KEY(workspace_id, connection_id) "
        "REFERENCES connection (workspace_id, id) ON DELETE CASCADE,",
        "    FOREIGN KEY(workspace_id) REFERENCES workspace (id)",
        ")",
    )
)
SQLITE_PAGE_BEFORE = "\n".join(
    (
        "CREATE TABLE page (",
        "    id CHAR(32) NOT NULL,",
        "    uid CHAR(32) NOT NULL,",
        "    workspace_id CHAR(32) NOT NULL,",
        "    source_id CHAR(32) NOT NULL,",
        "    source_uid CHAR(32),",
        "    source_identity TEXT,",
        "    digest TEXT NOT NULL,",
        "    body_ref TEXT NOT NULL,",
        "    stream TEXT DEFAULT ('') NOT NULL,",
        "    title TEXT DEFAULT ('') NOT NULL,",
        "    record_created_at TEXT,",
        "    record_updated_at TEXT,",
        "    subject TEXT NOT NULL,",
        "    revision BIGINT DEFAULT '0' NOT NULL,",
        "    tombstone BOOLEAN NOT NULL,",
        "    created_at DATETIME NOT NULL,",
        "    updated_at DATETIME NOT NULL,",
        "    PRIMARY KEY (id),",
        "    CONSTRAINT page_subject CHECK (subject = 'shared' or subject like 'member:%'),",
        "    CONSTRAINT page_source_id_fkey FOREIGN KEY(source_id) REFERENCES source (id) "
        "ON DELETE CASCADE,",
        "    FOREIGN KEY(workspace_id) REFERENCES workspace (id)",
        ")",
    )
)
SQLITE_INDEXES_BEFORE = (
    "create unique index source_feed_handle on source "
    "(workspace_id, connection_id, backend, feed_handle)",
    "create index source_authority on source (workspace_id, connection_id)",
    "create index source_due on source (next_sync_at)",
    "create unique index source_workspace_uid on source (workspace_id, uid)",
    "create index page_feed on page (workspace_id, revision, id)",
    "create index page_source on page (source_id)",
    "create unique index page_source_identity on page (source_id, source_identity) "
    "where source_identity is not null",
    "create unique index page_workspace_uid on page (workspace_id, uid)",
)
SQLITE_REVISION_TRIGGERS_BEFORE = tuple(
    trigger.replace(
        "where workspace_id = new.workspace_id and uid = new.uid;", "where id = new.id;"
    )
    for trigger in SQLITE_REVISION_TRIGGERS
)


def _downgrade_sqlite() -> None:
    op.execute("pragma legacy_alter_table = on")
    for trigger in ("page_assign_revision_insert", "page_assign_revision_update"):
        op.execute(f"drop trigger {trigger}")
    for table, create, columns in (
        ("source", SQLITE_SOURCE_BEFORE, SQLITE_SOURCE_COLUMNS),
        ("page", SQLITE_PAGE_BEFORE, SQLITE_PAGE_COLUMNS),
    ):
        op.execute(f"alter table {table} rename to {table}__old")
        op.execute(create)
        op.execute(f"insert into {table} ({columns}) select {columns} from {table}__old")
        op.execute(f"drop table {table}__old")
    for statement in (*SQLITE_INDEXES_BEFORE, *SQLITE_REVISION_TRIGGERS_BEFORE):
        op.execute(statement)
    op.execute("pragma legacy_alter_table = off")


def downgrade() -> None:
    """The cutover in reverse, under the same locks: every row written or removed since the swap is
    carried back into the old tables first, so they are the live tables' equal when they take the
    names again; the shadows return to `*_new` and the mirrors are restored. The old `page` kept
    its revision trigger through the swap, so none is recreated."""
    if op.get_bind().dialect.name != "postgresql":
        _downgrade_sqlite()
        return
    op.execute("lock table source, page in access exclusive mode")
    inbound = _inbound_keys()
    for table, name, _definition in inbound:
        op.execute(f"alter table {table} drop constraint {name}")
    op.execute("drop trigger page_assign_revision on page")
    for table in TABLES:
        columns = _columns(table)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in KEY)
        op.execute(
            f"insert into {table}_old ({', '.join(columns)}) "
            f"select {', '.join(columns)} from {table} "
            f"on conflict (workspace_id, uid) do update set {assignments}"
        )
        op.execute(
            f"delete from {table}_old o where not exists (select 1 from {table} n "
            f"where n.workspace_id = o.workspace_id and n.uid = o.uid)"
        )
    for table in reversed(TABLES):
        for canonical in RENAMES[table].values():
            shadow_name = next(k for k, v in RENAMES[table].items() if v == canonical)
            if canonical in _constraints_of(table):
                op.execute(f"alter table {table} rename constraint {canonical} to {shadow_name}")
            else:
                op.execute(f"alter index {canonical} rename to {shadow_name}")
        for remainder in range(PARTITIONS):
            op.execute(
                f"alter table {table}_p{remainder:02d} rename to {table}_new_p{remainder:02d}"
            )
        op.execute(f"alter table {table} rename to {table}_new")
        op.execute(f"alter table {table}_old rename to {table}")
        for name in _constraints_of(table):
            if name.endswith("_old"):
                op.execute(f"alter table {table} rename constraint {name} to {name[:-4]}")
        for name in _live_indexes(table):
            if name.endswith("_old"):
                op.execute(f"alter index {name} rename to {name[:-4]}")
    for table, name, definition in inbound:
        op.execute(f"alter table {table} add constraint {name} {definition}")
    with op.get_context().autocommit_block():
        for live, shadow in (("source", "source_new"), ("page", "page_new")):
            op.execute(_mirror_function(live, shadow))
            op.execute(
                f"create trigger {live}_mirror after insert or update or delete on {live} "
                f"for each row execute function mirror_{shadow}()"
            )
