"""the tasks screen is a workspace tab, so the tasks app is archived

The portal renders the tasks screen itself now, and the `app_tasks` extension that shipped the app
is gone with this release. The shipped agent rows are archived, not deleted: what the app did stays
— its conversations and any page a workspace forked are the record — and archiving is how an agent
leaves the roster everywhere else.

The provision identity stays on the row. The migrate Job completes before the fleet rolls, so the
outgoing pods still declare `(app_tasks, tasks)` and run a provisioning pass on each workspace's
first turn in the process; that pass matches an archived row by its identity and reports it present,
where a row stripped of it would send the pass to mint a second tasks agent beside the one this just
put away. A slug whose page left the bundle resolves to no homepage, so a row restored later stands
as an ordinary agent.

The downgrade leaves the rows archived. Nothing on a row tells this archive from a member's own,
and un-archiving a member's would overturn their answer to make the rollback tidy; the outgoing
image reads an archived tasks app exactly as it reads any other archived agent.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260826235718"
down_revision: str | None = "20260825044912"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "app_tasks"
DECLARED = "tasks"


def upgrade() -> None:
    agent = sa.table(
        "agent",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("archived_name", sa.Text()),
        sa.column("archived_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("provisioned_by", sa.Text()),
        sa.column("provisioned_name", sa.Text()),
    )
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == EXTENSION,
            agent.c.provisioned_name == DECLARED,
            agent.c.archived_at.is_(None),
        )
        .values(
            archived_name=agent.c.name,
            name="~archived-" + sa.cast(agent.c.id, sa.Text()),
            archived_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    pass
