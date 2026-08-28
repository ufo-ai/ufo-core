"""Stored agent allowlists name the Slack and iMessage setup tools by their wire names again.

`20260828011033` rewrote each wire name to a canonical action id the surface kind answered. This
image registers the wire tools and no surface kind, so every stored id goes back to the name the
registry holds; every other entry stands. The column is plain JSON: a SQL NULL is skipped by the
query and a JSON `null` or any non-list value by the guard.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260828045120"
down_revision: str | None = "20260828011033"
branch_labels: str | None = None
depends_on: str | None = None

SURFACE_TOOL_NAMES = {
    "action:surface:slack_connect": "slack_connect",
    "action:surface:slack_app_manifest": "slack_app_manifest",
    "action:surface:slack_channels": "slack_channels",
    "action:surface:imessage_connect": "imessage_connect",
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
    _rewrite(SURFACE_TOOL_NAMES)


def downgrade() -> None:
    _rewrite({name: canonical for canonical, name in SURFACE_TOOL_NAMES.items()})
