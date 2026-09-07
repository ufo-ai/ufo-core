"""coding tools"""

import sqlalchemy as sa
from alembic import op

revision: str = "coding_0004"
down_revision: str | None = "coding_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("coding_review_run")
    op.drop_table("coding_review_inbox")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("coding_turn_workspace_identity", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.create_unique_constraint("coding_turn_workspace_identity", ("workspace_id", "id"))
    op.create_table(
        "coding_review_inbox",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "source_id"),
    )
    op.create_table(
        "coding_review_run",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("review_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "turn_id"],
            ["turn.workspace_id", "turn.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "review_conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "repository",
            "pull_request_number",
            "base_sha",
            "head_sha",
        ),
        sa.UniqueConstraint("workspace_id", "run_id", name="coding_review_run_identity"),
    )
