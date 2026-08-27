"""the wiki app is private on the rows that already hold it

The release that shipped the wiki app declared `workspace`, so every workspace
`packs/assistant_hosted` provisioned holds a row visible to every member — the boot payload carries
it and its address opens the page, whatever the `enable-wiki-app` flag hides from the portal's list.
This release declares `private`, and no later pass would carry that: a provisioning pass writes
setup and purpose alone. Carrying a newly declared field onto the rows it belongs to is what a
migration is for, and it is what `adopt_coding_provision` did for the reviewer's own visibility.

Only a row still reading `workspace` moves — the value the shipped declaration wrote. A member who
already narrowed their own wiki keeps it narrow, and nothing else in the fleet is touched.

An archived row moves with the rest, which is where this parts from that widening. The widening left
an archived row alone because handing back what a member put away would make it visible to everyone
the day they restored it. This move only narrows: it takes the row from nobody who reaches it today,
since an archived agent stands in no member's roster at all, and a workspace that restores it later
gets the app the product now ships rather than the audience a withdrawn default left behind.

The downgrade widens nothing back. A row reading `private` afterwards is either one this narrowed
or one a member narrowed themselves, and nothing on the row tells the two apart — so widening them
all would take away a member's own answer to make the rollback tidy, and would hand the app to every
member of every workspace, which is the exact state this release ends. The old image reads a private
wiki agent as it reads any other private agent: its owner, the workspace's admins, and the members
granted web access reach it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260827161500"
down_revision: str | None = "20260827015500"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "app_wiki"
DECLARED = "wiki"
SHIPPED_VISIBILITY = "workspace"
"""What the release that shipped the wiki app left on every row: it declared `workspace`."""
APP_VISIBILITY = "private"


def upgrade() -> None:
    agent = sa.table(
        "agent",
        sa.column("provisioned_by", sa.Text()),
        sa.column("provisioned_name", sa.Text()),
        sa.column("visibility", sa.Text()),
    )
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == EXTENSION,
            agent.c.provisioned_name == DECLARED,
            agent.c.visibility == SHIPPED_VISIBILITY,
        )
        .values(visibility=APP_VISIBILITY)
    )


def downgrade() -> None:
    pass
