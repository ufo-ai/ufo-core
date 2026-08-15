"""carry an agent's tool policy and the extension that shipped it"""

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.add_column(sa.Column("tools", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("provisioned_by", sa.Text(), nullable=True))
        batch.add_column(sa.Column("provisioned_name", sa.Text(), nullable=True))
        batch.add_column(sa.Column("provisioned_version", sa.Text(), nullable=True))
    with op.batch_alter_table("agent") as batch:
        batch.create_check_constraint(
            "agent_provenance",
            "(provisioned_by is null) = (provisioned_name is null) "
            "and (provisioned_by is null) = (provisioned_version is null)",
        )
        batch.create_unique_constraint(
            "agent_provision_identity", ["workspace_id", "provisioned_by", "provisioned_name"]
        )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_constraint("agent_provision_identity", type_="unique")
        batch.drop_constraint("agent_provenance", type_="check")
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("provisioned_version")
        batch.drop_column("provisioned_name")
        batch.drop_column("provisioned_by")
        batch.drop_column("tools")
