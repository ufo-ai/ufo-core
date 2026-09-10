"""The content-addressed ids are gone — RFC 0046 unit D, contract half.

`20260909204543` stopped every writer and reader of `source.id`, `page.id` and `page.source_id`;
`20260909195911` left the pre-swap tables frozen as `source_old` and `page_old`. Nothing reads any
of them now, so the two tables, the three columns and the indexes that served them go. `uid`,
`source_uid`, `page_uid` and `created_from_page_uid` are the permanent names: renaming them to
`id` would be a catalog write the outgoing image's statements do not survive, so it would need a
quiesced fleet, and nothing is bought by it.

The downgrade rebuilds the shape of the release being replaced: the three columns come back
nullable with their indexes, and `source_old`/`page_old` come back empty in the pre-swap shape,
under the `_old` names the swap's downgrade strips, so the reverse cutover finds the tables it
copies into. Their content ids are NOT NULL as the pre-swap tables' were: `20260909204543`'s
downgrade runs before the copy and refuses any live row that lacks one.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260909233938"
down_revision: str | None = "20260909204543"
branch_labels: str | None = None
depends_on: str | None = None

POLICY = "ufo_workspace_rls"
CONTENT_ID_COLUMNS = (("source", "id"), ("page", "id"), ("page", "source_id"))
CONTENT_ID_INDEXES = (
    ("source", "source_workspace_identity", ("workspace_id", "id"), True),
    ("source", "source_id", ("id",), False),
    ("page", "page_workspace_identity", ("workspace_id", "id"), True),
    ("page", "page_source_id", ("source_id",), False),
    ("page", "page_id", ("id",), False),
)
OLD_TABLES = ("source_old", "page_old")
SOURCE_OLD = """
create table source_old (
    id uuid not null,
    workspace_id uuid not null,
    backend text not null,
    config json not null,
    cursor text,
    next_sync_at timestamptz not null,
    claimed_by text,
    claim_expires_at timestamptz,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    consecutive_errors integer not null default 0,
    connection_id uuid not null,
    consecutive_refusals integer not null default 0,
    parked_at timestamptz,
    parked_reason text,
    consecutive_empty integer not null default 0,
    feed_handle text not null,
    uid uuid not null,
    constraint source_pkey_old primary key (id),
    constraint source_workspace_identity_old unique (workspace_id, id),
    constraint source_workspace_uid_old unique (workspace_id, uid),
    constraint source_feed_handle_old unique (workspace_id, connection_id, backend, feed_handle),
    constraint source_workspace_id_fkey_old foreign key (workspace_id) references workspace (id),
    constraint source_authority_fkey_old foreign key (workspace_id, connection_id)
        references connection (workspace_id, id) on delete cascade
);
create index source_authority_old on source_old (workspace_id, connection_id);
create index source_due_old on source_old (next_sync_at)
"""
PAGE_OLD = """
create table page_old (
    id uuid not null,
    workspace_id uuid not null,
    source_id uuid not null,
    digest text not null,
    body_ref text not null,
    subject text not null,
    tombstone boolean not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    stream text not null default '',
    title text not null default '',
    record_created_at text,
    record_updated_at text,
    revision bigint not null default 0,
    source_identity text,
    uid uuid not null,
    source_uid uuid,
    constraint page_pkey_old primary key (id),
    constraint page_workspace_uid_old unique (workspace_id, uid),
    constraint page_subject_old check (subject = 'shared' or subject like 'member:%'),
    constraint page_workspace_id_fkey_old foreign key (workspace_id) references workspace (id),
    constraint page_source_id_fkey_old foreign key (source_id)
        references source_old (id) on delete cascade
);
create index page_feed_old on page_old (workspace_id, revision, id);
create index page_source_old on page_old (source_id);
create unique index page_source_identity_old on page_old (source_id, source_identity)
    where source_identity is not null;
create trigger page_assign_revision
    before insert or update of digest, body_ref, subject, tombstone on page_old
    for each row execute function assign_page_revision()
"""
PAGE_TRIGGERS = ("page_assign_revision_insert", "page_assign_revision_update")
PAGE_REVISION_TRIGGERS = (
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
    for table in reversed(OLD_TABLES):
        op.drop_table(table)
    for table, name, _columns, _unique in CONTENT_ID_INDEXES:
        op.drop_index(name, table_name=table)
    for table, column in CONTENT_ID_COLUMNS:
        op.drop_column(table, column)


def _upgrade_sqlite() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_workspace_identity", type_="unique")
        batch.drop_column("id")
    # A SQLite batch recreate drops the table's triggers with the table; page carries two.
    for trigger in PAGE_TRIGGERS:
        op.execute(f"drop trigger {trigger}")
    with op.batch_alter_table("page") as batch:
        batch.drop_constraint("page_workspace_identity", type_="unique")
        batch.drop_index("page_source_id")
        batch.drop_column("id")
        batch.drop_column("source_id")
    for trigger in PAGE_REVISION_TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        _downgrade_sqlite()
        return
    for table, column in CONTENT_ID_COLUMNS:
        op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
    for table, name, columns, unique in CONTENT_ID_INDEXES:
        op.create_index(name, table, list(columns), unique=unique)
    for statement in (*SOURCE_OLD.split(";"), *PAGE_OLD.split(";")):
        op.execute(statement)
    for table in OLD_TABLES:
        _clone_policy(table)


def _clone_policy(old: str) -> None:
    """The live table's policy, verbatim, so the table the reverse cutover makes live is fenced
    from its first row; a database the bootstrap has not visited has no policy to clone."""
    policy = (
        op.get_bind()
        .execute(
            sa.text(
                "select qual, with_check from pg_policies "
                "where tablename = :table and policyname = :policy"
            ),
            {"table": old.removesuffix("_old"), "policy": POLICY},
        )
        .one_or_none()
    )
    if policy is None:
        return
    op.execute(f"alter table {old} enable row level security")
    op.execute(
        f"create policy {POLICY} on {old} using ({policy.qual}) with check ({policy.with_check})"
    )


def _downgrade_sqlite() -> None:
    with op.batch_alter_table("source") as batch:
        batch.add_column(sa.Column("id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint("source_workspace_identity", ["workspace_id", "id"])
    for trigger in PAGE_TRIGGERS:
        op.execute(f"drop trigger {trigger}")
    with op.batch_alter_table("page") as batch:
        batch.add_column(sa.Column("id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("source_id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint("page_workspace_identity", ["workspace_id", "id"])
        batch.create_index("page_source_id", ["source_id"])
    for trigger in PAGE_REVISION_TRIGGERS:
        op.execute(trigger)
