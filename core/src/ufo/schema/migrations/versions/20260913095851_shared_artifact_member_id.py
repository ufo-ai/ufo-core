"""A shared artifact carries the member who put it in the conversation."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913095851"
down_revision: str | None = "20260913055555"
branch_labels: str | None = None
depends_on: str | None = None

shared_artifact = sa.table(
    "shared_artifact",
    sa.column("turn_id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("speaker_member_id", sa.Uuid()),
)
conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
)


def upgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(sa.Column("member_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("shared_artifact_member_id_fkey", "member", ["member_id"], ["id"])
    prior_member = (
        sa.select(sa.func.coalesce(turn.c.speaker_member_id, conversation.c.member_id))
        .select_from(turn.join(conversation, turn.c.conversation_id == conversation.c.id))
        .where(turn.c.id == shared_artifact.c.turn_id)
        .scalar_subquery()
    )
    op.execute(sa.update(shared_artifact).values(member_id=prior_member))


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_constraint("shared_artifact_member_id_fkey", type_="foreignkey")
        batch.drop_column("member_id")
