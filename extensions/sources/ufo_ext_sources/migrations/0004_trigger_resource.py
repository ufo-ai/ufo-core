"""one trigger table, keyed by the connection and the resource

A trigger is one conversation's standing interest in one connected account, narrowed to one resource
of it or over the whole thing. Both were rows of two tables — `source_trigger` keyed one row per
(workspace, conversation, binding), so a narrowed one could not live in it and kept
`source_resource_watch` — and both named their account by a binding, an eight-hex digest of the
provider, account handle and tenant URL a source's config spelled. The resource becomes a column
here and joins the key, so the two tables become one; `connection_id` replaces the binding, so the
account a trigger names is the row that holds it and deleting that connection deletes the triggers
over it.

The binding is not stored anywhere else, so the mapping is computed here from the connections
themselves: each one's provider, account handle and tenant URL hash to exactly the name its streams
derived, which is why this revision depends on the core revision that moves those three facts onto
`connection`.

A feed that ran on the workspace's own credential hashed its binding from the sentinel handle that
path spelled, and the core revision splits those feeds by who read them — one connection for the
workspace and one per member who held a private feed — so that one name now reaches several
connections. Such a trigger follows the member who created it, since a trigger belongs to whoever
asked for it, and falls back to the workspace's own feed where `created_by_member_id` is null or
that member held no private feed for the provider. A trigger no name and no fallback reaches is
deleted — its account is gone, or its tenant URL is one no connection dials any more — and nothing
else could ever tell it apart from a trigger on an account this workspace never held.

This revision does not meet the image it replaces, and that is the decision it records. The outgoing
image names the three-column key as the conflict target of every whole-binding trigger it writes,
selects `source_trigger.binding`, and reads `source_resource_watch` on every other trigger statement
it makes. Both shapes cannot stand at once, so no ordering of revisions makes the merge safe inside
one release, and the outgoing image's whole trigger surface fails for the length of the roll:

- Every trigger write is refused. A whole-binding apply finds no unique index matching its
  three-column conflict target; a narrowed apply, and the removal of a narrowed trigger, address a
  table that is gone.
- No trigger read answers. `waking` and the member-facing listing each read `source_resource_watch`
  after `source_trigger`, and the check on what a conversation already watches reads it alone. So no
  conversation wakes for a source batch, and the trigger kind's own listing and delete fail with
  that listing. The page-change cursor stays where it was on such a tick and counts on
  `page_change_stalled_total`; an incoming pod's tick carries the same batch, so a trigger fires
  late and none are lost.
- A source deletion strands its triggers. It removes each of the binding's streams in a transaction
  of its own and then deletes from both trigger tables in one, so the second statement rolls the
  first back with the streams already gone, and the binding's triggers outlive the source under a
  name no source answers to until a member deletes each one.

One record of one thing is worth that window.
"""

import hashlib
import json
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0004"
down_revision: str | None = "sources_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260907150257"

BINDING_NAME_DIGEST_HEX = 8
WORKSPACE_ACCOUNT = ""
MEMBER_PREFIX = "member:"
DIRECT_ACCOUNT = "default"
TRIGGER_CONNECTION_FK = "source_trigger_connection_id_fkey"

TRIGGER_ON_A_BINDING = sa.Table(
    "source_trigger",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("binding", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "conversation_id", "binding", name="source_trigger_conversation"
    ),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
    sa.Index("source_trigger_binding", "workspace_id", "binding"),
)

TRIGGER_ON_BOTH = sa.Table(
    "source_trigger",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("binding", sa.Text, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=True),
    sa.Column("resource", sa.Text, nullable=False, server_default=""),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
)

TRIGGER_ON_A_CONNECTION = sa.Table(
    "source_trigger",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=False),
    sa.Column("resource", sa.Text, nullable=False, server_default=""),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.UniqueConstraint(
        "workspace_id",
        "conversation_id",
        "connection_id",
        "resource",
        name="source_trigger_conversation",
    ),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
    sa.ForeignKeyConstraint(
        ["connection_id"], ["connection.id"], ondelete="CASCADE", name=TRIGGER_CONNECTION_FK
    ),
    sa.Index("source_trigger_connection", "workspace_id", "connection_id"),
)


def _watches() -> sa.TableClause:
    """The narrowed-trigger table, whose columns are exactly the ones a row carries when it moves
    between the two tables."""
    return sa.table(
        "source_resource_watch",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("binding", sa.Text()),
        sa.column("resource", sa.Text()),
        sa.column("delivery", sa.Text()),
        sa.column("created_by_member_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _triggers() -> sa.TableClause:
    """The carried columns plus the one only this table holds."""
    return sa.table(
        "source_trigger",
        *(sa.column(column.name, column.type) for column in _watches().c),
        sa.column("connection_id", sa.Uuid()),
    )


def _connections() -> sa.TableClause:
    return sa.table(
        "connection",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("base_url", sa.Text()),
    )


def _binding_name(provider: str, account: str, base_url: str | None) -> str:
    """The name a binding derived from what it authenticated as. Spelled here rather than imported:
    it names rows written before this revision, and nothing derives it once this has run."""
    digest = hashlib.sha256(
        json.dumps(
            {"account": account, "base_url": base_url, "provider": provider}, sort_keys=True
        ).encode()
    ).hexdigest()[:BINDING_NAME_DIGEST_HEX]
    return f"{provider.replace('_', '-')}-{digest}"


def upgrade() -> None:
    """The resource becomes a column and the connection replaces the binding, so one conversation
    holds a connected account and each resource of it as rows of one table. Every
    `source_resource_watch` row crosses over keeping its own id — the generation an object edit
    checks itself against, and the key its per-page conversation is queued under — and lands under
    the wide key, which the narrow one it leaves could not have held."""
    bind = op.get_bind()
    trigger, watch = _triggers(), _watches()
    with op.batch_alter_table("source_trigger", copy_from=TRIGGER_ON_A_BINDING) as batch:
        batch.add_column(sa.Column("connection_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("resource", sa.Text(), nullable=False, server_default=""))
        batch.drop_constraint("source_trigger_conversation", type_="unique")
    bind.execute(
        trigger.insert().from_select([column.name for column in watch.c], sa.select(*watch.c))
    )
    op.drop_index("source_resource_watch_binding", "source_resource_watch")
    op.drop_table("source_resource_watch")
    connections = bind.execute(sa.select(_connections())).all()
    named: dict[tuple[UUID, str], UUID] = {
        (row.workspace_id, _binding_name(row.provider, row.account_id, row.base_url)): row.id
        for row in connections
    }
    direct: dict[tuple[UUID, str], dict[UUID | None, UUID]] = {}
    for row in connections:
        if row.account_id == WORKSPACE_ACCOUNT:
            member = None
        elif row.account_id.startswith(MEMBER_PREFIX):
            member = UUID(row.account_id.removeprefix(MEMBER_PREFIX))
        else:
            continue
        alias = _binding_name(row.provider, DIRECT_ACCOUNT, row.base_url)
        direct.setdefault((row.workspace_id, alias), {})[member] = row.id
    for workspace_id, binding, creator in bind.execute(
        sa.select(
            trigger.c.workspace_id, trigger.c.binding, trigger.c.created_by_member_id
        ).distinct()
    ):
        here = sa.and_(
            trigger.c.workspace_id == workspace_id,
            trigger.c.binding == binding,
            trigger.c.created_by_member_id.is_(creator)
            if creator is None
            else trigger.c.created_by_member_id == creator,
        )
        feeds = direct.get((workspace_id, binding), {})
        connection_id = named.get((workspace_id, binding)) or feeds.get(creator) or feeds.get(None)
        bind.execute(
            sa.delete(trigger).where(here)
            if connection_id is None
            else sa.update(trigger)
            .where(here)
            .values(connection_id=connection_id, updated_at=sa.func.now())
        )
    with op.batch_alter_table("source_trigger", copy_from=TRIGGER_ON_BOTH) as batch:
        batch.alter_column("connection_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("binding")
        batch.create_unique_constraint(
            "source_trigger_conversation",
            ["workspace_id", "conversation_id", "connection_id", "resource"],
        )
        batch.create_foreign_key(
            TRIGGER_CONNECTION_FK, "connection", ["connection_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("source_trigger_connection", ["workspace_id", "connection_id"])


def downgrade() -> None:
    """Each trigger takes back the binding its connection derives — the sentinel handle where the
    connection's own names no broker account, which is what a feed on the workspace's own
    credential answered to, whoever read it. Each narrowed trigger returns to a table of its own
    keeping its id, and `source_trigger` is left holding the whole-account rows alone, the only ones
    the narrow key admits."""
    bind = op.get_bind()
    trigger, watch = _triggers(), _watches()
    with op.batch_alter_table("source_trigger", copy_from=TRIGGER_ON_A_CONNECTION) as batch:
        batch.drop_index("source_trigger_connection")
        batch.drop_constraint("source_trigger_conversation", type_="unique")
        batch.drop_constraint(TRIGGER_CONNECTION_FK, type_="foreignkey")
        batch.add_column(sa.Column("binding", sa.Text(), nullable=False, server_default=""))
    for row in bind.execute(sa.select(_connections())):
        workspace_feed = row.account_id == WORKSPACE_ACCOUNT or row.account_id.startswith(
            MEMBER_PREFIX
        )
        account = DIRECT_ACCOUNT if workspace_feed else row.account_id
        bind.execute(
            sa.update(trigger)
            .where(trigger.c.connection_id == row.id)
            .values(binding=_binding_name(row.provider, account, row.base_url))
        )
    op.create_table(
        "source_resource_watch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("binding", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("delivery", sa.Text(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resource <> ''", name="source_resource_watch_narrowed"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "binding",
            "resource",
            name="source_resource_watch_resource",
        ),
    )
    op.create_index(
        "source_resource_watch_binding", "source_resource_watch", ["workspace_id", "binding"]
    )
    bind.execute(
        watch.insert().from_select(
            [column.name for column in watch.c],
            sa.select(*(trigger.c[column.name] for column in watch.c)).where(
                trigger.c.resource != ""
            ),
        )
    )
    bind.execute(sa.delete(trigger).where(trigger.c.resource != ""))
    with op.batch_alter_table("source_trigger", copy_from=TRIGGER_ON_BOTH) as batch:
        batch.alter_column("binding", existing_type=sa.Text(), server_default=None)
        batch.drop_column("connection_id")
        batch.drop_column("resource")
        batch.create_unique_constraint(
            "source_trigger_conversation", ["workspace_id", "conversation_id", "binding"]
        )
        batch.create_index("source_trigger_binding", ["workspace_id", "binding"])
