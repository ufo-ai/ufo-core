"""What the composio extension declares: an open connector namespace over the shared
`ComposioBroker` plus a small set of explicit connectors — the CLI exceptions whose grant needs a
real provider host (github). The namespace (`ComposioResolver`) serves every other Composio toolkit
by its slug alone: the connect flow resolves its OAuth descriptor, the `ConnectorRegistry` routes
and catalog-searches it, and a brokered grant admits no provider host because tools execute
server-side. An explicit `ConnectorProvider` carries the Composio-backed OAuth descriptor behind
`/connect`, the member-facing label, the shared broker, and the CLI credential; the browser bridge
route the consent leg redirects through serves both. Composio holds each account's token
server-side, so no secret ever reaches this deploy."""

from ufo.sdk.connectors import CliCredential, ConnectorBroker, connect_bridge_workspace
from ufo.sdk.manifest import ConnectorProvider, Manifest, RouteSpec
from ufo_ext_composio.broker import ComposioBroker
from ufo_ext_composio.client import COMPOSIO_TRANSFER_HOSTS, CONNECTORS
from ufo_ext_composio.provider import OAUTH_ROUTE_PATH, ComposioOAuthProvider, oauth_route
from ufo_ext_composio.proxy import ComposioRequestForwarder
from ufo_ext_composio.resolver import ComposioResolver

NAME = "composio"
VERSION = "0.1.0"
CLI_AUTH_HEADER = "authorization"


def manifest() -> Manifest:
    broker: ConnectorBroker = ComposioBroker()
    forwarder = ComposioRequestForwarder()
    return Manifest(
        name=NAME,
        version=VERSION,
        connectors=tuple(
            ConnectorProvider(
                oauth=ComposioOAuthProvider(provider=provider, host=spec.host),
                label=spec.label,
                broker=broker,
                transfer_hosts=COMPOSIO_TRANSFER_HOSTS,
                cli=(
                    CliCredential(env=spec.cli_env, header=CLI_AUTH_HEADER, forward=forwarder)
                    if spec.cli_env is not None
                    else None
                ),
            )
            for provider, spec in CONNECTORS.items()
        ),
        connector_resolver=ComposioResolver(broker=broker),
        routes=(
            RouteSpec(
                method="GET",
                path=OAUTH_ROUTE_PATH,
                handler=oauth_route,
                identify=connect_bridge_workspace,
            ),
        ),
    )
