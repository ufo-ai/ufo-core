"""sample_ext_note"""

import sqlalchemy as sa
from alembic import op

revision: str = "sample_ext_note_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("sample_ext",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "sample_ext_note",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("sample_ext_note")
