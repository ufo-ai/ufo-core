"""What the sources extension declares: one content-source backend per registered connector, the
credential slots the direct auth backend reads BYOK keys from, the `direct` auth-proxy backend
itself, and the `sync_source` chat tool that turns a granted or keyed provider into syncing source
rows. One extension, N backends — each provider builds on the REST connector framework from
`ufo.sdk.sources`, consuming the pluggable auth-proxy seam rather than importing a broker: the sync
runner routes a brokered provider's credential to the broker extension that registers it (Composio,
Pipedream) and every other to the deploy-selected fallback. `serve` sources the backends into the
sync driver's backend map (so a registered account syncs offline into memory) and, when a deploy
selects `[connectors] auth_backend = "direct"`, builds the direct proxy with a reader scoped to
these slots."""

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.manifest import CredentialSlot, Manifest, SourceProvider
from ufo.sdk.sources import ConnectorBackend
from ufo.sdk.tools import ToolDef
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.syncing import SyncSourceInput, sync_source

NAME = "sources"
VERSION = "0.1.0"
DIRECT_BACKEND = "direct"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="sync_source",
                description=(
                    "Start syncing a provider's content into shared memory: registers the "
                    "provider's canonical streams (or one named stream) as workspace sources the "
                    "sync driver polls. Auth is checked before anything registers — a brokered "
                    "provider needs the agent's connector grant, any other a BYOK credential "
                    "slot. Idempotent — re-syncing an already-synced provider changes nothing."
                ),
                input_model=SyncSourceInput,
                handler=sync_source,
            ),
        ),
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
