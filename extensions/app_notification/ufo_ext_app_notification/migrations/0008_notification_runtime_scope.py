"""Notification runtime causality. A missing or stale config authorizes no egress."""

import json

import sqlalchemy as sa
from alembic import op

from ufo.sdk.context import TurnRuntimeConfig

revision: str = "notification_0008"
down_revision: str | None = "notification_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260830013444"

notification = sa.table(
    "notification",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("occurrences", sa.Integer()),
    sa.column("produced_by_turn_id", sa.Uuid()),
    sa.column("delivered_turn_id", sa.Uuid()),
    sa.column("delivered_surface", sa.Text()),
    sa.column("runtime_config", sa.Text()),
    sa.column("scope_occurrences", sa.Integer()),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("runtime_config", sa.JSON()),
    sa.column("idempotency_key", sa.Text()),
)
notification_delivery = sa.table(
    "notification_delivery",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("delivery_key", sa.Text()),
    sa.column("request_digest", sa.Text()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("surface", sa.Text()),
    sa.column("relay_turn_id", sa.Uuid()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


FAIL_CLOSED_RUNTIME_CONFIG = TurnRuntimeConfig(internet_access=False)


def upgrade() -> None:
    with op.batch_alter_table("notification") as batch:
        batch.add_column(sa.Column("runtime_config", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scope_occurrences", sa.Integer(), nullable=True))
    op.create_table(
        "notification_delivery",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("surface", sa.Text(), nullable=True),
        sa.Column("relay_turn_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "delivery_key"),
        sa.UniqueConstraint(
            "workspace_id", "relay_turn_id", name="notification_delivery_relay_turn"
        ),
    )

    joined = notification.outerjoin(
        turn,
        sa.and_(
            turn.c.workspace_id == notification.c.workspace_id,
            turn.c.id == notification.c.produced_by_turn_id,
        ),
    )
    updates: list[dict[str, object]] = []
    for row in op.get_bind().execute(
        sa.select(
            notification.c.id,
            notification.c.occurrences,
            turn.c.id.label("turn_id"),
            turn.c.runtime_config,
        ).select_from(joined)
    ):
        runtime_config: TurnRuntimeConfig | None = FAIL_CLOSED_RUNTIME_CONFIG
        if row.occurrences == 1 and row.turn_id is not None:
            runtime_config = (
                None
                if row.runtime_config is None
                else TurnRuntimeConfig.model_validate(row.runtime_config)
            )
        updates.append(
            {
                "notification_id": row.id,
                "runtime_config": json.dumps(
                    None if runtime_config is None else runtime_config.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "scope_occurrences": row.occurrences,
            }
        )
    if updates:
        op.get_bind().execute(
            sa.update(notification)
            .where(notification.c.id == sa.bindparam("notification_id"))
            .values(
                runtime_config=sa.bindparam("runtime_config"),
                scope_occurrences=sa.bindparam("scope_occurrences"),
            ),
            updates,
        )
    relay = turn.alias("relay")
    op.get_bind().execute(
        sa.insert(notification_delivery).from_select(
            (
                notification_delivery.c.workspace_id,
                notification_delivery.c.delivery_key,
                notification_delivery.c.request_digest,
                notification_delivery.c.conversation_id,
                notification_delivery.c.agent_id,
                notification_delivery.c.surface,
                notification_delivery.c.relay_turn_id,
                notification_delivery.c.created_at,
            ),
            sa.select(
                notification.c.workspace_id,
                relay.c.idempotency_key,
                sa.null(),
                relay.c.conversation_id,
                relay.c.agent_id,
                notification.c.delivered_surface,
                notification.c.delivered_turn_id,
                sa.func.now(),
            )
            .select_from(
                notification.join(
                    relay,
                    sa.and_(
                        relay.c.workspace_id == notification.c.workspace_id,
                        relay.c.id == notification.c.delivered_turn_id,
                    ),
                )
            )
            .where(relay.c.idempotency_key.is_not(None))
            .distinct(),
        )
    )


def downgrade() -> None:
    op.drop_table("notification_delivery")
    with op.batch_alter_table("notification") as batch:
        batch.drop_column("scope_occurrences")
        batch.drop_column("runtime_config")
