"""add source grants, held by every agent that could already read each live source"""

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.create_unique_constraint("source_workspace_identity", ("workspace_id", "id"))
    op.create_table(
        "source_grant",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "source_id", "agent_id"),
    )
    source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("removed_at", sa.DateTime(timezone=True)),
    )
    agent = sa.table(
        "agent",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
    )
    source_grant = sa.table(
        "source_grant",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("source_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    in_workspace = agent.c.workspace_id == source.c.workspace_id
    connection = op.get_bind()
    unreachable = sorted(
        str(source_id)
        for source_id in connection.execute(
            sa.select(source.c.id).where(
                source.c.removed_at.is_(None),
                ~sa.exists(sa.select(agent.c.id).where(in_workspace)),
            )
        ).scalars()
    )
    if unreachable:
        raise RuntimeError(
            f"the live sources {unreachable} have no agent to hold their grant; "
            f"create an agent in their workspaces before granting sources"
        )
    connection.execute(
        source_grant.insert().from_select(
            ["workspace_id", "source_id", "agent_id", "created_at", "updated_at"],
            sa.select(
                source.c.workspace_id,
                source.c.id,
                agent.c.id,
                sa.func.now(),
                sa.func.now(),
            )
            .select_from(source)
            .join(agent, in_workspace)
            .where(source.c.removed_at.is_(None)),
        )
    )


def downgrade() -> None:
    op.drop_table("source_grant")
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_workspace_identity", type_="unique")
