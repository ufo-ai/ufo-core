"""Drop the GitHub App's rows: the installation seal and the member token the `coding` extension
stored under its two credential slots, the fulfillment claims those slots' private prompts wrote,
and the `connect_github` action's name in every agent allowlist. GitHub now rides the `github`
connector's own grant, so nothing reads these rows again and nothing else would ever remove them.

Disconnect every `github` connection with it. `github` moves from the Composio broker to the
Pipedream one, and a connection row names an account by the id of the broker that minted it, so
every row this finds points at a Composio account the registry no longer routes to: the listing
would keep reporting GitHub connected while the broker 404s on the id, no token reaches the wire,
and a reconnect would seat a second account beside the first — two accounts a static `GH_TOKEN`
cannot disambiguate, so the sandbox would export none. Removing them leaves the member exactly
where a member who never connected GitHub stands, and the way back is the connect they ask for in
chat. This is `ConnectionStore.disconnect` written as SQL, so the rows it leaves behind match what
that verb leaves: sources detached and removed with their pages tombstoned, source grants dropped,
and every agent's grant on the connection gone. The grants are deleted rather than left to the
connection's `ondelete` cascade, so this reads as one act and lands the same on any engine.

The image being replaced reads these rows too, and finds them gone rather than wrong: it lists no
GitHub connection, exports no `GH_TOKEN`, and clones anonymously — the same withholding it already
does for a member who has connected nothing."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260902225627"
down_revision: str | None = "20260902223926"
branch_labels: str | None = None
depends_on: str | None = None

SLOTS = ("github_app_installation", "github_git_token")
CONNECT_GITHUB_ACTION = "action:credential:connect_github"
GITHUB_PROVIDER = "github"

credential = sa.table(
    "credential",
    sa.column("slot", sa.Text()),
)
credential_fulfillment = sa.table(
    "credential_fulfillment",
    sa.column("slot", sa.Text()),
)
agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid()),
    sa.column("tools", sa.JSON()),
)
connection = sa.table(
    "connection",
    sa.column("id", sa.Uuid()),
    sa.column("provider", sa.Text()),
)
source = sa.table(
    "source",
    sa.column("id", sa.Uuid()),
    sa.column("connection_id", sa.Uuid()),
    sa.column("claimed_by", sa.Text()),
    sa.column("claim_expires_at", sa.DateTime(timezone=True)),
    sa.column("removed_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
source_grant = sa.table(
    "source_grant",
    sa.column("source_id", sa.Uuid()),
)
connector_grant = sa.table(
    "connector_grant",
    sa.column("connection_id", sa.Uuid()),
)
page = sa.table(
    "page",
    sa.column("source_id", sa.Uuid()),
    sa.column("tombstone", sa.Boolean()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(credential.delete().where(credential.c.slot.in_(SLOTS)))
    bind.execute(credential_fulfillment.delete().where(credential_fulfillment.c.slot.in_(SLOTS)))
    rows = bind.execute(
        sa.select(agent.c.id, agent.c.tools).where(agent.c.tools.is_not(None))
    ).all()
    for agent_id, tools in rows:
        if not isinstance(tools, list) or CONNECT_GITHUB_ACTION not in tools:
            continue
        kept = [name for name in tools if name != CONNECT_GITHUB_ACTION]
        bind.execute(sa.update(agent).where(agent.c.id == agent_id).values(tools=kept))
    connections = sa.select(connection.c.id).where(connection.c.provider == GITHUB_PROVIDER)
    sources = sa.select(source.c.id).where(source.c.connection_id.in_(connections))
    now = datetime.now(UTC)
    bind.execute(
        sa.update(page)
        .values(tombstone=True, updated_at=now)
        .where(page.c.source_id.in_(sources), page.c.tombstone.is_(False))
    )
    bind.execute(source_grant.delete().where(source_grant.c.source_id.in_(sources)))
    bind.execute(
        sa.update(source)
        .values(
            connection_id=None,
            removed_at=now,
            claimed_by=None,
            claim_expires_at=None,
            updated_at=now,
        )
        .where(source.c.connection_id.in_(connections))
    )
    bind.execute(connector_grant.delete().where(connector_grant.c.connection_id.in_(connections)))
    bind.execute(connection.delete().where(connection.c.provider == GITHUB_PROVIDER))


def downgrade() -> None:
    pass
