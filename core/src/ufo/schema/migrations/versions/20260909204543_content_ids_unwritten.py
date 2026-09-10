"""The content-addressed ids stop being written — RFC 0046 unit D, expand half.

`source.id`, `page.id` and `page.source_id` were the keys before `uid`; since unit C every read
runs on `(workspace_id, uid)`, and this release's writers name no content id at all. The three
columns become nullable so a row this release lands can omit them while the release being replaced
still fills them; unit D's contract half drops them with the frozen `*_old` tables. A page's
identity within its source is `source_identity`. A page synced before that column existed carries
none and is never matched by a fetch again: under an incremental source it stays as history, and a
snapshot source tombstones it on its next pass and lands the ref again under a new uid.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260909204543"
down_revision: str | None = "20260909203000"
branch_labels: str | None = None
depends_on: str | None = None

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
CONTENT_ID_COLUMNS = (("source", "id"), ("page", "id"), ("page", "source_id"))


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, column in CONTENT_ID_COLUMNS:
            op.alter_column(table, column, existing_type=sa.Uuid(), nullable=True)
        return
    # A SQLite batch recreate drops the table's triggers with the table; page carries two.
    with op.batch_alter_table("source") as batch:
        batch.alter_column("id", existing_type=sa.Uuid(), nullable=True)
    for trigger in PAGE_TRIGGERS:
        op.execute(f"drop trigger {trigger}")
    with op.batch_alter_table("page") as batch:
        batch.alter_column("id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("source_id", existing_type=sa.Uuid(), nullable=True)
    for trigger in PAGE_REVISION_TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    orphaned = op.get_bind().scalar(
        sa.text(
            "select count(*) from page where id is null or source_id is null "
            "union all select count(*) from source where id is null order by 1 desc limit 1"
        )
    )
    if orphaned:
        raise RuntimeError(
            f"{orphaned} rows carry no content id; the previous release cannot key them"
        )
    if op.get_bind().dialect.name == "postgresql":
        for table, column in CONTENT_ID_COLUMNS:
            op.alter_column(table, column, existing_type=sa.Uuid(), nullable=False)
        return
    with op.batch_alter_table("source") as batch:
        batch.alter_column("id", existing_type=sa.Uuid(), nullable=False)
    for trigger in PAGE_TRIGGERS:
        op.execute(f"drop trigger {trigger}")
    with op.batch_alter_table("page") as batch:
        batch.alter_column("id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("source_id", existing_type=sa.Uuid(), nullable=False)
    for trigger in PAGE_REVISION_TRIGGERS:
        op.execute(trigger)
