"""intent turn admission source"""

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_admission_source", type_="check")
        batch.create_check_constraint(
            "turn_admission_source",
            "admission_source in ('member', 'internal', 'scheduled', 'intent')",
        )


def downgrade() -> None:
    op.execute("update turn set admission_source = 'internal' where admission_source = 'intent'")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_admission_source", type_="check")
        batch.create_check_constraint(
            "turn_admission_source",
            "admission_source in ('member', 'internal', 'scheduled')",
        )
