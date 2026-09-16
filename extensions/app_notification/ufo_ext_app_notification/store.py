"""The notification table and every read and write over it.

A notification is one row in an agent's inbox about one member: the agent it is for, the member it
concerns, a `subject` naming the stable thing it is about, and the latest `body` said about that
subject. Open rows fold the way a source folds pages by identity — a second post on an open subject
rewrites the body and counts an occurrence instead of adding a row — so a sync over four hundred
changed pages under one subject is one row saying four hundred. A row is open until a drain turn
reads it, which `triaged_turn_id` records; the next post on the same subject reopens that row, so a
row is the subject's running record: first and last raised, times raised, the latest body and its
producer, and where the current cycle stands.

A **lane** is one inbox for one member under one exact runtime config: the
`(to_agent_id, member_id, runtime_config)` triple the drain folds into one turn. The per-minute
drain claims a lane's open rows under a lease, so an overlapping tick never hands the same rows to
two turns, and a lease that lapses is the retry.

Each row carries the exact runtime config of the turn that last raised its current body. The
occurrence count binds the two, so a writer that changes the body without carrying its config makes
the row unable to reach connections or the internet rather than leaving an older grant beside
newer content.

The tables are the extension's own, with their own migrations, and every statement filters
`workspace_id` itself because `ExtensionContext.transaction` yields an unscoped connection."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext, TurnRuntimeConfig
from ufo.sdk.jobs import WorkspaceCandidates, agent_is_live, owner_candidates

EXTENSION_NAME = "app_notification"
NOTIFICATION_FLAG = "enable-notification-app"
NOTIFICATION_KIND = "notification"
NOTIFY_SUBJECTS_PER_TURN = 8
SUBJECT_MAX = 120
BODY_MAX = 1000


def _encoded_runtime_config(runtime_config: TurnRuntimeConfig | None) -> str:
    return json.dumps(
        None if runtime_config is None else runtime_config.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )


def _parsed_runtime_config(encoded: str) -> TurnRuntimeConfig | None:
    decoded = json.loads(encoded)
    return None if decoded is None else TurnRuntimeConfig.model_validate(decoded)


FAIL_CLOSED_RUNTIME_CONFIG = TurnRuntimeConfig(internet_access=False)
ENCODED_FAIL_CLOSED_RUNTIME_CONFIG = _encoded_runtime_config(FAIL_CLOSED_RUNTIME_CONFIG)

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
    sa.Column("runtime_config", sa.Text, nullable=True),
    sa.Column("scope_occurrences", sa.Integer, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("triaged_turn_id", sa.Uuid, nullable=True),
    sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("delivered_turn_id", sa.Uuid, nullable=True),
    sa.Column("delivered_surface", sa.Text, nullable=True),
    sa.Column("last_raised_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index(
        "notification_subject", "workspace_id", "to_agent_id", "member_id", "subject", unique=True
    ),
    sa.Index("notification_delivered_turn", "workspace_id", "delivered_turn_id"),
)
notification_delivery = sa.Table(
    "notification_delivery",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("delivery_key", sa.Text, primary_key=True),
    sa.Column("request_digest", sa.Text, nullable=True),
    sa.Column("conversation_id", sa.Uuid, nullable=True),
    sa.Column("agent_id", sa.Uuid, nullable=True),
    sa.Column("surface", sa.Text, nullable=True),
    sa.Column("relay_turn_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "relay_turn_id", name="notification_delivery_relay_turn"),
)

_COLUMNS = tuple(notification.c)
_OPEN = notification.c.triaged_turn_id.is_(None)
_TRIAGED = notification.c.triaged_turn_id.is_not(None)


def _effective_runtime_config() -> sa.ColumnElement[str]:
    return sa.case(
        (
            sa.and_(
                notification.c.runtime_config.is_not(None),
                notification.c.scope_occurrences == notification.c.occurrences,
            ),
            notification.c.runtime_config,
        ),
        else_=ENCODED_FAIL_CLOSED_RUNTIME_CONFIG,
    )


@dataclass(frozen=True)
class Lane:
    """One inbox for one member under one exact runtime config. `stored` is every text that config
    is stored as: the image a deploy replaces encodes keys this release drops, and its rows claim
    with the rest."""

    agent_id: UUID
    member_id: UUID
    runtime_config: TurnRuntimeConfig | None
    stored: frozenset[str]


@dataclass(frozen=True)
class DeliveryDestination:
    conversation_id: UUID
    agent_id: UUID
    surface: str


class DeliveryRequestConflict(ValueError):
    """A delivery key already reserves another request or an incomplete historical request."""


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
    runtime_config: TurnRuntimeConfig | None
    triaged_turn_id: UUID | None
    triaged_at: datetime | None
    delivered_turn_id: UUID | None
    delivered_surface: str | None
    last_raised_at: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def name(self) -> str:
        return self.id.hex

    @property
    def lane(self) -> Lane:
        return Lane(
            agent_id=self.to_agent_id,
            member_id=self.member_id,
            runtime_config=self.runtime_config,
            stored=frozenset({_encoded_runtime_config(self.runtime_config)}),
        )


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
        runtime_config=(
            FAIL_CLOSED_RUNTIME_CONFIG
            if row["runtime_config"] is None or row["scope_occurrences"] != row["occurrences"]
            else _parsed_runtime_config(row["runtime_config"])
        ),
        triaged_turn_id=row["triaged_turn_id"],
        triaged_at=None if row["triaged_at"] is None else _aware(row["triaged_at"]),
        delivered_turn_id=row["delivered_turn_id"],
        delivered_surface=row["delivered_surface"],
        last_raised_at=_aware(row["last_raised_at"]),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )


def _claim_available(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(notification.c.claim_expires_at.is_(None), notification.c.claim_expires_at < now)


async def inbox_agent_id(ctx: ExtensionContext) -> UUID | None:
    """The Notification app's own agent: the live row this extension provisioned, whatever name it
    landed under. A shipped agent is identified by the extension that ships it, never by the row's
    name — a member's own agent may hold `notification`, and the provision then lands on a free
    variant. Every writer and reader of the inbox resolves it here, so no row is ever addressed to,
    or woken on, an agent the app did not ship."""
    return next(
        (
            agent.id
            for agent in await ctx.workspace_agents()
            if agent.provisioned_by == EXTENSION_NAME and not agent.archived
        ),
        None,
    )


def untriaged_workspaces() -> WorkspaceCandidates:
    """The drain's candidate seam: one workspace holding an open row whose lease is free and whose
    inbox agent is live."""

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        return (
            sa.select(notification.c.workspace_id)
            .where(
                _OPEN,
                _claim_available(now),
                agent_is_live(notification.c.workspace_id, notification.c.to_agent_id),
            )
            .distinct()
        )

    return owner_candidates(due)


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
        runtime_config: TurnRuntimeConfig | None,
    ) -> Posted | Refused:
        """One upsert over one row per subject per lane: a new subject is a row, an open subject
        takes the new body and counts, a triaged subject reopens — counted on, producer replaced,
        stamps cleared — so the lane is due again. A turn that has already opened
        `NOTIFY_SUBJECTS_PER_TURN` subjects is refused a new one, with the reason, so the model
        folds instead of fanning out."""
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
                        _OPEN,
                    )
                )
            ).scalar_one_or_none()
            if held is None and opened >= NOTIFY_SUBJECTS_PER_TURN:
                return Refused(
                    f"this turn already raised {NOTIFY_SUBJECTS_PER_TURN} subjects; fold what is "
                    "left into one of them or leave it"
                )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            encoded_runtime_config = _encoded_runtime_config(runtime_config)
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
                        runtime_config=encoded_runtime_config,
                        scope_occurrences=1,
                        claim_expires_at=None,
                        triaged_turn_id=None,
                        triaged_at=None,
                        delivered_turn_id=None,
                        delivered_surface=None,
                        last_raised_at=sa.func.now(),
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
                            "produced_by_agent_id": agent_id,
                            "produced_by_agent_name": agent_name,
                            "produced_by_turn_id": turn_id,
                            "produced_in_conversation_id": conversation_id,
                            "runtime_config": encoded_runtime_config,
                            "scope_occurrences": notification.c.occurrences + 1,
                            "claim_expires_at": sa.case(
                                (_TRIAGED, sa.null()), else_=notification.c.claim_expires_at
                            ),
                            "triaged_turn_id": None,
                            "delivered_turn_id": None,
                            "delivered_surface": None,
                            "last_raised_at": sa.func.now(),
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
                        .order_by(notification.c.last_raised_at.desc(), notification.c.id)
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

    async def lanes_with_untriaged(self, cooldown_seconds: int) -> tuple[Lane, ...]:
        """Every lane holding an open row whose lease is free, less the lanes a drain turn read
        inside `cooldown_seconds` — the bound on a woken turn's inbox waking it again. `triaged_at`
        is the read's clock and outlives a reopen, so a subject raised again right after its turn
        waits the same cooldown a fresh one does."""
        now = datetime.now(UTC)
        scope = _effective_runtime_config().label("runtime_config")
        lane_columns = (notification.c.to_agent_id, notification.c.member_id, scope)
        async with self.ctx.transaction() as connection:
            open_lanes = (
                await connection.execute(
                    sa.select(*lane_columns)
                    .where(
                        notification.c.workspace_id == self.ctx.workspace_id,
                        _OPEN,
                        _claim_available(now),
                    )
                    .distinct()
                    .order_by(notification.c.to_agent_id, notification.c.member_id, scope)
                )
            ).all()
            recent = (
                await connection.execute(
                    sa.select(*lane_columns)
                    .where(
                        notification.c.workspace_id == self.ctx.workspace_id,
                        notification.c.triaged_at >= now - timedelta(seconds=cooldown_seconds),
                    )
                    .distinct()
                )
            ).all()
        cooling = {
            (row.to_agent_id, row.member_id, _parsed_runtime_config(row.runtime_config))
            for row in recent
        }
        stored: dict[tuple[UUID, UUID, TurnRuntimeConfig | None], set[str]] = {}
        for row in open_lanes:
            key = (row.to_agent_id, row.member_id, _parsed_runtime_config(row.runtime_config))
            stored.setdefault(key, set()).add(row.runtime_config)
        return tuple(
            Lane(
                agent_id=agent_id,
                member_id=member_id,
                runtime_config=runtime_config,
                stored=frozenset(texts),
            )
            for (agent_id, member_id, runtime_config), texts in stored.items()
            if (agent_id, member_id, runtime_config) not in cooling
        )

    async def claim(self, lane: Lane, limit: int, lease_seconds: int) -> tuple[Notification, ...]:
        """Lease up to `limit` oldest open rows of one lane. The lease UPDATE stamps and returns its
        rows atomically, so overlapping ticks partition the lane rather than both reading it."""
        now = datetime.now(UTC)
        in_lane = sa.and_(
            notification.c.workspace_id == self.ctx.workspace_id,
            notification.c.to_agent_id == lane.agent_id,
            notification.c.member_id == lane.member_id,
            _effective_runtime_config().in_(lane.stored),
        )
        selected = (
            sa.select(notification.c.id)
            .where(in_lane, _OPEN, _claim_available(now))
            .order_by(notification.c.created_at, notification.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("open_notification")
        )
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.update(notification)
                        .where(
                            notification.c.id.in_(sa.select(selected.c.id)),
                            in_lane,
                            _OPEN,
                            _claim_available(now),
                        )
                        .values(
                            claim_expires_at=now + timedelta(seconds=lease_seconds),
                            updated_at=sa.func.now(),
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(sorted((_row(row) for row in rows), key=lambda row: (row.created_at, row.id)))

    async def mark_triaged(self, batch: tuple[Notification, ...], turn_id: UUID) -> None:
        """Close the rows the turn `turn_id` read — exactly as it read them. A post that folded into
        a claimed row after the claim moved its `occurrences`, so the body the turn carries is not
        the body the row holds; such a row is left open under its lease, and the next tick after the
        lease lapses reads the folded body. `occurrences` is the guard rather than `updated_at`
        because every fold moves it and a clock may not."""
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(notification)
                .where(
                    notification.c.workspace_id == self.ctx.workspace_id,
                    sa.or_(
                        *(
                            sa.and_(
                                notification.c.id == row.id,
                                notification.c.occurrences == row.occurrences,
                            )
                            for row in batch
                        )
                    ),
                )
                .values(
                    triaged_turn_id=turn_id,
                    triaged_at=sa.func.now(),
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )

    async def prepare_delivery(
        self,
        delivery_key: str,
        request_digest: str,
        destination: DeliveryDestination,
    ) -> DeliveryDestination:
        """Reserve one exact delivery request and destination before its relay can start."""
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(notification_delivery)
                .values(
                    workspace_id=self.ctx.workspace_id,
                    delivery_key=delivery_key,
                    request_digest=request_digest,
                    conversation_id=destination.conversation_id,
                    agent_id=destination.agent_id,
                    surface=destination.surface,
                    relay_turn_id=None,
                    created_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        notification_delivery.c.workspace_id,
                        notification_delivery.c.delivery_key,
                    ]
                )
            )
        stored = await self.delivery_destination(delivery_key, request_digest)
        if stored is None:
            raise RuntimeError("a prepared notification delivery is missing")
        return stored

    async def delivery_destination(
        self, delivery_key: str, request_digest: str
    ) -> DeliveryDestination | None:
        """The destination already reserved for this exact request, if one exists."""
        async with self.ctx.transaction() as connection:
            stored = (
                await connection.execute(
                    sa.select(
                        notification_delivery.c.request_digest,
                        notification_delivery.c.conversation_id,
                        notification_delivery.c.agent_id,
                        notification_delivery.c.surface,
                    ).where(
                        notification_delivery.c.workspace_id == self.ctx.workspace_id,
                        notification_delivery.c.delivery_key == delivery_key,
                    )
                )
            ).one_or_none()
        if stored is None:
            return None
        if (
            stored.request_digest != request_digest
            or stored.conversation_id is None
            or stored.agent_id is None
            or stored.surface is None
        ):
            raise DeliveryRequestConflict(delivery_key)
        return DeliveryDestination(
            conversation_id=stored.conversation_id,
            agent_id=stored.agent_id,
            surface=stored.surface,
        )

    async def bind_delivery_turn(self, delivery_key: str, turn_id: UUID) -> None:
        """Bind the admitted relay to its pre-reserved delivery identity."""
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(notification_delivery)
                .where(
                    notification_delivery.c.workspace_id == self.ctx.workspace_id,
                    notification_delivery.c.delivery_key == delivery_key,
                    notification_delivery.c.relay_turn_id.is_(None),
                )
                .values(relay_turn_id=turn_id)
            )
            stored = (
                await connection.execute(
                    sa.select(notification_delivery.c.relay_turn_id).where(
                        notification_delivery.c.workspace_id == self.ctx.workspace_id,
                        notification_delivery.c.delivery_key == delivery_key,
                    )
                )
            ).scalar_one()
        if stored != turn_id:
            raise RuntimeError("a notification delivery key resolved to another relay turn")

    async def move_delivery_destination(
        self,
        delivery_key: str,
        current: DeliveryDestination,
        replacement: DeliveryDestination,
    ) -> DeliveryDestination | None:
        """Move an unadmitted delivery off a destination admission found archived."""
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(notification_delivery)
                .where(
                    notification_delivery.c.workspace_id == self.ctx.workspace_id,
                    notification_delivery.c.delivery_key == delivery_key,
                    notification_delivery.c.conversation_id == current.conversation_id,
                    notification_delivery.c.agent_id == current.agent_id,
                    notification_delivery.c.surface == current.surface,
                    notification_delivery.c.relay_turn_id.is_(None),
                )
                .values(
                    conversation_id=replacement.conversation_id,
                    agent_id=replacement.agent_id,
                    surface=replacement.surface,
                )
            )
            stored = (
                await connection.execute(
                    sa.select(
                        notification_delivery.c.conversation_id,
                        notification_delivery.c.agent_id,
                        notification_delivery.c.surface,
                    ).where(
                        notification_delivery.c.workspace_id == self.ctx.workspace_id,
                        notification_delivery.c.delivery_key == delivery_key,
                    )
                )
            ).one()
        if stored.conversation_id is None or stored.agent_id is None or stored.surface is None:
            return None
        return DeliveryDestination(
            conversation_id=stored.conversation_id,
            agent_id=stored.agent_id,
            surface=stored.surface,
        )

    async def is_delivery_turn(self, turn_id: UUID, idempotency_key: str | None) -> bool:
        """Whether this turn is a reserved relay, including before its id is bound."""
        delivery_identity = notification_delivery.c.relay_turn_id == turn_id
        if idempotency_key is not None:
            delivery_identity = sa.or_(
                delivery_identity,
                notification_delivery.c.delivery_key == idempotency_key,
            )
        async with self.ctx.transaction() as connection:
            found = (
                await connection.execute(
                    sa.select(
                        sa.or_(
                            sa.exists(
                                sa.select(notification_delivery.c.delivery_key).where(
                                    notification_delivery.c.workspace_id == self.ctx.workspace_id,
                                    delivery_identity,
                                )
                            ),
                            sa.exists(
                                sa.select(notification.c.id).where(
                                    notification.c.workspace_id == self.ctx.workspace_id,
                                    notification.c.delivered_turn_id == turn_id,
                                )
                            ),
                        )
                    )
                )
            ).scalar_one()
        return bool(found)

    async def deliverable(
        self, member_id: UUID, names: tuple[str, ...]
    ) -> tuple[Notification, ...]:
        """The named rows that concern `member_id` and have not been delivered."""
        rows = await self.rows()
        wanted = set(names)
        return tuple(
            row
            for row in rows
            if row.name in wanted and row.member_id == member_id and row.delivered_surface is None
        )

    async def mark_delivered(
        self, rows: tuple[Notification, ...], *, turn_id: UUID | None, surface: str
    ) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(notification)
                .where(
                    notification.c.workspace_id == self.ctx.workspace_id,
                    sa.or_(
                        *(
                            sa.and_(
                                notification.c.id == row.id,
                                notification.c.occurrences == row.occurrences,
                            )
                            for row in rows
                        )
                    ),
                )
                .values(
                    delivered_turn_id=turn_id, delivered_surface=surface, updated_at=sa.func.now()
                )
            )
