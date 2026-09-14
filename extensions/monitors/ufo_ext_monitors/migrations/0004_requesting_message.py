"""Monitor causal request."""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "monitors_0004"
down_revision: str | None = "monitors_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

monitor = sa.table(
    "monitor",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("created_by_member_id", sa.Uuid()),
    sa.column("requesting_message_ref", sa.Uuid()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("speaker_member_id", sa.Uuid()),
    sa.column("admission_source", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
inbound = sa.table(
    "inbound_message",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("speaker_member_id", sa.Uuid()),
    sa.column("admission_source", sa.Text()),
    sa.column("consumed_turn_id", sa.Uuid()),
)
authorization = sa.table(
    "member_authorization",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
    sa.column("call", sa.Text()),
    sa.column("request_key", sa.Text()),
    sa.column("requested_by", sa.Uuid()),
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def upgrade() -> None:
    with op.batch_alter_table("monitor") as batch:
        batch.add_column(sa.Column("requesting_message_ref", sa.Uuid(), nullable=True))
    bind = op.get_bind()
    has_authorization = sa.inspect(bind).has_table("member_authorization")
    for row in bind.execute(sa.select(monitor)).mappings():
        if row["created_by_member_id"] is None:
            continue
        created_at = _aware(row["created_at"])
        creating = [
            candidate
            for candidate in bind.execute(
                sa.select(turn).where(
                    turn.c.workspace_id == row["workspace_id"],
                    turn.c.conversation_id == row["conversation_id"],
                    turn.c.agent_id == row["agent_id"],
                )
            ).mappings()
            if _aware(candidate["created_at"]) <= created_at <= _aware(candidate["updated_at"])
        ]
        if len(creating) != 1:
            continue
        [creating_turn] = creating
        authorized: set[UUID] = set()
        if has_authorization:
            authorized.update(
                bind.execute(
                    sa.select(authorization.c.requested_by).where(
                        authorization.c.workspace_id == row["workspace_id"],
                        authorization.c.member_id == row["created_by_member_id"],
                        authorization.c.call == "monitor",
                        authorization.c.request_key.like(f"{creating_turn['id']}/monitor/%"),
                    )
                ).scalars()
            )
        candidates = set(authorized)
        if not candidates:
            if (
                creating_turn["admission_source"] == "member"
                and creating_turn["speaker_member_id"] == row["created_by_member_id"]
            ):
                candidates.add(creating_turn["id"])
            candidates.update(
                bind.execute(
                    sa.select(inbound.c.id).where(
                        inbound.c.workspace_id == row["workspace_id"],
                        inbound.c.consumed_turn_id == creating_turn["id"],
                        inbound.c.admission_source == "member",
                        inbound.c.speaker_member_id == row["created_by_member_id"],
                    )
                ).scalars()
            )
        if len(candidates) == 1:
            bind.execute(
                sa.update(monitor)
                .where(monitor.c.id == row["id"])
                .values(requesting_message_ref=next(iter(candidates)))
            )


def downgrade() -> None:
    with op.batch_alter_table("monitor") as batch:
        batch.drop_column("requesting_message_ref")
