"""separate connections from connector grants"""

from collections import defaultdict
from collections.abc import Mapping
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | None = None
depends_on: str | None = None

DIRECT_ACCOUNT = "default"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text('LOCK TABLE "grant", source IN ACCESS EXCLUSIVE MODE'))
    old_grant = sa.table(
        "grant",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("grantor_member_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    conflict = bind.execute(
        sa.select(
            old_grant.c.workspace_id,
            old_grant.c.provider,
            old_grant.c.account_id,
        )
        .group_by(
            old_grant.c.workspace_id,
            old_grant.c.provider,
            old_grant.c.account_id,
        )
        .having(sa.func.count(sa.distinct(old_grant.c.grantor_member_id)) > 1)
        .limit(1)
    ).one_or_none()
    if conflict is not None:
        raise RuntimeError(
            f"{conflict.workspace_id}/{conflict.provider}/{conflict.account_id} has grants "
            "owned by different members; resolve its ownership before migrating"
        )
    conflict = bind.execute(
        sa.select(
            old_grant.c.workspace_id,
            old_grant.c.provider,
            old_grant.c.account_id,
        )
        .group_by(
            old_grant.c.workspace_id,
            old_grant.c.provider,
            old_grant.c.account_id,
        )
        .having(sa.func.count(sa.distinct(old_grant.c.host)) > 1)
        .limit(1)
    ).one_or_none()
    if conflict is not None:
        raise RuntimeError(
            f"{conflict.workspace_id}/{conflict.provider}/{conflict.account_id} has grants "
            "with different hosts; resolve its host before migrating"
        )

    member = sa.table(
        "member",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
    )
    agent = sa.table(
        "agent",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
    )
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
    )
    invalid = bind.execute(
        sa.select(old_grant.c.id, old_grant.c.workspace_id)
        .select_from(
            old_grant.outerjoin(
                member,
                sa.and_(
                    member.c.id == old_grant.c.grantor_member_id,
                    member.c.workspace_id == old_grant.c.workspace_id,
                ),
            )
            .outerjoin(
                agent,
                sa.and_(
                    agent.c.id == old_grant.c.agent_id,
                    agent.c.workspace_id == old_grant.c.workspace_id,
                ),
            )
            .outerjoin(
                conversation,
                sa.and_(
                    conversation.c.id == old_grant.c.conversation_id,
                    conversation.c.workspace_id == old_grant.c.workspace_id,
                ),
            )
        )
        .where(
            sa.or_(
                member.c.id.is_(None),
                agent.c.id.is_(None),
                conversation.c.id.is_(None),
            )
        )
        .limit(1)
    ).one_or_none()
    if invalid is not None:
        raise RuntimeError(
            f"grant {invalid.id} names a member, agent, or conversation outside workspace "
            f"{invalid.workspace_id}; repair its workspace references before migrating"
        )

    rows = list(
        bind.execute(
            sa.select(old_grant).order_by(
                old_grant.c.workspace_id,
                old_grant.c.provider,
                old_grant.c.account_id,
                old_grant.c.created_at,
                old_grant.c.id,
            )
        ).mappings()
    )
    grouped: dict[tuple[UUID, str, str], list[sa.RowMapping]] = defaultdict(list)
    for row in rows:
        grouped[(row["workspace_id"], row["provider"], row["account_id"])].append(row)

    source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("backend", sa.Text()),
        sa.column("config", sa.JSON()),
        sa.column("owner_member_id", sa.Uuid()),
        sa.column("removed_at", sa.DateTime(timezone=True)),
    )
    invalid = bind.execute(
        sa.select(source.c.id, source.c.workspace_id)
        .select_from(
            source.outerjoin(
                member,
                sa.and_(
                    member.c.id == source.c.owner_member_id,
                    member.c.workspace_id == source.c.workspace_id,
                ),
            )
        )
        .where(source.c.owner_member_id.is_not(None), member.c.id.is_(None))
        .limit(1)
    ).one_or_none()
    if invalid is not None:
        raise RuntimeError(
            f"source {invalid.id} names a member outside workspace {invalid.workspace_id}; "
            "repair its owner before migrating"
        )
    source_connections: dict[UUID, UUID] = {}
    for row in bind.execute(sa.select(source).where(source.c.removed_at.is_(None))).mappings():
        config = row["config"]
        account = config.get("account") if isinstance(config, Mapping) else None
        if not isinstance(account, str) or not account or account == DIRECT_ACCOUNT:
            continue
        group = grouped.get((row["workspace_id"], row["backend"], account))
        if not group:
            raise RuntimeError(
                f"source {row['id']} has no active {row['backend']!r} connection for account "
                f"{account!r}; remove or reconnect it before migrating"
            )
        owner_member_id = group[0]["grantor_member_id"]
        if row["owner_member_id"] != owner_member_id:
            raise RuntimeError(
                f"source {row['id']} is not owned by its {row['backend']!r} connection member; "
                "repair its owner before migrating"
            )
        source_connections[row["id"]] = group[0]["id"]

    with op.batch_alter_table("member") as batch:
        batch.create_unique_constraint("member_workspace_identity", ("workspace_id", "id"))
    with op.batch_alter_table("agent") as batch:
        batch.create_unique_constraint("agent_workspace_identity", ("workspace_id", "id"))
    with op.batch_alter_table("conversation") as batch:
        batch.create_unique_constraint("conversation_workspace_identity", ("workspace_id", "id"))

    op.create_table(
        "connection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("owner_member_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_member_id"],
            ["member.workspace_id", "member.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "provider", "account_id", name="connection_identity"),
        sa.UniqueConstraint("workspace_id", "id", name="connection_workspace_identity"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "owner_member_id",
            name="connection_owner_identity",
        ),
    )
    op.create_table(
        "connector_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("shared", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["connection.workspace_id", "connection.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "agent_id", "connection_id", name="connector_grant_identity"
        ),
    )

    new_connection = sa.table(
        "connection",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("owner_member_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    connector_grant = sa.table(
        "connector_grant",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("connection_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for group in grouped.values():
        first = group[0]
        latest = max(group, key=lambda row: (row["updated_at"], str(row["id"])))
        connection_id = first["id"]
        bind.execute(
            new_connection.insert().values(
                id=connection_id,
                workspace_id=first["workspace_id"],
                provider=first["provider"],
                account_id=first["account_id"],
                host=latest["host"],
                owner_member_id=first["grantor_member_id"],
                conversation_id=first["conversation_id"],
                created_at=first["created_at"],
                updated_at=latest["updated_at"],
            )
        )
        for row in group:
            bind.execute(
                connector_grant.insert().values(
                    id=row["id"],
                    workspace_id=row["workspace_id"],
                    agent_id=row["agent_id"],
                    connection_id=connection_id,
                    conversation_id=row["conversation_id"],
                    shared=row["shared"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_owner_member_id_fkey", type_="foreignkey")
        batch.add_column(sa.Column("connection_id", sa.Uuid(), nullable=True))
        batch.create_check_constraint(
            "source_connection_owner",
            "connection_id is null or owner_member_id is not null",
        )
        batch.create_foreign_key(
            "source_owner_workspace_fkey",
            "member",
            ["workspace_id", "owner_member_id"],
            ["workspace_id", "id"],
        )
        batch.create_foreign_key(
            "source_connection_owner_fkey",
            "connection",
            ["workspace_id", "connection_id", "owner_member_id"],
            ["workspace_id", "id", "owner_member_id"],
        )
    bound_source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("connection_id", sa.Uuid()),
    )
    for source_id, connection_id in source_connections.items():
        bind.execute(
            sa.update(bound_source)
            .where(bound_source.c.id == source_id)
            .values(connection_id=connection_id)
        )
    op.drop_index("grant_workspace", table_name="grant")
    op.drop_table("grant")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text('LOCK TABLE "connection", connector_grant, source IN ACCESS EXCLUSIVE MODE')
        )
    connection = sa.table(
        "connection",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("owner_member_id", sa.Uuid()),
    )
    connector_grant = sa.table(
        "connector_grant",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("connection_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    orphan = bind.execute(
        sa.select(
            connection.c.workspace_id,
            connection.c.provider,
            connection.c.account_id,
        )
        .select_from(
            connection.outerjoin(
                connector_grant,
                sa.and_(
                    connector_grant.c.workspace_id == connection.c.workspace_id,
                    connector_grant.c.connection_id == connection.c.id,
                ),
            )
        )
        .where(connector_grant.c.id.is_(None))
        .limit(1)
    ).one_or_none()
    if orphan is not None:
        raise RuntimeError(
            f"{orphan.workspace_id}/{orphan.provider}/{orphan.account_id} has no connector grants; "
            "the grant schema cannot represent this connection"
        )

    op.create_table(
        "grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("grantor_member_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("shared", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"]),
        sa.ForeignKeyConstraint(["grantor_member_id"], ["member.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "agent_id", "provider", "account_id", name="grant_identity"
        ),
    )
    op.create_index("grant_workspace", "grant", ["workspace_id"])
    old_grant = sa.table(
        "grant",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("grantor_member_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            connector_grant.c.id,
            connector_grant.c.workspace_id,
            connector_grant.c.agent_id,
            connection.c.provider,
            connection.c.account_id,
            connection.c.host,
            connection.c.owner_member_id.label("grantor_member_id"),
            connector_grant.c.conversation_id,
            connector_grant.c.shared,
            connector_grant.c.created_at,
            connector_grant.c.updated_at,
        ).select_from(
            connector_grant.join(
                connection,
                sa.and_(
                    connector_grant.c.workspace_id == connection.c.workspace_id,
                    connector_grant.c.connection_id == connection.c.id,
                ),
            )
        )
    ).mappings()
    for row in rows:
        bind.execute(old_grant.insert().values(**row))
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_connection_owner_fkey", type_="foreignkey")
        batch.drop_constraint("source_owner_workspace_fkey", type_="foreignkey")
        batch.drop_constraint("source_connection_owner", type_="check")
        batch.create_foreign_key(
            "source_owner_member_id_fkey",
            "member",
            ["owner_member_id"],
            ["id"],
        )
        batch.drop_column("connection_id")
    op.drop_table("connector_grant")
    op.drop_table("connection")
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_workspace_identity", type_="unique")
    with op.batch_alter_table("agent") as batch:
        batch.drop_constraint("agent_workspace_identity", type_="unique")
    with op.batch_alter_table("member") as batch:
        batch.drop_constraint("member_workspace_identity", type_="unique")
