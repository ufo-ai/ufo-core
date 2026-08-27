"""memory_profile: what the workspace knows about each of its members.

A profile is written about a colleague, so it cannot live in `memory_item`, whose `subject` is the
disclosure audience — a row about a member filed under `member:<id>` would be private to the person
it describes. It is written from the workspace-shared facts alone and read by everyone the roster
holds, which is a table of its own: one row per (workspace, member), the primary key that makes
"a rewrite replaces the entry" structural rather than a rule the writer remembers. Both keys cascade
because a profile outlives neither the workspace nor the member it describes.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0016"
down_revision: str | None = "memory_0015"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "memory_profile",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "member_id", name="memory_profile_pkey"),
    )


def downgrade() -> None:
    op.drop_table("memory_profile")
