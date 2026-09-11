"""Seed a read cursor on every conversation a workspace already holds, one per member.

The rail draws a thread unread when its newest turn stands past the cursor, so without this seed
every thread that predates `conversation_read` would carry a mark nothing earned. The cursor is the
conversation's own activity moment, so a thread stays read until something new happens in it. A
conversation opened after this revision holds no cursor and draws its mark on its first turn, which
is the arrival the mark exists to report.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

revision: str = "20260911124440"
down_revision: str | None = "20260911102948"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    member = sa.table(
        "member",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
    )
    turn = sa.table(
        "turn",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    conversation_read = sa.table(
        "conversation_read",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("member_id", sa.Uuid()),
        sa.column("read_at", sa.DateTime(timezone=True)),
    )
    activity = (
        sa.select(sa.func.max(turn.c.updated_at))
        .where(
            turn.c.workspace_id == conversation.c.workspace_id,
            turn.c.conversation_id == conversation.c.id,
        )
        .correlate(conversation)
        .scalar_subquery()
    )
    bind = op.get_bind()
    insert = postgres_insert if bind.dialect.name == "postgresql" else sqlite_insert
    bind.execute(
        insert(conversation_read)
        .from_select(
            ["workspace_id", "conversation_id", "member_id", "read_at"],
            sa.select(
                conversation.c.workspace_id,
                conversation.c.id,
                member.c.id,
                sa.func.coalesce(activity, conversation.c.updated_at),
            )
            .select_from(conversation)
            .join(member, member.c.workspace_id == conversation.c.workspace_id)
            .where(
                ~sa.exists(
                    sa.select(conversation_read.c.read_at).where(
                        conversation_read.c.workspace_id == conversation.c.workspace_id,
                        conversation_read.c.conversation_id == conversation.c.id,
                        conversation_read.c.member_id == member.c.id,
                    )
                )
            ),
        )
        .on_conflict_do_nothing(
            index_elements=[
                conversation_read.c.workspace_id,
                conversation_read.c.conversation_id,
                conversation_read.c.member_id,
            ]
        )
    )


def downgrade() -> None:
    pass
