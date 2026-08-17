"""seeded main agent homepages read workspace-wide

Only the seed shape widens: the bound site must live in the homepage seed room this same agent
opened with the site's creator, because there the one turn that deployed the site also bound it —
the binder IS the creator, which is the gate the tool now enforces. A private site bound to main
by someone other than its creator was possible under the earlier tool and stays private, and so
does one another agent's seed room holds, whose creator asked for that agent's homepage and not
for main's: a migration has no speaker, so it performs no disclosure it cannot prove the creator
already made."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0004"
down_revision: str | None = "sites_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0056"

hosted_site = sa.table(
    "hosted_site",
    sa.column("workspace_id", sa.Uuid),
    sa.column("conversation_id", sa.Uuid),
    sa.column("name", sa.Text),
    sa.column("visibility", sa.Text),
    sa.column("creator_member_id", sa.Uuid),
    sa.column("generation", sa.Uuid),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("homepage_agent_id", sa.Uuid),
)

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid),
    sa.column("is_main", sa.Boolean),
)

conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid),
    sa.column("agent_id", sa.Uuid),
    sa.column("surface", sa.Text),
    sa.column("queue_key", sa.Text),
    sa.column("member_id", sa.Uuid),
)


def upgrade() -> None:
    bind = op.get_bind()
    seed_room = sa.select(conversation.c.id).where(
        conversation.c.id == hosted_site.c.conversation_id,
        conversation.c.surface == "web",
        conversation.c.queue_key.like("homepage/%"),
        conversation.c.member_id == hosted_site.c.creator_member_id,
        conversation.c.agent_id == hosted_site.c.homepage_agent_id,
    )
    bound_to_main = (
        bind.execute(
            sa.select(
                hosted_site.c.workspace_id, hosted_site.c.conversation_id, hosted_site.c.name
            ).where(
                hosted_site.c.visibility == "private",
                hosted_site.c.homepage_agent_id.in_(
                    sa.select(agent.c.id).where(agent.c.is_main.is_(True))
                ),
                sa.exists(seed_room),
            )
        )
    ).all()
    for row in bound_to_main:
        bind.execute(
            sa.update(hosted_site)
            .where(
                hosted_site.c.workspace_id == row.workspace_id,
                hosted_site.c.conversation_id == row.conversation_id,
                hosted_site.c.name == row.name,
            )
            .values(visibility="workspace", generation=uuid4(), updated_at=sa.func.now())
        )


def downgrade() -> None:
    pass
