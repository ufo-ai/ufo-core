from alembic import op

revision: str = "20260901111145"
down_revision: str | None = "20260901073109"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.create_check_constraint(
            "turn_authority",
            "speaker_member_id is null or on_behalf_of_member_id is null",
        )


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_authority", type_="check")
