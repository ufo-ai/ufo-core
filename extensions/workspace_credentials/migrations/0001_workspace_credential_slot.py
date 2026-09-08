"""workspace_credential_slot

A workspace declares credential slots of its own: an env var, the host its value rides to, and the
header it rides in. The secret itself keeps its home in core's `credential` row, under the same slot
name. The row is the declaration an extension manifest carries for a keyed provider, written per
workspace instead of per deploy.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "workspace_credential_slot_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("workspace_credentials_ext",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "workspace_credential_slot",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("header", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "slot"),
        sa.UniqueConstraint("workspace_id", "env", name="uq_workspace_credential_slot_env"),
    )


def downgrade() -> None:
    op.drop_table("workspace_credential_slot")
