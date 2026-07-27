"""surface installation and conversation agent bindings"""

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | None = None
depends_on: str | None = None

EARLIEST_AGENT = (
    "(select a.id from agent a where a.workspace_id = {table}.workspace_id "
    "order by a.created_at, a.id limit 1)"
)


def upgrade() -> None:
    for table in ("surface_installation", "conversation"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("agent_id", sa.Uuid(), nullable=True))
        op.execute(
            f"update {table} set agent_id = {EARLIEST_AGENT.format(table=table)} "
            "where agent_id is null"
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_foreign_key(f"{table}_agent_id_fkey", "agent", ["agent_id"], ["id"])


def downgrade() -> None:
    for table in ("surface_installation", "conversation"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"{table}_agent_id_fkey", type_="foreignkey")
            batch.drop_column("agent_id")
