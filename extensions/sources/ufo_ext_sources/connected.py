"""A connection's feeds are created with the connection: the registrar the `connection_recorded`
hook fires, and the job that retries a creation which did not land.

A connection is one account's authority and a feed is one `source` row per canonical stream of its
connector, so connecting an account is the whole of what a member does to sync it. Canonical
streams are the provider's core collections — the ones a connector marks as the objects it exists
to carry, one to nine per provider — so the feed is what the account is for rather than every list
its API publishes. Which agents read what it syncs is the grant's answer at read time, so this asks
nothing about grants and nothing about disclosure: the connection carries both.

A provider no broker grants is connected the same way from the other end: a member fills its
credential slots, and the job mints the workspace's own connection to it — no account handle, and
nobody's to keep private — so the one act of adding the keys starts the feed, and the one act of
clearing them ends it: the next tick removes that connection, and its streams, their pages and its
grants go with it. Which slots those are is the connector's own declaration, one per header where a
provider authenticates with several keys, and a pair half filled is neither act. A provider already
holding a connection keeps it, whoever made it.

The job is the retry path for everything else, never the producer: a hook that raised, or a process
that died between the connection and its rows, leaves streams uncreated, and the next tick creates
exactly those. It is also how a per-tenant provider's rows arrive — a connector that declares no
host of its own dials the connection's `base_url`, and a connection that carries none registers
nothing until the member names it, which the next tick reads. A connector that dials no host at all
reads through broker tool executions instead, so its empty `base_url` is its whole address and its
rows land with the connection like a fixed-host provider's.

Nothing marks a connection done, because the rows are the record in both directions: a stream a
later connector release marks canonical reaches accounts that already sync, and a stream a release
stops marking canonical leaves them. Each stream's first sync reaches back as far as
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
from ufo_ext_sources.registry import CONNECTORS, direct_slots


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
            if connector_cls is None:
                continue
            if connector_cls.dials_host and not (connector_cls.base_url or connection.base_url):
                continue
            streams = {stream.name: stream for stream in connector_cls().streams()}
            await self._retire(connection, streams, live)
            await self._create(connection, streams, live)
            await self._rewindow(connection, streams, live)

    async def _settle_keyed_connections(self) -> None:
        """The workspace's own connection to each provider whose credentials a member filled, and
        none to a provider whose credentials are empty — the slots are the whole of a keyed feed's
        lifecycle. Filling them mints the connection here and its canonical streams register below
        on the same tick; clearing them removes the connection here, and its streams, their pages
        and every grant on it go by cascade, so nothing keeps asking for a key that is gone and
        nothing it synced stays recallable.

        A provider authenticating with several keys is settled on all of them, and the state between
        is neither act: one key of a pair mints nothing, because a connection that cannot
        authenticate would only park, and clearing one key of a filled pair removes nothing, because
        a member mid-rotation is not asking for their synced pages to be destroyed. Such a feed's
        runs skip while it stands, naming the slot still to fill.

        A provider already holding a connection is left alone, whoever made it. An account someone
        connected is the authority for that provider, and a second connection beside it would sync
        the same content twice under two disclosures."""
        connections = await feed_connections()
        stored = await self.ext.credentials.stored_slots()
        keyed: set[str] = set()
        keyless: set[str] = set()
        for provider, connector in CONNECTORS.items():
            slots = direct_slots(connector)
            filled = sum(slot in stored for slot in slots)
            if filled == len(slots):
                keyed.add(provider)
            elif filled == 0:
                keyless.add(provider)
        for connection in connections:
            if (
                connection.owner_member_id is None
                and connection.account_id == ""
                and connection.provider in keyless
            ):
                await self.ext.remove_connection(connection.id)
        connected = {connection.provider for connection in connections}
        for provider in sorted((CONNECTORS.keys() - connected) & keyed):
            await self.ext.register_connection(provider)

    async def _retire(
        self,
        connection: FeedConnection,
        streams: dict[str, StreamSpec],
        live: tuple[SourceRecord, ...],
    ) -> None:
        """A stream a later connector release stops marking canonical leaves the connections that
        already sync it, so the rows are the record in both directions of a release: the one that
        adds a stream and the one that withdraws it reach a connection registered long ago alike.

        Only a row this provider registered for a stream it no longer declares canonical is
        removed, and it is removed rather than parked, because the rows are the record of what a
        connection carries: a row left inert would be found by the next registration of that stream
        and read as a feed the member asked for. Its pages go with it by cascade, and a
        `source_trigger` narrowed to that stream stops being woken by it, while every other row of
        the connection — and every trigger on one — is left exactly as it is.

        A row of another backend names no stream (a gbrain origin's config is a repository, not a
        stream) and is never a row this registrar looks at, so it is passed over."""
        for record in live:
            if record.connection_id != connection.id or record.backend != connection.provider:
                continue
            stream = record.config.get("stream")
            if not isinstance(stream, str):
                continue
            declared = streams.get(stream)
            if declared is not None and declared.canonical:
                continue
            await self.ext.remove_source(record.id)

    async def _create(
        self,
        connection: FeedConnection,
        streams: dict[str, StreamSpec],
        live: tuple[SourceRecord, ...],
    ) -> None:
        """One row per canonical stream the connection does not hold yet, read off the streams this
        connection already holds — the natural key a connector's rows carry, so a stream already
        syncing is left exactly as it is and a stream a later connector release marks canonical
        joins a connection registered long ago. Only this connection's rows name a stream: a
        gbrain origin, a folder root and the sample feed each hang off a connection of their own
        and their configs declare no stream at all."""
        held = {record.config["stream"] for record in live if record.connection_id == connection.id}
        registered_at = datetime.now(UTC)
        for stream in streams.values():
            if not stream.canonical or stream.name in held:
                continue
            days = backfill_days(connection, stream)
            config = ConnectorSourceConfig(
                stream=stream.name,
                backfill_days=days,
                backfill_after=None if days is None else registered_at - timedelta(days=days),
            )
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
