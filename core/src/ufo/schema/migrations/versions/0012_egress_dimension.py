"""egress ledger dimension"""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.drop_constraint("ledger_dimension", type_="check")
        batch.create_check_constraint("ledger_dimension", "dimension in ('tokens', 'egress')")


def downgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.drop_constraint("ledger_dimension", type_="check")
        batch.create_check_constraint("ledger_dimension", "dimension in ('tokens')")
