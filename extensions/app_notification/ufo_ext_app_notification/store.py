"""The notification table and every read and write over it.

A notification is one row in an agent's inbox about one member: the agent it is for, the member it
concerns, a `subject` naming the stable thing it is about, and the latest `body` said about that
subject. Rows fold the way a source folds pages by identity — a second post on a subject already
raised rewrites the body and counts an occurrence instead of adding a row — so a sync over four
hundred changed pages under one subject is one row saying four hundred. A row stands until the
member dismisses it. The table is the extension's own, with its own migration, and every statement
filters `workspace_id` itself because `ExtensionContext.transaction` yields an unscoped
connection."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext

EXTENSION_NAME = "app_notification"
NOTIFICATION_KIND = "notification"
NOTIFY_SUBJECTS_PER_TURN = 8
SUBJECT_MAX = 120
BODY_MAX = 1000

_metadata = sa.MetaData()
notification = sa.Table(
    "notification",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("to_agent_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("occurrences", sa.Integer, nullable=False),
    sa.Column("produced_by_agent_id", sa.Uuid, nullable=False),
    sa.Column("produced_by_agent_name", sa.Text, nullable=False),
    sa.Column("produced_by_turn_id", sa.Uuid, nullable=False),
    sa.Column("produced_in_conversation_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index(
        "notification_subject",
        "workspace_id",
        "to_agent_id",
        "member_id",
        "subject",
        unique=True,
    ),
)

_COLUMNS = tuple(notification.c)


@dataclass(frozen=True)
class Notification:
    """One inbox row as a handler reads it. `name` is the row's object name: the id's hex, which
    the object-name grammar admits and which nothing else in the workspace can hold."""

    id: UUID
    to_agent_id: UUID
    member_id: UUID
    subject: str
    body: str
    occurrences: int
    produced_by_agent_id: UUID
    produced_by_agent_name: str
    produced_by_turn_id: UUID
    produced_in_conversation_id: UUID
    created_at: datetime
    updated_at: datetime

    @property
    def name(self) -> str:
        return self.id.hex


@dataclass(frozen=True)
class Posted:
    occurrences: int


@dataclass(frozen=True)
class Refused:
    reason: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row(row: sa.RowMapping) -> Notification:
    return Notification(
        id=row["id"],
        to_agent_id=row["to_agent_id"],
        member_id=row["member_id"],
        subject=row["subject"],
        body=row["body"],
        occurrences=row["occurrences"],
        produced_by_agent_id=row["produced_by_agent_id"],
        produced_by_agent_name=row["produced_by_agent_name"],
        produced_by_turn_id=row["produced_by_turn_id"],
        produced_in_conversation_id=row["produced_in_conversation_id"],
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )


@dataclass(frozen=True)
class NotificationStore:
    """The notification rows of one workspace, reached through the extension's own transaction."""

    ctx: ExtensionContext

    async def post(
        self,
        *,
        to_agent_id: UUID,
        member_id: UUID,
        subject: str,
        body: str,
        agent_id: UUID,
        agent_name: str,
        turn_id: UUID,
        conversation_id: UUID,
    ) -> Posted | Refused:
        """One upsert: a new subject is a row, a subject already raised takes the new body and
        counts. A turn that has already opened `NOTIFY_SUBJECTS_PER_TURN` subjects is refused a new
        one, with the reason, so the model folds instead of fanning out."""
        workspace_id = self.ctx.workspace_id
        async with self.ctx.transaction() as connection:
            opened = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(notification)
                    .where(
                        notification.c.workspace_id == workspace_id,
                        notification.c.produced_by_turn_id == turn_id,
                    )
                )
            ).scalar_one()
            held = (
                await connection.execute(
                    sa.select(notification.c.id).where(
                        notification.c.workspace_id == workspace_id,
                        notification.c.to_agent_id == to_agent_id,
                        notification.c.member_id == member_id,
                        notification.c.subject == subject,
                    )
                )
            ).scalar_one_or_none()
            if held is None and opened >= NOTIFY_SUBJECTS_PER_TURN:
                return Refused(
                    f"this turn already raised {NOTIFY_SUBJECTS_PER_TURN} subjects; fold what is "
                    "left into one of them or leave it"
                )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            occurrences = (
                await connection.execute(
                    insert(notification)
                    .values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        to_agent_id=to_agent_id,
                        member_id=member_id,
                        subject=subject,
                        body=body,
                        occurrences=1,
                        produced_by_agent_id=agent_id,
                        produced_by_agent_name=agent_name,
                        produced_by_turn_id=turn_id,
                        produced_in_conversation_id=conversation_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            notification.c.workspace_id,
                            notification.c.to_agent_id,
                            notification.c.member_id,
                            notification.c.subject,
                        ],
                        set_={
                            "body": body,
                            "occurrences": notification.c.occurrences + 1,
                            "updated_at": sa.func.now(),
                        },
                    )
                    .returning(notification.c.occurrences)
                )
            ).scalar_one()
        return Posted(occurrences=occurrences)

    async def rows(self) -> tuple[Notification, ...]:
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(*_COLUMNS)
                        .where(notification.c.workspace_id == self.ctx.workspace_id)
                        .order_by(notification.c.updated_at.desc(), notification.c.id)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_row(row) for row in rows)

    async def dismiss(self, row_id: UUID) -> bool:
        async with self.ctx.transaction() as connection:
            result = await connection.execute(
                sa.delete(notification).where(
                    notification.c.workspace_id == self.ctx.workspace_id,
                    notification.c.id == row_id,
                )
            )
        return result.rowcount == 1
