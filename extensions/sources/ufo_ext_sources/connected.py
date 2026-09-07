"""A connection's feeds are created with the connection: the registrar the `connection_recorded`
hook fires, and the job that retries a creation which did not land.

A connection is one account's authority and a feed is one `source` row per canonical stream of its
connector, so connecting an account is the whole of what a member does to sync it. Canonical
streams are the provider's core collections — the ones a connector marks as the objects it exists
to carry, one to nine per provider — so the feed is what the account is for rather than every list
its API publishes. Which agents read what it syncs is the grant's answer at read time, so this asks
nothing about grants and nothing about disclosure: the connection carries both.

A provider no broker grants is connected the same way from the other end: a member fills its
credential slot, and the job mints the workspace's own connection to it — no account handle, and
nobody's to keep private — so the one act of adding a key starts the feed, and the one act of
clearing the slot ends it: the next tick removes that connection, and its streams, their pages and
its grants go with it. A provider already holding a connection keeps it, whoever made it.

The job is the retry path for everything else, never the producer: a hook that raised, or a process
that died between the connection and its rows, leaves streams uncreated, and the next tick creates
exactly those. It is also how a per-tenant provider's rows arrive — a connector that declares no
host of its own dials the connection's `base_url`, and a connection that carries none registers
nothing until the member names it, which the next tick reads.

Nothing marks a connection done, because the rows are the record: a stream a later connector release
marks canonical reaches accounts that already sync. Each stream's first sync reaches back as far as
the connection's `backfill_days` asks, and where it asks nothing, as far as the stream declares.
Raising that window re-pins the rows it now reaches further back and refetches them; lowering it
leaves them where they are, because the pages between the two floors would otherwise be stranded —
never re-walked, never tombstoned."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ufo.sdk.context import ExtensionContext, SourceRecord
from ufo.sdk.grants import ConnectionRecorded, FeedConnection, feed_connections
from ufo.sdk.manifest import HookContext, HookOutcome
from ufo.sdk.sources import ConnectorSourceConfig, StreamSpec
from ufo_ext_sources.registry import CONNECTORS


async def on_connection_recorded(ctx: HookContext) -> HookOutcome:
    """Give the connection the member just made its feeds, before the callback answers them."""
    match ctx.payload:
        case ConnectionRecorded(connection_id=connection_id):
            await ConnectedSources(ext=ctx.ext).register(connection_id=connection_id)
        case _:
            raise RuntimeError("sources hook fired on a non-connection_recorded payload")
    return None


async def retry_connected_sources(ctx: ExtensionContext) -> None:
    """Create the rows a connect-time creation did not. See the module docstring."""
    await ConnectedSources(ext=ctx).register()


def backfill_days(connection: FeedConnection, stream: StreamSpec) -> int | None:
    """How far back one row's first sync reaches: the connection's window where it names one, else
    the window the stream declares. A stream declaring none reads its whole history and takes no
    cutoff at all, which is what None on the row means."""
    if stream.backfill_window_days is None:
        return None
    if connection.backfill_days is None:
        return stream.backfill_window_days
    return connection.backfill_days


@dataclass(frozen=True)
class ConnectedSources:
    """Register the canonical streams of a connection — one connection for the hook that fires as it
    lands, every one of them for the job that retries. See the module docstring."""

    ext: ExtensionContext

    async def register(self, connection_id: UUID | None = None) -> None:
        if connection_id is None:
            await self._settle_keyed_connections()
        live = await self.ext.sources()
        for connection in await feed_connections():
            if connection_id is not None and connection.id != connection_id:
                continue
            connector_cls = CONNECTORS.get(connection.provider)
            if connector_cls is None or not (connector_cls.base_url or connection.base_url):
                continue
            streams = {stream.name: stream for stream in connector_cls().streams()}
            await self._create(connection, streams, live)
            await self._rewindow(connection, streams, live)

    async def _settle_keyed_connections(self) -> None:
        """The workspace's own connection to each provider whose credential slot a member filled,
        and none to a provider whose slot is empty — the slot is the whole of a keyed feed's
        lifecycle. Filling it mints the connection here and its canonical streams register below on
        the same tick; clearing it removes the connection here, and its streams, their pages and
        every grant on it go by cascade, so nothing keeps asking for a key that is gone and nothing
        it synced stays recallable.

        A provider already holding a connection is left alone, whoever made it. An account someone
        connected is the authority for that provider, and a second connection beside it would sync
        the same content twice under two disclosures."""
        connections = await feed_connections()
        keyed = await self.ext.credentials.stored_slots()
        for connection in connections:
            if (
                connection.provider in CONNECTORS
                and connection.owner_member_id is None
                and connection.account_id == ""
                and connection.provider not in keyed
            ):
                await self.ext.remove_connection(connection.id)
        connected = {connection.provider for connection in connections}
        for provider in sorted((CONNECTORS.keys() - connected) & keyed):
            await self.ext.register_connection(provider)

    async def _create(
        self,
        connection: FeedConnection,
        streams: dict[str, StreamSpec],
        live: tuple[SourceRecord, ...],
    ) -> None:
        """One row per canonical stream the connection does not hold yet. The row a stream would
        take is derived, not searched, so a stream already syncing is left exactly as it is and a
        stream a later connector release marks canonical joins a connection registered long ago."""
        held = {record.id for record in live}
        registered_at = datetime.now(UTC)
        for stream in streams.values():
            if not stream.canonical:
                continue
            days = backfill_days(connection, stream)
            config = ConnectorSourceConfig(
                stream=stream.name,
                backfill_days=days,
                backfill_after=None if days is None else registered_at - timedelta(days=days),
            )
            source_id = self.ext.source_id(connection.provider, config, connection_id=connection.id)
            if source_id in held:
                continue
            await self.ext.register_source(connection.provider, config, connection_id=connection.id)

    async def _rewindow(
        self,
        connection: FeedConnection,
        streams: dict[str, StreamSpec],
        live: tuple[SourceRecord, ...],
    ) -> None:
        """Re-pin the rows a raised window now reaches past, and refetch them, so a connection that
        reads further back actually reads it.

        The new floor is measured from the instant each row was registered, reconstructed as its own
        pin plus the days it was pinned with. Against `now` instead, widening an old connection
        would pin a LATER floor than the one it replaced.

        Only a widening lands. Narrowing would strand the pages between the two floors — never
        re-walked, never tombstoned — so a lowered window leaves every live row exactly where it is
        and governs only the rows registered after it."""
        configs: dict[UUID, ConnectorSourceConfig] = {}
        for record in live:
            if record.connection_id != connection.id:
                continue
            config = ConnectorSourceConfig.model_validate(record.config)
            stream = streams.get(config.stream)
            if stream is None or config.backfill_after is None:
                continue
            pinned = (
                stream.backfill_window_days
                if config.backfill_days is None
                else config.backfill_days
            )
            if pinned is None:
                raise RuntimeError(f"source {record.id} is pinned to a window it does not name")
            days = backfill_days(connection, stream)
            anchor = config.backfill_after + timedelta(days=pinned)
            pin = None if days is None else anchor - timedelta(days=days)
            if pin is not None and pin >= config.backfill_after:
                continue
            configs[record.id] = ConnectorSourceConfig(
                stream=config.stream, backfill_days=days, backfill_after=pin
            )
        await self.ext.rewindow_sources(configs, refetch=frozenset(configs))
