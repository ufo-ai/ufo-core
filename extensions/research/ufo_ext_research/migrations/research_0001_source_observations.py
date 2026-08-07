"""retrieved sources by conversation"""

import sqlalchemy as sa
from alembic import op

revision: str = "research_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("research",)
depends_on: str | None = "0069"


def upgrade() -> None:
    op.create_table(
        "research_source_observation",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("url_digest", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("published_date", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "url_digest"),
    )
    op.create_index(
        "research_source_observation_conversation",
        "research_source_observation",
        ["workspace_id", "conversation_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("research_source_observation_conversation")
    op.drop_table("research_source_observation")
