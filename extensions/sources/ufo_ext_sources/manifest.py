"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, the `direct` auth-proxy backend
itself, and the `sync_source` chat tool that turns a granted or keyed provider into syncing
source rows. One extension, N backends — each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker: the sync
runner routes a brokered provider's credential to the broker extension that registers it (Composio,
Pipedream) and every other to the selected fallback. `serve` sources the backends into the sync
driver's backend map (so a registered account syncs offline into memory) and builds this sole
`direct` proxy automatically with a reader scoped to these slots."""

from dataclasses import dataclass

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, Manifest, SourceProvider
from ufo.sdk.sources import Connector, ConnectorBackend
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.tools import SYNC_SOURCE_TOOL

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
        tools=(SYNC_SOURCE_TOOL,),
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
