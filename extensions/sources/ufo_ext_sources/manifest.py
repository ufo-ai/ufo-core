"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, the `direct` auth-proxy backend
itself, the `page` object kind that projects the synced pages back for read, the `source_trigger`
kind that delivers a shared connection's changed-page batches to its owning conversation,
the `page_change` hook that performs that delivery, the `user_prompt_submit` and `post_tool_use`
hooks that offer a conversation a trigger on a synced resource the text it reads links to, the
`connection_recorded` hook that gives a connected account its syncing streams as the connection
lands — so a member who connects a provider in chat needs no second act — and the job that mints the
workspace's own connection to each provider whose credential slot a member filled and retries a
creation which did not land. One extension, N backends —
each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker: the sync
runner routes a source on a broker connection to the extension that registers its provider
(Composio, Pipedream) and a source on a connection holding no account handle — the workspace's own,
where a member set the provider's credential instead of connecting an account — to the selected
fallback, this `direct` backend in every deploy that installs no other. `serve` sources the backends
into the sync driver's backend map (so a connected account syncs offline into memory) and builds
this sole `direct` proxy automatically with a reader scoped to these slots."""

from dataclasses import dataclass

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.context import CredentialAccess
from ufo.sdk.jobs import JobSpec, feed_workspaces
from ufo.sdk.manifest import HookSpec, Manifest, SourceProvider
from ufo.sdk.sources import Connector, ConnectorBackend
from ufo_ext_sources.connected import on_connection_recorded, retry_connected_sources
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.feeds import FEED_SLOTS, FEEDS
from ufo_ext_sources.pages import PAGE_OBJECT
from ufo_ext_sources.providers.googleads import DEVELOPER_TOKEN_ENV
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import SOURCE_TRIGGER_OBJECT, on_link_seen, on_page_change

NAME = "sources"
VERSION = "0.1.0"
DIRECT_BACKEND = "direct"
CONNECTED_SOURCES_RETRY_JOB = "connected_sources_retry"
CONNECTED_SOURCES_RETRY_SCHEDULE = "0 * * * * *"


@dataclass(frozen=True)
class ConnectorSourceFactory:
    connector: type[Connector]

    def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend:
        return ConnectorBackend(connector=self.connector())


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(SOURCE_TRIGGER_OBJECT, PAGE_OBJECT),
        hooks=(
            HookSpec(
                event="page_change",
                handler=on_page_change,
                page_change_failure_scope="batch",
            ),
            HookSpec(event="connection_recorded", handler=on_connection_recorded),
            HookSpec(event="user_prompt_submit", handler=on_link_seen, best_effort=True),
            HookSpec(event="post_tool_use", handler=on_link_seen),
        ),
        jobs=(
            JobSpec(
                name=CONNECTED_SOURCES_RETRY_JOB,
                schedule=CONNECTED_SOURCES_RETRY_SCHEDULE,
                handler=retry_connected_sources,
                candidates=feed_workspaces(FEED_SLOTS),
            ),
        ),
        sources=tuple(
            SourceProvider(backend=name, build=ConnectorSourceFactory(connector=cls))
            for name, cls in CONNECTORS.items()
        ),
        credentials=tuple(slot for feed in FEEDS.values() for slot in feed.slots),
        auth_proxies=(
            AuthProxySpec(
                backend=DIRECT_BACKEND,
                build=lambda credentials: DirectAuthProxy(credentials=credentials),
            ),
        ),
        deploy_keys=(DEVELOPER_TOKEN_ENV,),
    )
