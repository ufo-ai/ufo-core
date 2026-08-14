"""the workflow pause leaves core, with its rows

The pause is an extension row now, so the columns and the index that made a `scheduled_task` row
carry a member's wait have no producer and no reader left in core: `origin_seq` recorded the turn
sequence the wait was armed at, `resume_turn_id` named the one turn accepted to end it, and the
partial unique index reserved one such row per conversation. Admission asks the same question
directly under its own conversation lock, so nothing writes or reads them.

An armed pause in flight when this rolls is dropped, not migrated. A pause lives minutes to days
and its whole state is one row the agent re-arms by calling the tool again, so carrying it across
into an extension table would trade a permanent seam between two schemas for a wait that ends on
its own; the cost is that a conversation paused across the roll waits for its member's next message
instead of its timer. The rows go in the same migration that drops the columns, because nothing
else ever will: absence of the `@once` name cannot be the trigger later, and a row left behind
would be a recurring task whose schedule no cron parser accepts.

SQLite drops a column by rebuilding the table, so the partial index goes first: its WHERE clause
does not survive reflection, and the rebuild would otherwise recreate it as a plain unique index
over (workspace_id, conversation_id) — one that refuses a workspace's second scheduled task in the
same conversation.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | None = None
depends_on: str | None = None

ONE_TIME_SCHEDULE = "@once"


def upgrade() -> None:
    scheduled_task = sa.table("scheduled_task", sa.column("schedule", sa.Text()))
    op.get_bind().execute(
        sa.delete(scheduled_task).where(scheduled_task.c.schedule == ONE_TIME_SCHEDULE)
    )
    op.drop_index("scheduled_task_pause", table_name="scheduled_task")
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_column("resume_turn_id")
        batch.drop_column("origin_seq")


def downgrade() -> None:
    pass
