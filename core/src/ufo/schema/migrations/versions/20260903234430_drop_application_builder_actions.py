"""Give every allowlist that names the application builder the names that replace it.

An agent allowlist naming `action:site:build_ufo_application` or
`action:site:design_ufo_application` names a tool the deploy no longer registers, so an allowlist
carrying only those would withhold a build the workspace is owed. Each one gains the names the
homepage build asks of the agent it fires on: reach the builder, load the skill that says how,
show the member the wireframe, host the page, and bind it.

Nothing is taken away. The old names are what the image this release replaces still dispatches the
create-application flow through, and the migrate Job runs to completion while only that image
serves — so removing them here would refuse the one create path it holds. They cost the new image
nothing: an allowlist grants the canonical ids it names, and a name no active extension answers is
simply absent. A name is dropped in a later revision than the one that stops reading it.

No store key is dropped here. The builder's own rows and the homepage markers are both what the
outgoing image reads and writes until its last pod stops: `application-wireframe/<name>` is written
in a design turn and read in a later build turn, so clearing it would set that image building a
design the member never approved, and clearing `homepage-seed/` would hand it a fleet of unmarked
agents to seed again. A key is dropped in a later revision than the one that stops reading it. The
new sweep starts on an empty space of its own under `homepage-settled/`: an agent already bound is
marked `bound` on the next tick without a turn, and one that is not gets its three attempts.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260903234430"
down_revision: str | None = "20260905013000"
branch_labels: str | None = None
depends_on: str | None = None

RETIRED_ACTIONS = ("action:site:build_ufo_application", "action:site:design_ufo_application")
REPLACEMENT_ACTIONS = (
    "spawn",
    "load_skill",
    "share_file",
    "action:site:deploy_website",
    "action:agent:set_homepage",
)

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid()),
    sa.column("tools", sa.JSON()),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(agent.c.id, agent.c.tools).where(agent.c.tools.is_not(None))
    ).all()
    for agent_id, tools in rows:
        if not isinstance(tools, list) or not set(RETIRED_ACTIONS) & set(tools):
            continue
        kept = list(tools)
        kept.extend(name for name in REPLACEMENT_ACTIONS if name not in kept)
        bind.execute(sa.update(agent).where(agent.c.id == agent_id).values(tools=kept))


def downgrade() -> None:
    """Nothing to undo, and nothing that could be undone safely.

    The upgrade only adds, and it adds only the names a row was missing — so a row that already
    held `action:site:deploy_website` beside the builder, the shape revision 20260828010853 writes,
    is indistinguishable afterwards from one that was given it here. Removing the replacement names
    would take that agent's own grant away with no record of it having been its own.

    Leaving them costs the image this rolls back to nothing: it grants the canonical ids an
    allowlist names, intersected with its live registry, and the names it does not answer are
    simply absent. The names the outgoing image does read are the retired ones, and the upgrade
    never touched them."""
