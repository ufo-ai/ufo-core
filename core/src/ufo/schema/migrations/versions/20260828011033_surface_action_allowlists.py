"""the Slack and iMessage setup tools are actions on the surface kind

An agent's `tools` allowlist names what its model may call. The four setup tools now dispatch
through `object_action` under their canonical ids, so an allowlist still naming the old wire names
would grant nothing — the intersection against the live registry leaves a stale name silently
absent. Every stored entry moves to its canonical id; every other entry stands. The downgrade is
the exact inverse, so a rolled-back image reads the names it registers.

The column is plain JSON: a provisioned app whose declaration names no allowlist is stored as the
JSON value `null`, which `IS NOT NULL` selects, so a row is rewritten only when what it holds is a
list.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260828011033"
down_revision: str | None = "20260827153512"
branch_labels: str | None = None
depends_on: str | None = None

SURFACE_ACTIONS = {
    "slack_connect": "action:surface:slack_connect",
    "slack_app_manifest": "action:surface:slack_app_manifest",
    "slack_channels": "action:surface:slack_channels",
    "imessage_connect": "action:surface:imessage_connect",
}


def _rewrite(names: dict[str, str]) -> None:
    agent = sa.table("agent", sa.column("id"), sa.column("tools", sa.JSON()))
    bind = op.get_bind()
    rows = bind.execute(sa.select(agent.c.id, agent.c.tools).where(agent.c.tools.is_not(None)))
    for row in rows.all():
        if not isinstance(row.tools, list):
            continue
        rewritten = [names.get(name, name) for name in row.tools]
        if rewritten != row.tools:
            bind.execute(agent.update().where(agent.c.id == row.id).values(tools=rewritten))


def upgrade() -> None:
    _rewrite(SURFACE_ACTIONS)


def downgrade() -> None:
    _rewrite({canonical: name for name, canonical in SURFACE_ACTIONS.items()})
