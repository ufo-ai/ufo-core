"""a shared line's inbound traffic resolves its workspace by the sender's address

A surface whose provider belongs to the deploy rather than to a customer has one installation for
the whole env, so the installation identity cannot be what selects a tenant. `surface_address` is
what does: one row per (surface, address), naming the workspace and member that address reaches,
unique across the fleet because the phone is the identity. A row with `claim_expires_at` set is a
reservation the sender has not proved yet; clearing it against the proving message is what links
the address.

`routes_ingress` splits the two kinds of installation. A customer's own account (a Slack team) must
belong to one workspace, and the partial unique index keeps that true. The deploy's own project
routes nothing, so every workspace binds it — which is what the table-wide constraint used to
refuse, leaving iMessage working for exactly one workspace per env.

iMessage's linked phones move out of `surface_identity` into their fleet rows, and its stream cursor
out of the holding workspace's extension store into `surface_stream_cursor`, where one position
serves the one stream. Unproved claims and their receipts do not survive the move: they are worth
half an hour and the member re-runs the tool.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260820052830"
down_revision: str | None = "20260819175749"
branch_labels: str | None = None
depends_on: str | None = None

IMESSAGE = "imessage"
CURSOR_KEY = "stream:shared:cursor"
CLAIM_PREFIX = "phone-claim:"
RECEIPT_PREFIX = "phone-receipt:"
INSTALLATION_INDEX = "surface_installation_surface_installation_id_key"
ROUTES_INGRESS = sa.text("routes_ingress")

INSTALLATION_WITH_CONSTRAINT = sa.Table(
    "surface_installation",
    sa.MetaData(),
    sa.Column(
        "workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True, nullable=False
    ),
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("installation_id", sa.Text, nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("surface", "installation_id", name=INSTALLATION_INDEX),
    sa.CheckConstraint("installation_id <> ''", name="surface_installation_id_nonempty"),
)

INSTALLATION_WITH_COLUMN = sa.Table(
    "surface_installation",
    sa.MetaData(),
    sa.Column(
        "workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True, nullable=False
    ),
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("installation_id", sa.Text, nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("routes_ingress", sa.Boolean, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("installation_id <> ''", name="surface_installation_id_nonempty"),
)

installation = sa.table(
    "surface_installation",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("surface", sa.Text()),
    sa.column("installation_id", sa.Text()),
    sa.column("routes_ingress", sa.Boolean()),
)

identity = sa.table(
    "surface_identity",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
    sa.column("surface", sa.Text()),
    sa.column("external_id", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

address = sa.table(
    "surface_address",
    sa.column("surface", sa.Text()),
    sa.column("address", sa.Text()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
    sa.column("claim_expires_at", sa.DateTime(timezone=True)),
    sa.column("proved_by", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

stream_cursor = sa.table(
    "surface_stream_cursor",
    sa.column("surface", sa.Text()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("installation_id", sa.Text()),
    sa.column("sequence", sa.BigInteger()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
)


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table(
        "surface_installation", copy_from=INSTALLATION_WITH_CONSTRAINT
    ) as batch:
        batch.add_column(sa.Column("routes_ingress", sa.Boolean, nullable=True))
        batch.drop_constraint(INSTALLATION_INDEX, type_="unique")
    bind.execute(sa.update(installation).values(routes_ingress=installation.c.surface != IMESSAGE))
    with op.batch_alter_table("surface_installation", copy_from=INSTALLATION_WITH_COLUMN) as batch:
        batch.alter_column("routes_ingress", existing_type=sa.Boolean(), nullable=False)
    op.create_index(
        INSTALLATION_INDEX,
        "surface_installation",
        ["surface", "installation_id"],
        unique=True,
        postgresql_where=ROUTES_INGRESS,
        sqlite_where=ROUTES_INGRESS,
    )

    op.create_table(
        "surface_address",
        sa.Column("surface", sa.Text, primary_key=True),
        sa.Column("address", sa.Text, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proved_by", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("address <> ''", name="surface_address_address_nonempty"),
        sa.CheckConstraint(
            "claim_expires_at is null or proved_by is null",
            name="surface_address_claim_or_proof",
        ),
    )
    op.create_index("ix_surface_address_workspace_id", "surface_address", ["workspace_id"])
    op.create_table(
        "surface_stream_cursor",
        sa.Column("surface", sa.Text, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=True),
        sa.Column("installation_id", sa.Text, nullable=False),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("installation_id <> ''", name="surface_stream_cursor_id_nonempty"),
    )

    now = sa.func.now()
    holder = sa.select(installation.c.workspace_id, installation.c.installation_id).where(
        installation.c.surface == IMESSAGE
    )
    bind.execute(
        sa.insert(address).from_select(
            ["surface", "address", "workspace_id", "member_id", "created_at", "updated_at"],
            sa.select(
                identity.c.surface,
                identity.c.external_id,
                identity.c.workspace_id,
                identity.c.member_id,
                identity.c.created_at,
                now,
            ).where(
                identity.c.surface == IMESSAGE,
                identity.c.workspace_id.in_(
                    sa.select(installation.c.workspace_id).where(installation.c.surface == IMESSAGE)
                ),
            ),
        )
    )
    bind.execute(sa.delete(identity).where(identity.c.surface == IMESSAGE))

    holders = {row.workspace_id: row.installation_id for row in bind.execute(holder)}
    for row in bind.execute(
        sa.select(ext_store.c.workspace_id, ext_store.c.value).where(
            ext_store.c.extension == IMESSAGE, ext_store.c.key == CURSOR_KEY
        )
    ):
        installation_id = holders.get(row.workspace_id)
        if installation_id is None or not isinstance(row.value, int):
            continue
        bind.execute(
            sa.insert(stream_cursor).values(
                surface=IMESSAGE,
                workspace_id=None,
                installation_id=installation_id,
                sequence=row.value,
                created_at=now,
                updated_at=now,
            )
        )
    bind.execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == IMESSAGE,
            sa.or_(
                ext_store.c.key == CURSOR_KEY,
                ext_store.c.key.startswith(CLAIM_PREFIX, autoescape=True),
                ext_store.c.key.startswith(RECEIPT_PREFIX, autoescape=True),
            ),
        )
    )


def downgrade() -> None:
    pass
