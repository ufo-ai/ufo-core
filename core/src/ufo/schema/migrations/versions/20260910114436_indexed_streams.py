"""`page.indexed`: whether a page's stream reaches memory — RFC 0047 change 1.

A `StreamSpec` declares it; the sync driver writes it onto every row of the source each run and
onto each row it inserts, so a row the outgoing image lands under the column default during the
roll meets the declaration on the next run. The rows GitHub's unindexed streams already landed are
backfilled here, and only then does `indexed` join the revision trigger: the bulk backfill assigns
no revision and replays nothing through the page-change consumers, while every later flip is a
revision event the page indexer and the fact deriver each meet once. The outgoing image never
writes the column and reads its default `true`, so the fleet rolls over it; the memory extension's
marker and the drain that consumes it land beside it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260910114436"
down_revision: str | None = "20260909233938"
branch_labels: str | None = None
depends_on: str | None = None

UNINDEXED_GITHUB_STREAMS = ("contributor_activity", "stargazers", "workflow_runs")
REVISION_COLUMNS = ("digest", "body_ref", "subject", "tombstone")

page = sa.table(
    "page",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("source_uid", sa.Uuid()),
    sa.column("stream", sa.Text()),
    sa.column("indexed", sa.Boolean()),
)
source = sa.table(
    "source",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("uid", sa.Uuid()),
    sa.column("backend", sa.Text()),
)


def _postgres_revision_function(columns: tuple[str, ...]) -> str:
    new = ", ".join(f"new.{column}" for column in columns)
    old = ", ".join(f"old.{column}" for column in columns)
    return f"""
create or replace function assign_page_revision() returns trigger as $$
begin
    if tg_op = 'INSERT'
       or row({new}) is distinct from row({old})
    then
        update workspace
        set page_revision = page_revision + 1
        where id = new.workspace_id
        returning page_revision into new.revision;
    end if;
    return new;
end;
$$ language plpgsql
"""


def _sqlite_update_trigger(columns: tuple[str, ...]) -> str:
    changed = "\n  or ".join(f"new.{column} is not old.{column}" for column in columns)
    return f"""
create trigger page_assign_revision_update
after update of {", ".join(columns)} on page
when {changed}
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
"""


def _revision_on(columns: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_postgres_revision_function(columns))
        op.execute("drop trigger page_assign_revision on page")
        op.execute(
            f"""
create trigger page_assign_revision
before insert or update of {", ".join(columns)} on page
for each row execute function assign_page_revision()
"""
        )
        return
    op.execute("drop trigger page_assign_revision_update")
    op.execute(_sqlite_update_trigger(columns))


def upgrade() -> None:
    op.add_column(
        "page", sa.Column("indexed", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    github = sa.exists(
        sa.select(1)
        .where(
            source.c.workspace_id == page.c.workspace_id,
            source.c.uid == page.c.source_uid,
            source.c.backend == "github",
        )
        .correlate(page)
    )
    op.get_bind().execute(
        sa.update(page)
        .values(indexed=False)
        .where(github, page.c.stream.in_(UNINDEXED_GITHUB_STREAMS))
    )
    _revision_on((*REVISION_COLUMNS, "indexed"))


def downgrade() -> None:
    _revision_on(REVISION_COLUMNS)
    op.drop_column("page", "indexed")
