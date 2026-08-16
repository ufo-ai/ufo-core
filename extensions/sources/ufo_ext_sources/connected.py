"""A connected account's feeds are created with the connection: the registrar the
`connection_recorded` hook fires, and the job that retries a creation which did not land.

Connecting an account records a grant — an edge over an account — while a feed is one `source` row
per stream, so a member who connected a provider still had to ask for its content. This closes that
gap with no second member act and no wait: the connect flow publishes the connection it just
committed, and this gives it one row per canonical stream of its connector, private to the member
who owns the connection, granted to the main agent alone (`register_source` grants the main agent
when the caller names none). Canonical streams are the provider's core collections — the ones a
connector marks as the objects it exists to carry, one to nine per provider — so the feed is what
the account is for rather than every list its API publishes. Each stream's first sync reaches back
exactly as far as that stream declares.

The job is the retry path, never the producer: a hook that raised, a process that died between the
connection and its rows, or a main-agent grant that arrived by another path leaves streams
uncreated, and the next tick creates exactly those.

Nothing marks a connection done, because the rows are the record. The gate is per stream, and both
paths read the same two facts about the row a stream would take: `ext.sources()` holds it already,
so it is left exactly as it is — the member's or ours; or `removed_source_ids` says the member
removed it, so it stays removed, since registering again would reset its `removed_at`. A stream a
later connector release marks canonical therefore reaches accounts that already have feeds, and a
removal is permanent whichever path runs next — whether the member deleted the whole binding or
re-applied it with fewer streams, which removes the rows of the ones they dropped.

A stream added to an account that already holds a binding joins that binding instead of splitting
it, and takes its backfill request, because a binding reports the window of whichever of its rows
sorts first. It never takes that binding's disclosure: every row this creates is private to the
connection's owner, exactly as the first row is. An account holding a row the member took into
workspace-shared content is therefore left exactly as it is and gains nothing — only the member
widens what the workspace reads, through the `source` kind's own apply. So a canonical stream the
member never selected reaches neither workspace recall nor a shared binding's `per_page` trigger.

A per-tenant provider (its connector class leaves `base_url` empty, so a row without one fails every
run) is never auto-registered: only the member knows the tenant URL, so those wait for the `source`
kind's own apply."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ufo.sdk.context import ExtensionContext, SourceRecord
from ufo.sdk.grants import ConnectionRecorded, MainAgentConnection, main_agent_connections
from ufo.sdk.manifest import HookContext, HookOutcome
from ufo.sdk.sources import Connector, ConnectorSourceConfig
from ufo.sdk.subjects import member_subject
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import effective_days


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


@dataclass(frozen=True)
class ConnectedSources:
    """Register the canonical streams of a connected account the main agent holds — one connection
    for the hook that fires as it lands, every one of them for the job that retries. See the module
    docstring."""

    ext: ExtensionContext

    async def register(self, connection_id: UUID | None = None) -> None:
        live = await self.ext.sources()
        for connection in await main_agent_connections():
            if connection_id is not None and connection.id != connection_id:
                continue
            connector_cls = CONNECTORS.get(connection.provider)
            if connector_cls is None or not connector_cls.base_url:
                continue
            await self._register(connection, connector_cls(), live)

    async def _register(
        self,
        connection: MainAgentConnection,
        connector: Connector,
        live: tuple[SourceRecord, ...],
    ) -> None:
        subject = member_subject(connection.owner_member_id)
        bound = [record for record in live if record.connection_id == connection.id]
        if any(record.subject != subject for record in bound):
            return
        request = (
            None
            if not bound
            else ConnectorSourceConfig.model_validate(bound[0].config).backfill_days
        )
        registered_at = datetime.now(UTC)
        held = {record.id for record in live}
        fresh: dict[UUID, ConnectorSourceConfig] = {}
        for stream in connector.streams():
            if not stream.canonical:
                continue
            days = (
                None
                if stream.backfill_window_days is None
                else effective_days(request, stream.backfill_window_days)
            )
            config = ConnectorSourceConfig(
                account=connection.account_id,
                stream=stream.name,
                backfill_days=request,
                backfill_after=None if days is None else registered_at - timedelta(days=days),
            )
            source_id = self.ext.source_id(connection.provider, config, connection_id=connection.id)
            if source_id not in held:
                fresh[source_id] = config
        deleted = await self.ext.removed_source_ids(tuple(fresh))
        for source_id, config in fresh.items():
            if source_id in deleted:
                continue
            await self.ext.register_source(
                connection.provider,
                config,
                subject=subject,
                owner_member_id=connection.owner_member_id,
                connection_id=connection.id,
            )
