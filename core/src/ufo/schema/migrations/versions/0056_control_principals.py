"""name workspace control principals"""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE workspace, member, agent IN SHARE ROW EXCLUSIVE MODE")
        )
    workspace = sa.table("workspace", sa.column("id", sa.Uuid()))
    member = sa.table(
        "member",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    agent = sa.table(
        "agent",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    principals: list[tuple[UUID, UUID]] = []
    for workspace_id in connection.execute(sa.select(workspace.c.id)).scalars():
        admin_id = connection.execute(
            sa.select(member.c.id)
            .where(member.c.workspace_id == workspace_id)
            .order_by(member.c.created_at, member.c.id)
            .limit(1)
        ).scalar_one_or_none()
        main_id = connection.execute(
            sa.select(agent.c.id)
            .where(agent.c.workspace_id == workspace_id)
            .order_by(agent.c.created_at, agent.c.id)
            .limit(1)
        ).scalar_one_or_none()
        if admin_id is None or main_id is None:
            raise RuntimeError(f"workspace {workspace_id} has no control principals")
        principals.append((admin_id, main_id))

    op.add_column(
        "member",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "agent",
        sa.Column("is_main", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    member = sa.table("member", sa.column("id", sa.Uuid()), sa.column("is_admin", sa.Boolean()))
    agent = sa.table("agent", sa.column("id", sa.Uuid()), sa.column("is_main", sa.Boolean()))
    for admin_id, main_id in principals:
        connection.execute(member.update().where(member.c.id == admin_id).values(is_admin=True))
        connection.execute(agent.update().where(agent.c.id == main_id).values(is_main=True))
    op.create_index(
        "agent_workspace_main",
        "agent",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_main"),
        sqlite_where=sa.text("is_main"),
    )


def downgrade() -> None:
    op.drop_index("agent_workspace_main", table_name="agent")
    op.drop_column("agent", "is_main")
    op.drop_column("member", "is_admin")
