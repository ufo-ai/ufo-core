"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, and the `direct` auth-proxy backend
itself. One extension, N backends — each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker.
`serve` sources the backends
into the sync driver's backend map (so a registered account syncs offline into memory) and, when a
deploy selects `[connectors] auth_backend = "direct"`, builds the direct proxy with a reader scoped
to these slots. The Composio broker is a separate backend the `connectors` extension registers."""

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.manifest import CredentialSlot, Manifest, SourceProvider
from ufo.sdk.sources import ConnectorBackend
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.registry import CONNECTORS

NAME = "sources"
VERSION = "0.1.0"
DIRECT_BACKEND = "direct"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        sources=tuple(
            SourceProvider(backend=name, source=ConnectorBackend(connector=cls()))
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
