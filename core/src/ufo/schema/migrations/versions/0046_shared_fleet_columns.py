"""drop dedicated-mode columns the shared fleet never reads

The shared fleet is the only runtime. The scale-out boot guard is gone, so `runtime_instance`
keeps only its liveness columns — `started_at` and `fingerprint` had no reader. The dedicated CLI
approve route is gone and proposal approval has no shared path, so `proposal.approved_by` (and its
member FK) had no reader; `status` alone carries the promotion signal.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("proposal") as batch:
        batch.drop_column("approved_by")
    with op.batch_alter_table("runtime_instance") as batch:
        batch.drop_column("fingerprint")
        batch.drop_column("started_at")


def downgrade() -> None:
    with op.batch_alter_table("runtime_instance") as batch:
        batch.add_column(
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.add_column(sa.Column("fingerprint", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("proposal") as batch:
        batch.add_column(sa.Column("approved_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key("proposal_approved_by_fkey", "member", ["approved_by"], ["id"])
