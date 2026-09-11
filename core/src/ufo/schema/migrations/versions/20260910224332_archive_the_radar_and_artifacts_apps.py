"""the radar and artifacts screens are portal views, so both apps are archived

The portal draws Radar and Artifacts itself, and the `app_radar` and `app_artifacts` extensions that
shipped them as framed pages are gone with this release. The shipped agent rows are archived, not
deleted: what each app did stays — its conversations and any page a workspace forked are the record
— and archiving is how an agent leaves the roster everywhere else.

The provision identity stays on the row. The migrate Job completes before the fleet rolls, so the
outgoing pods still declare `(app_radar, radar)` and `(app_artifacts, artifacts)` and run a
provisioning pass on each workspace's first turn in the process; that pass matches an archived row
by its identity and reports it present, where a row stripped of it would send the pass to mint a
second agent beside the one this just put away.

The downgrade leaves the rows archived. Nothing on a row tells this archive from a member's own, and
un-archiving a member's would overturn their answer to make the rollback tidy.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260910224332"
down_revision: str | None = "20260910194926"
branch_labels: str | None = None
depends_on: str | None = None

PROVISIONS = (("app_radar", "radar"), ("app_artifacts", "artifacts"))


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
    for extension, declared in PROVISIONS:
        op.execute(
            agent.update()
            .where(
                agent.c.provisioned_by == extension,
                agent.c.provisioned_name == declared,
                agent.c.archived_at.is_(None),
            )
            .values(
                archived_name=agent.c.name,
                name="~archived-" + sa.cast(agent.c.id, sa.Text()),
                archived_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    ext_store = sa.table("ext_store", sa.column("extension", sa.Text()))
    op.execute(ext_store.delete().where(ext_store.c.extension.in_([e for e, _ in PROVISIONS])))


def downgrade() -> None:
    pass
