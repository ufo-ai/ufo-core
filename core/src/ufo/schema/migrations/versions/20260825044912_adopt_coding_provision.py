"""the coding app adopts the agent the coding extension shipped

The reviewer is not a new agent. `coding` shipped it as `code-review`, and `app_code` is the same
agent with a page. The whole provision identity moves — the extension half, which is what lets the
homepage read find the page, because the read keys an agent by the slug in its `provisioned_by`;
and the declared name, so a workspace that adopts one reads the same name as a workspace that is
given one new. The row's own name follows where the workspace never answered otherwise. It is not
`coding`: that name is taken in the spawn namespace by the subagent profile the reviewers are, and
an agent under it would be an ambiguous spawn target.

The mark comes with it. The release that shipped the reviewer declared no icon, so every row took
an automatic one dealt from the element pack, and a provisioning pass writes an icon when it
creates a row and never again — so the `git-pull-request` this extension declares would otherwise
reach only workspaces that do not exist yet, and one app would wear two marks across the fleet. It
is set for every row this moves, which is what `app_icons` did for the five apps before it.

The row's visibility comes with it. The release that shipped the reviewer declared none, so every
row took the default and is `private` — invisible to every member who is not an admin, page and all.
This extension declares `workspace`, which is what an app is, and no later pass would carry that: a
provisioning pass writes setup and purpose alone. Carrying a newly declared field onto the rows it
belongs to is what a migration is for, and it is what `app_icons` did for the mark. Only a row still
at the old default moves, so a member who narrowed their own reviewer keeps it narrow — and an
archived row is left alone entirely. Archiving the shipped reviewer is how a workspace says it does
not want one (`_one` matches an archived row and reports it present, so no pass re-creates it), and
widening what a member put away would hand it back visible to everyone the day they restored it.
The identity still moves on an archived row, which is what keeps it saying that under the new name.

The downgrade leaves that visibility where it stands. A row reading `workspace` afterwards is either
one this widened or one a member widened themselves, and nothing on the row tells the two apart — so
narrowing them all would take away a member's own answer to make the rollback tidy. The old image
reads the column exactly as it always did, and a reviewer visible to the workspace is not a fault it
has to be rescued from.

Nothing else is touched — the member's grants, its conversations, its name and any edit they made
all stand. `provisioned_version` still names the release that created the row, which is what it is
for; the next provisioning pass carries this extension's setup and purpose onto the row and moves
the version with them.

The identity is movable only once no running image declares it. A shipped agent is found by
`(workspace_id, provisioned_by, provisioned_name)`, and provisioning runs on a workspace's first
turn in each process — so an image that still declared `(coding, code-review)` would meet a lookup
this migration had just emptied, mint a free name, and stand a second reviewer beside the first.
The migrate Job completes before the fleet rolls and the outgoing pods serve until the new ones are
ready, which is exactly the interval that lookup would run in. The release that stops the `coding`
pack declaring the reviewer is what closes it, and it ships before this one: the image this
replaces provisions no reviewer at all, so the lookup this empties is one no running image makes.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260825044912"
down_revision: str | None = "20260825023542"
branch_labels: str | None = None
depends_on: str | None = None

SHIPPED_NAME = "code-review"
DECLARED = "code"
WAS = "coding"
NOW = "app_code"
SHIPPED_VISIBILITY = "private"
"""What the release that shipped the reviewer left on every row: it declared no visibility, so the
column took its default."""
APP_VISIBILITY = "workspace"
APP_ICON = "git-pull-request"


def _agent() -> sa.TableClause:
    return sa.table(
        "agent",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("provisioned_by", sa.Text()),
        sa.column("provisioned_name", sa.Text()),
        sa.column("icon", sa.Text()),
        sa.column("visibility", sa.Text()),
        sa.column("archived_at", sa.DateTime(timezone=True)),
    )


def _move(was: str, was_declared: str, now: str, declared: str) -> None:
    agent = _agent()
    op.execute(
        agent.update()
        .where(agent.c.provisioned_by == was, agent.c.provisioned_name == was_declared)
        .values(provisioned_by=now, provisioned_name=declared)
    )


def _rename(was: str, now: str) -> None:
    """The row's own name follows the declaration, where the workspace never answered otherwise.

    A member who renamed their reviewer keeps their name — that is theirs, and a rename is not a
    migration's to overturn — and so does a workspace already holding something else by the new
    name, since two agents cannot share one. Everybody else reads the app by the name the product
    calls it."""
    agent = _agent()
    held = agent.alias("held")
    taken = (
        sa.select(sa.literal(1))
        .select_from(held)
        .where(
            sa.text("held.workspace_id = agent.workspace_id"),
            held.c.name == now,
        )
        .exists()
    )
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == NOW,
            agent.c.provisioned_name == DECLARED,
            agent.c.name == was,
            ~taken,
        )
        .values(name=now)
    )


def _mark(icon: str) -> None:
    agent = _agent()
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == NOW,
            agent.c.provisioned_name == DECLARED,
        )
        .values(icon=icon)
    )


def _widen(was: str, visibility: str) -> None:
    agent = _agent()
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == NOW,
            agent.c.provisioned_name == DECLARED,
            agent.c.visibility == was,
            agent.c.archived_at.is_(None),
        )
        .values(visibility=visibility)
    )


def upgrade() -> None:
    _move(WAS, SHIPPED_NAME, NOW, DECLARED)
    _rename(SHIPPED_NAME, DECLARED)
    _mark(APP_ICON)
    _widen(SHIPPED_VISIBILITY, APP_VISIBILITY)


def downgrade() -> None:
    _rename(DECLARED, SHIPPED_NAME)
    _move(NOW, DECLARED, WAS, SHIPPED_NAME)
