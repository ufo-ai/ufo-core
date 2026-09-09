"""Shadow `source` and `page` as hash-partitioned tables keyed by `(workspace_id, uid)`, mirrored
from the live tables row for row, backfilled, and verified — the expand half of RFC 0046 unit C.

`page` first gains `source_uid`, the parent it will be keyed to once `source.id` is gone; the
release being replaced writes only `source_id`, so the column is nullable here and the mirror
fills it from the join. Each shadow is the live table's shape plus the final keys: a primary key
of `(workspace_id, uid)`, every unique index qualified by the partition column, `page → source`
by `(workspace_id, source_uid)` with the same cascade, sixteen `HASH (workspace_id)` partitions,
and the live table's row-security policy cloned onto the parent so a read through it is fenced
from its first row. A plain index on `id` stays on each: a partitioned unique index must lead
with the partition column, and until unit D the driver still reaches rows by their content id
alone. Row triggers on the live tables mirror every insert, update and delete into the shadow;
they are committed before the backfill starts, so a write racing the copy lands in both tables,
the copy's `ON CONFLICT DO NOTHING` keeps the newer row, and the sweep afterwards drops a copy
whose original went while the copy ran. The check at the end reads both tables in one statement
— one snapshot — and refuses to finish unless they agree. The swap is the next revision.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260909193737"
down_revision: str | None = "20260909062725"
branch_labels: str | None = None
depends_on: str | None = None

PARTITIONS = 16
POLICY = "ufo_workspace_rls"
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
    where id = new.id;
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
    where id = new.id;
end
""",
)
SHADOWS = (("source", "source_new"), ("page", "page_new"))
KEY = ("workspace_id", "uid")

SOURCE_SHADOW = """
create table if not exists source_new (like source including defaults including constraints)
    partition by hash (workspace_id);
create unique index if not exists source_new_workspace_identity on source_new (workspace_id, id);
create unique index if not exists source_new_feed_handle
    on source_new (workspace_id, connection_id, backend, feed_handle);
create index if not exists source_new_due on source_new (next_sync_at);
create index if not exists source_new_id on source_new (id);
create index if not exists source_new_authority on source_new (workspace_id, connection_id);
"""
PAGE_SHADOW = """
create table if not exists page_new (like page including defaults including constraints)
    partition by hash (workspace_id);
alter table page_new alter column source_uid set not null;
create unique index if not exists page_new_workspace_identity on page_new (workspace_id, id);
create index if not exists page_new_feed on page_new (workspace_id, revision, uid);
create index if not exists page_new_source on page_new (workspace_id, source_uid);
create index if not exists page_new_source_id on page_new (source_id);
create index if not exists page_new_id on page_new (id);
create unique index if not exists page_new_source_identity
    on page_new (workspace_id, source_uid, source_identity) where source_identity is not null;
"""
CONSTRAINTS = (
    ("source_new", "source_new_pkey", "primary key (workspace_id, uid)"),
    (
        "source_new",
        "source_new_workspace_id_fkey",
        "foreign key (workspace_id) references workspace (id)",
    ),
    (
        "source_new",
        "source_new_authority_fkey",
        "foreign key (workspace_id, connection_id) references connection (workspace_id, id) "
        "on delete cascade",
    ),
    ("page_new", "page_new_pkey", "primary key (workspace_id, uid)"),
    (
        "page_new",
        "page_new_workspace_id_fkey",
        "foreign key (workspace_id) references workspace (id)",
    ),
)
PAGE_SOURCE_KEY = (
    "page_new",
    "page_new_source_fkey",
    "foreign key (workspace_id, source_uid) references source_new (workspace_id, uid) "
    "on delete cascade",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.add_column("page", sa.Column("source_uid", sa.Uuid(), nullable=True))
        op.execute(
            "update page set source_uid = (select uid from source where source.id = page.source_id)"
        )
        return
    # Every step commits on its own and can be run again: a Job that dies between two of them
    # leaves a rerunnable revision, never armed triggers over an unbuilt shadow.
    with op.get_context().autocommit_block():
        op.add_column("page", sa.Column("source_uid", sa.Uuid(), nullable=True), if_not_exists=True)
        op.execute(
            "update page set source_uid = source.uid from source "
            "where source.id = page.source_id and page.source_uid is null"
        )
        for statement in (*SOURCE_SHADOW.split(";"), *PAGE_SHADOW.split(";")):
            if statement.strip():
                op.execute(statement)
        for table, name, definition in CONSTRAINTS:
            _ensure_constraint(table, name, definition)
        for _live, shadow in SHADOWS:
            for remainder in range(PARTITIONS):
                op.execute(
                    f"create table if not exists {shadow}_p{remainder:02d} partition of {shadow} "
                    f"for values with (modulus {PARTITIONS}, remainder {remainder})"
                )
            _clone_policy(shadow)
        # The source shadow is complete and committed before the page mirror arms, so a page the
        # fleet writes meanwhile finds its source's row when the key between them is added.
        for live, shadow in SHADOWS:
            op.execute(_mirror_function(live, shadow))
            op.execute(f"drop trigger if exists {live}_mirror on {live}")
            op.execute(
                f"create trigger {live}_mirror after insert or update or delete on {live} "
                f"for each row execute function mirror_{shadow}()"
            )
            _backfill(live, shadow)
            op.execute(
                f"delete from {shadow} n where not exists "
                f"(select 1 from {live} l where l.workspace_id = n.workspace_id and l.uid = n.uid)"
            )
        _ensure_constraint(*PAGE_SOURCE_KEY)
    for live, shadow in SHADOWS:
        missing, extra = (
            op.get_bind()
            .execute(
                sa.text(
                    f"select (select count(*) from {live} l where not exists "
                    f"(select 1 from {shadow} n where n.workspace_id = l.workspace_id "
                    f"and n.uid = l.uid)), "
                    f"(select count(*) from {shadow} n where not exists "
                    f"(select 1 from {live} l where l.workspace_id = n.workspace_id "
                    f"and l.uid = n.uid))"
                )
            )
            .one()
        )
        if missing or extra:
            raise RuntimeError(f"{shadow} disagrees with {live}: {missing} missing, {extra} extra")


def _ensure_constraint(table: str, name: str, definition: str) -> None:
    present = op.get_bind().scalar(
        sa.text("select count(*) from pg_constraint where conname = :name"), {"name": name}
    )
    if not present:
        op.execute(f"alter table {table} add constraint {name} {definition}")


def _backfill(live: str, shadow: str) -> None:
    columns = _columns(live)
    projected = ", ".join(
        "coalesce(p.source_uid, s.uid)" if column == "source_uid" else f"p.{column}"
        for column in columns
    )
    joined = " join source s on s.id = p.source_id" if live == "page" else ""
    op.execute(
        f"insert into {shadow} ({', '.join(columns)}) select {projected} from {live} p{joined} "
        "on conflict (workspace_id, uid) do nothing"
    )


def _clone_policy(shadow: str) -> None:
    """The live table's policy, verbatim, so the shadow fences exactly as the bootstrap left the
    original; a database the bootstrap has not visited yet has no policy to clone and gets its
    fence from the bootstrap's next pass like every other table."""
    live = shadow.removesuffix("_new")
    policy = (
        op.get_bind()
        .execute(
            sa.text(
                "select qual, with_check from pg_policies "
                "where tablename = :table and policyname = :policy"
            ),
            {"table": live, "policy": POLICY},
        )
        .one_or_none()
    )
    if policy is None:
        return
    op.execute(f"alter table {shadow} enable row level security")
    op.execute(f"drop policy if exists {POLICY} on {shadow}")
    op.execute(
        f"create policy {POLICY} on {shadow} using ({policy.qual}) with check ({policy.with_check})"
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
create or replace function mirror_{shadow}() returns trigger
language plpgsql security definer set search_path = pg_catalog, public as $$
begin
    if tg_op = 'DELETE' then
        delete from {shadow} where workspace_id = old.workspace_id and uid = old.uid;
        return old;
    end if;
    insert into {shadow} ({", ".join(columns)}) values ({values})
    on conflict (workspace_id, uid) do update set {assignments};
    return new;
end;
$$
"""


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for live, shadow in reversed(SHADOWS):
            op.execute(f"drop trigger if exists {live}_mirror on {live}")
            op.execute(f"drop function if exists mirror_{shadow}()")
            op.execute(f"drop table if exists {shadow}")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_column("page", "source_uid")
        return
    # A SQLite batch recreate drops the table's triggers with the table; page carries two.
    for trigger in PAGE_TRIGGERS:
        op.execute(f"drop trigger {trigger}")
    with op.batch_alter_table("page") as batch:
        batch.drop_column("source_uid")
    for trigger in PAGE_REVISION_TRIGGERS:
        op.execute(trigger)
