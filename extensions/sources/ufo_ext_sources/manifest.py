"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, the `direct` auth-proxy backend
itself, the `source` object kind that turns a granted or keyed provider into syncing source rows,
the `page` object kind that projects the synced pages back for read, and the `page_change` hook
that alerts a source's subscribers when its synced content changes. One extension, N backends —
each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker: the sync
runner routes a source holding a broker grant to the broker extension that registers its provider
(Composio, Pipedream) and a source holding `DIRECT_ACCOUNT` to the selected fallback — this
`direct` backend, in every deploy that installs no other. `serve` sources the backends into the sync
driver's backend map (so a registered account syncs offline into memory) and builds this sole
`direct` proxy automatically with a reader scoped to these slots."""

from dataclasses import dataclass

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, HookSpec, Manifest, SourceProvider
from ufo.sdk.sources import Connector, ConnectorBackend
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.pages import PAGE_OBJECT
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import SOURCE_OBJECT, on_page_change

NAME = "sources"
VERSION = "0.1.0"
DIRECT_BACKEND = "direct"


@dataclass(frozen=True)
class ConnectorSourceFactory:
    connector: type[Connector]

    def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend:
        return ConnectorBackend(connector=self.connector())


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(SOURCE_OBJECT, PAGE_OBJECT),
        hooks=(HookSpec(event="page_change", handler=on_page_change),),
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
