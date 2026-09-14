"""Source-trigger causal request."""

import hashlib
import re
from collections import defaultdict
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0009"
down_revision: str | None = "sources_0008"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

OBJECT_NAME_MAX = 64
DIGEST_HEX = 8
HEAD_MAX = OBJECT_NAME_MAX - DIGEST_HEX - 1

trigger = sa.table(
    "source_trigger",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("connection_id", sa.Uuid()),
    sa.column("resource", sa.Text()),
    sa.column("streams", sa.Text()),
    sa.column("created_by_member_id", sa.Uuid()),
    sa.column("requesting_message_ref", sa.Uuid()),
)
connection = sa.table(
    "connection",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("provider", sa.Text()),
    sa.column("account_id", sa.Text()),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("speaker_member_id", sa.Uuid()),
    sa.column("admission_source", sa.Text()),
    sa.column("created_refs", sa.JSON()),
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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _account_name(provider: str, account_id: str) -> str:
    identity = f"{provider}\0{account_id}".encode()
    qualifier = hashlib.sha256(identity).hexdigest()[:DIGEST_HEX]
    head = f"{_slug(provider)}-{_slug(account_id)}".strip("-")[:HEAD_MAX].strip("-")
    return f"{head}-{qualifier}" if head else qualifier


def _trigger_name(account: str, conversation_id: UUID, resource: str, streams: str) -> str:
    identity = f"{account}\0{conversation_id}\0{resource}\0{streams}".encode()
    qualifier = hashlib.sha256(identity).hexdigest()[:DIGEST_HEX]
    head = f"{account}-{conversation_id.hex}"[:HEAD_MAX].strip("-")
    return f"{head}-{qualifier}"


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(sa.Column("requesting_message_ref", sa.Uuid(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            trigger,
            connection.c.provider,
            connection.c.account_id,
        ).select_from(
            trigger.join(
                connection,
                sa.and_(
                    connection.c.workspace_id == trigger.c.workspace_id,
                    connection.c.id == trigger.c.connection_id,
                ),
            )
        )
    ).mappings()
    turns_by_object: dict[tuple[UUID, UUID, UUID, str], list[sa.RowMapping]] = defaultdict(list)
    for row in bind.execute(sa.select(turn).where(turn.c.created_refs.is_not(None))).mappings():
        for ref in row["created_refs"] or ():
            if isinstance(ref, dict) and ref.get("kind") == "source_trigger":
                name = ref.get("name")
                if isinstance(name, str):
                    turns_by_object[
                        (row["workspace_id"], row["conversation_id"], row["agent_id"], name)
                    ].append(row)
    has_authorization = sa.inspect(bind).has_table("member_authorization")
    for row in rows:
        account = _account_name(row["provider"], row["account_id"])
        name = _trigger_name(
            account,
            row["conversation_id"],
            row["resource"],
            row["streams"],
        )
        creating = turns_by_object.get(
            (row["workspace_id"], row["conversation_id"], row["agent_id"], name), ()
        )
        if len(creating) != 1 or row["created_by_member_id"] is None:
            continue
        [creating_turn] = creating
        authorized: set[UUID] = set()
        if has_authorization:
            authorized.update(
                bind.execute(
                    sa.select(authorization.c.requested_by).where(
                        authorization.c.workspace_id == row["workspace_id"],
                        authorization.c.member_id == row["created_by_member_id"],
                        authorization.c.call == "object_apply",
                        authorization.c.request_key.like(f"{creating_turn['id']}/object_apply/%"),
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
                sa.update(trigger)
                .where(trigger.c.id == row["id"])
                .values(requesting_message_ref=next(iter(candidates)))
            )


def downgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_column("requesting_message_ref")
