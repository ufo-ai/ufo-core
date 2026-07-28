"""allow room memory audiences"""

from alembic import op

revision: str = "memory_0011"
down_revision: str | None = "memory_0010"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_subject", type_="check")
        batch.create_check_constraint(
            "memory_item_subject",
            "subject = 'shared' or subject like 'member:%' or "
            "subject like 'room:%:%' or subject like 'foreign:%:%'",
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_subject", type_="check")
        batch.create_check_constraint(
            "memory_item_subject",
            "subject = 'shared' or subject like 'member:%'",
        )
