"""a conversation carries what it is called

The name a member reads on a rail row, a conversations index and a run's header was derived per
read: the words the first turn opened with, unfenced and cut in Python, or — for a chat the portal
opened — a string the web extension kept in its own key-value store. Neither is reachable from the
query that lists conversations, so a search over them could only ever filter the page that query
had already bounded, and a member looking for an older conversation was told it does not exist.

The column is that name, written by whichever turn opens the conversation and rewritten only by a
surface that has a better one. Existing rows are backfilled from the same first-turn words the read
derived them from, so no conversation changes what it is called by this migration; a portal chat's
stored summary is carried over by the web extension's own migration, which owns those rows.

The fence pattern is spelled here rather than imported: a migration states the shape of the data as
it stood when it ran, and one that follows the code would rewrite history the next time the fence
changes.
"""

import re

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | None = None
depends_on: str | None = None

TITLE_CHARS = 240
BACKFILL_BATCH = 500

MEMBER_MESSAGE = re.compile(
    r"<member_message_(?P<marker>[0-9a-f]{8})>\n(?P<said>.*)\n</member_message_(?P=marker)>",
    re.DOTALL,
)

conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid()),
    sa.column("title", sa.Text()),
)

turn = sa.table(
    "turn",
    sa.column("conversation_id", sa.Uuid()),
    sa.column("seq", sa.Integer()),
    sa.column("inbound", sa.Text()),
)


def _said(inbound: str) -> str:
    found = MEMBER_MESSAGE.search(inbound)
    return (inbound if found is None else found.group("said")).strip()[:TITLE_CHARS]


def upgrade() -> None:
    op.add_column("conversation", sa.Column("title", sa.Text(), nullable=True))
    bind = op.get_bind()
    opening = (
        sa.select(turn.c.conversation_id, sa.func.min(turn.c.seq).label("seq"))
        .group_by(turn.c.conversation_id)
        .subquery()
    )
    rows = bind.execute(
        sa.select(turn.c.conversation_id, turn.c.inbound).select_from(
            turn.join(
                opening,
                sa.and_(
                    turn.c.conversation_id == opening.c.conversation_id,
                    turn.c.seq == opening.c.seq,
                ),
            )
        )
    ).all()
    named = [
        {"row_id": row.conversation_id, "row_title": _said(row.inbound)}
        for row in rows
        if _said(row.inbound)
    ]
    for start in range(0, len(named), BACKFILL_BATCH):
        bind.execute(
            sa.update(conversation)
            .where(conversation.c.id == sa.bindparam("row_id"))
            .values(title=sa.bindparam("row_title")),
            named[start : start + BACKFILL_BATCH],
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation") as batch:
        batch.drop_column("title")
