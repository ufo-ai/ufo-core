"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, the `direct` auth-proxy backend
itself, the `source` object kind that turns a granted or keyed provider into syncing source rows,
the `page` object kind that projects the synced pages back for read, the `source_trigger` kind that
delivers shared-source changes to one conversation or one conversation per page, the `page_change`
hook that performs that delivery, the `user_prompt_submit` and `post_tool_use` hooks that offer a
conversation a trigger on a synced resource the text it reads links to, the `connection_recorded`
hook that gives a connected account its canonical streams as the connection lands — so a member who
connects a provider in chat needs no second act — and the job that retries a creation which did not
land. One extension, N backends —
each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker: the sync
runner routes a source holding a broker connection to the extension that registers its provider
(Composio, Pipedream) and a source holding `DIRECT_ACCOUNT` to the selected fallback — this
`direct` backend, in every deploy that installs no other. `serve` sources the backends into the sync
driver's backend map (so a registered account syncs offline into memory) and builds this sole
`direct` proxy automatically with a reader scoped to these slots."""

from dataclasses import dataclass

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.context import CredentialAccess
from ufo.sdk.jobs import JobSpec, connection_workspaces
from ufo.sdk.manifest import CredentialSlot, HookSpec, Manifest, SourceProvider
from ufo.sdk.sources import Connector, ConnectorBackend
from ufo_ext_sources.connected import on_connection_recorded, retry_connected_sources
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.pages import PAGE_OBJECT
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import (
    SOURCE_OBJECT,
    SOURCE_TRIGGER_OBJECT,
    on_link_seen,
    on_page_change,
)

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
        objects=(SOURCE_OBJECT, SOURCE_TRIGGER_OBJECT, PAGE_OBJECT),
        hooks=(
            HookSpec(event="page_change", handler=on_page_change),
            HookSpec(event="connection_recorded", handler=on_connection_recorded),
            HookSpec(event="user_prompt_submit", handler=on_link_seen, best_effort=True),
            HookSpec(event="post_tool_use", handler=on_link_seen),
        ),
        jobs=(
            JobSpec(
                name=CONNECTED_SOURCES_RETRY_JOB,
                schedule=CONNECTED_SOURCES_RETRY_SCHEDULE,
                handler=retry_connected_sources,
                candidates=connection_workspaces(),
            ),
        ),
        sources=tuple(
            SourceProvider(
                backend=name,
                build=ConnectorSourceFactory(connector=cls),
            )
            for name, cls in CONNECTORS.items()
        ),
        credentials=tuple(
            CredentialSlot(
                name=name,
                description=f"BYOK API key for {name} feed-sync via the direct auth backend.",
            )
            for name in CONNECTORS
        ),
        auth_proxies=(
            AuthProxySpec(
                backend=DIRECT_BACKEND,
                build=lambda credentials: DirectAuthProxy(credentials=credentials),
            ),
        ),
    )
