import sqlalchemy as sa
from alembic import op

revision: str = "20260914122142"
down_revision: str | None = "20260913214954"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("detached_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "turn_detached",
        "turn",
        ["workspace_id"],
        postgresql_where=sa.text("detached_until is not null"),
        sqlite_where=sa.text("detached_until is not null"),
    )
    op.create_table(
        "detached_task",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sandbox_conversation_id", sa.Uuid(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("runtime_base", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=True),
        sa.Column("follow_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("task <> ''", name="detached_task_task_nonempty"),
        sa.CheckConstraint(
            "runtime_base <> ''",
            name="detached_task_runtime_base_nonempty",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(["sandbox_conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["sandbox_call_capability.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("turn_id", "task"),
    )
    op.create_index("detached_task_workspace", "detached_task", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("detached_task_workspace", table_name="detached_task")
    op.drop_table("detached_task")
    op.drop_index("turn_detached", table_name="turn")
    op.drop_column("turn", "detached_until")
