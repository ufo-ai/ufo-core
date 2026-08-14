"""seat every member and drop the seat bounds

One flat fee per workspace, unlimited members: nothing bounds how many members a workspace has, so
`seat_limit` and `included_seats` have no producer and no reader left and the columns go. A
workspace still carrying the values the seat shipper wrote would otherwise keep refusing members
past the fifth.

Seating every member is what makes the boundary readable rather than tidying. A seat is now the
whole answer to whether the agent answers someone: a member holds one from creation and an admin
revokes it to remove their access. A row left unseated by a bound that no longer exists would read
as an admin's deliberate revocation and stay unanswered, and every read that picks a seated
admin would skip them.

`seated_at` therefore takes a default of now: with no bound deciding who is seated, the answer is
every member, and a default is the one place that holds for whatever writes the next member row.
An admin's revoke is then the only thing that ever writes NULL there.

The retired seat-approval job's `ext_store` markers go with it: nothing else will ever read them.

SQLite drops a column by rebuilding the table, which is why two things here are spelled out that
Postgres needs no help with. `copy_from` names the shape the columns come out of, so neither
positivity CHECK is carried forward onto a column that no longer exists. And the page-revision
triggers are dropped and recreated around the rebuild: their bodies name `workspace`, and SQLite
rewrites that name when the rebuild renames the table under them, leaving two triggers pointing at
a table that has been dropped and every page write failing.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "metronome"
APPROVAL_KEY_PREFIX = "seat_approval_asked/"

WORKSPACE_WITH_BOUNDS = sa.Table(
    "workspace",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("seat_limit", sa.Integer, nullable=True),
    sa.Column("included_seats", sa.Integer, nullable=True),
    sa.Column("page_revision", sa.BigInteger, nullable=False, server_default="0"),
)

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


def upgrade() -> None:
    member = sa.table(
        "member",
        sa.column("seated_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    ext_store = sa.table(
        "ext_store",
        sa.column("extension", sa.Text()),
        sa.column("key", sa.Text()),
    )
    connection = op.get_bind()
    connection.execute(
        sa.update(member)
        .where(member.c.seated_at.is_(None))
        .values(seated_at=member.c.created_at, updated_at=sa.func.now())
    )
    connection.execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == EXTENSION,
            ext_store.c.key.like(f"{APPROVAL_KEY_PREFIX}%"),
        )
    )
    with op.batch_alter_table("member") as batch:
        batch.alter_column("seated_at", server_default=sa.func.now())
    rebuilds = connection.dialect.name == "sqlite"
    if rebuilds:
        op.execute("drop trigger page_assign_revision_insert")
        op.execute("drop trigger page_assign_revision_update")
    with op.batch_alter_table("workspace", copy_from=WORKSPACE_WITH_BOUNDS) as batch:
        batch.drop_column("seat_limit")
        batch.drop_column("included_seats")
    if rebuilds:
        op.execute(PAGE_REVISION_INSERT_TRIGGER)
        op.execute(PAGE_REVISION_UPDATE_TRIGGER)


def downgrade() -> None:
    pass
