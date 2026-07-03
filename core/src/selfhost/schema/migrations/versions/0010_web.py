"""web"""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint(
            "conversation_surface", "surface in ('cli', 'subagent', 'slack', 'web')"
        )
    with op.batch_alter_table("surface_identity") as batch:
        batch.drop_constraint("surface_identity_surface", type_="check")
        batch.create_check_constraint(
            "surface_identity_surface", "surface in ('cli', 'slack', 'web')"
        )


def downgrade() -> None:
    with op.batch_alter_table("surface_identity") as batch:
        batch.drop_constraint("surface_identity_surface", type_="check")
        batch.create_check_constraint("surface_identity_surface", "surface in ('cli', 'slack')")
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint(
            "conversation_surface", "surface in ('cli', 'subagent', 'slack')"
        )
