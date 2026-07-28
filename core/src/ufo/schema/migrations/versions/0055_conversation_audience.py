"""persist conversation audience"""

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("surface", sa.Text()),
        sa.column("member_id", sa.Uuid()),
    )
    turn = sa.table(
        "turn",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
    )
    unclassified_history = (
        op.get_bind()
        .execute(
            sa.select(conversation.c.id)
            .where(
                conversation.c.surface == "slack",
                conversation.c.member_id.is_(None),
                sa.exists(
                    sa.select(turn.c.conversation_id).where(
                        turn.c.workspace_id == conversation.c.workspace_id,
                        turn.c.conversation_id == conversation.c.id,
                    )
                ),
            )
            .limit(1)
        )
        .one_or_none()
    )
    if unclassified_history is not None:
        raise RuntimeError(
            "existing memberless Slack conversation history has no provable disclosure audience"
        )
    op.add_column(
        "conversation",
        sa.Column("audience", sa.Text(), server_default="shared", nullable=False),
    )
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("member_id", sa.Uuid()),
        sa.column("audience", sa.Text()),
    )
    connection = op.get_bind()
    for row in connection.execute(
        sa.select(conversation.c.id, conversation.c.member_id).where(
            conversation.c.member_id.is_not(None)
        )
    ):
        connection.execute(
            conversation.update()
            .where(conversation.c.id == row.id)
            .values(audience=f"member:{row.member_id}")
        )
    with op.batch_alter_table("conversation") as batch:
        batch.create_check_constraint(
            "conversation_audience",
            "audience = 'shared' or audience like 'member:%' or "
            "audience like 'room:%:%' or audience like 'foreign:%:%'",
        )
        batch.create_check_constraint(
            "conversation_audience_member",
            "(member_id is null and audience not like 'member:%') or "
            "(member_id is not null and audience like 'member:%')",
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_audience_member", type_="check")
        batch.drop_constraint("conversation_audience", type_="check")
        batch.drop_column("audience")
