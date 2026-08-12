"""move connector sharing to connections"""

import sqlalchemy as sa
from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "connection",
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("connection", sa.Column("account_label", sa.Text(), nullable=True))
    connection = sa.table(
        "connection",
        sa.column("id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
    )
    connector_grant = sa.table(
        "connector_grant",
        sa.column("connection_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
    )
    op.get_bind().execute(
        sa.update(connection)
        .values(shared=True)
        .where(
            sa.exists(
                sa.select(1)
                .select_from(connector_grant)
                .where(
                    connector_grant.c.connection_id == connection.c.id,
                    connector_grant.c.shared.is_(True),
                )
            )
        )
    )
    with op.batch_alter_table("connector_grant") as batch:
        batch.drop_column("shared")


def downgrade() -> None:
    with op.batch_alter_table("connector_grant") as batch:
        batch.add_column(
            sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.drop_column("connection", "account_label")
    op.drop_column("connection", "shared")
