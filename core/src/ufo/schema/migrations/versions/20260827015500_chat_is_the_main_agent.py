import sqlalchemy as sa
from alembic import op

revision: str = "20260827015500"
down_revision: str | None = "20260826235718"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "app_chat"
DECLARED = "chat"
DECLARED_VERSION = "0.2.0"
ARCHIVED_NAME_PREFIX = "~archived-"

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("is_main", sa.Boolean()),
    sa.column("provisioned_by", sa.Text()),
    sa.column("provisioned_name", sa.Text()),
    sa.column("provisioned_version", sa.Text()),
    sa.column("archived_at", sa.DateTime(timezone=True)),
    sa.column("archived_name", sa.Text()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    workspaces = (
        bind.execute(
            sa.select(agent.c.workspace_id).where(
                agent.c.provisioned_by == EXTENSION,
                agent.c.provisioned_name == DECLARED,
                agent.c.is_main.is_(False),
            )
        )
        .scalars()
        .all()
    )
    if not workspaces:
        return
    bind.execute(
        sa.update(agent)
        .where(
            agent.c.workspace_id.in_(workspaces),
            agent.c.provisioned_by == EXTENSION,
            agent.c.provisioned_name == DECLARED,
            agent.c.is_main.is_(False),
            agent.c.archived_at.is_(None),
        )
        .values(
            archived_name=agent.c.name,
            name=ARCHIVED_NAME_PREFIX + sa.cast(agent.c.id, sa.Text()),
            archived_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    bind.execute(
        sa.update(agent)
        .where(
            agent.c.workspace_id.in_(workspaces),
            agent.c.provisioned_by == EXTENSION,
            agent.c.provisioned_name == DECLARED,
            agent.c.is_main.is_(False),
        )
        .values(
            provisioned_by=None,
            provisioned_name=None,
            provisioned_version=None,
            updated_at=sa.func.now(),
        )
    )
    held = agent.alias("held")
    name_taken = sa.exists(
        sa.select(sa.literal(1)).where(
            held.c.workspace_id == agent.c.workspace_id,
            held.c.name == DECLARED,
        )
    )
    bind.execute(
        sa.update(agent)
        .where(
            agent.c.workspace_id.in_(workspaces),
            agent.c.is_main.is_(True),
            agent.c.name == "assistant",
            ~name_taken,
        )
        .values(name=DECLARED, updated_at=sa.func.now())
    )
    bind.execute(
        sa.update(agent)
        .where(
            agent.c.workspace_id.in_(workspaces),
            agent.c.is_main.is_(True),
            agent.c.provisioned_by.is_(None),
        )
        .values(
            provisioned_by=EXTENSION,
            provisioned_name=DECLARED,
            provisioned_version=DECLARED_VERSION,
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    pass
