"""What the composio extension declares: one brokered connector per catalog entry — the
Composio-backed OAuth descriptor behind `/connect`, the member-facing label, and the shared
`ComposioBroker` serving its catalog, server-side execution, and feed-sync credential — plus the
browser bridge route the consent leg redirects through. `serve` folds every connector into the
connect registry and the `ConnectorRegistry` the dynamic connector tools (the `connectors`
extension) and the sync runner route through; Composio holds each account's token server-side, so
no secret ever reaches this deploy."""

from ufo.sdk.connectors import connect_bridge_workspace
from ufo.sdk.manifest import ConnectorProvider, Manifest, RouteSpec
from ufo_ext_composio.broker import ComposioBroker
from ufo_ext_composio.client import COMPOSIO_TRANSFER_HOSTS, CONNECTORS
from ufo_ext_composio.provider import OAUTH_ROUTE_PATH, ComposioOAuthProvider, oauth_route

NAME = "composio"
VERSION = "0.1.0"


def manifest() -> Manifest:
    broker = ComposioBroker()
    return Manifest(
        name=NAME,
        version=VERSION,
        connectors=tuple(
            ConnectorProvider(
                oauth=ComposioOAuthProvider(
                    provider=provider, host=spec.host, toolkit=spec.toolkit
                ),
                label=spec.label,
                broker=broker,
                transfer_hosts=COMPOSIO_TRANSFER_HOSTS,
            )
            for provider, spec in CONNECTORS.items()
        ),
        routes=(
            RouteSpec(
                method="GET",
                path=OAUTH_ROUTE_PATH,
                handler=oauth_route,
                identify=connect_bridge_workspace,
            ),
        ),
    )
