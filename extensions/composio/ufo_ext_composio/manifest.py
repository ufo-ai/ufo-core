"""What the composio extension declares: an open connector namespace over the shared
`ComposioBroker`. The namespace (`ComposioResolver`) serves every Composio toolkit by its slug
alone: the connect flow resolves its OAuth descriptor, the `ConnectorRegistry` routes and
catalog-searches it, and a brokered grant admits no provider host because tools execute
server-side. The browser bridge route the consent leg redirects through serves it. Composio holds
each account's token server-side, so no secret ever reaches this deploy."""

from ufo.sdk.connectors import ConnectorBroker, connect_bridge_workspace
from ufo.sdk.manifest import Manifest, RouteSpec
from ufo_ext_composio.broker import ComposioBroker
from ufo_ext_composio.provider import OAUTH_ROUTE_PATH, oauth_route
from ufo_ext_composio.resolver import ComposioResolver

NAME = "composio"
VERSION = "0.1.0"


def manifest() -> Manifest:
    broker: ConnectorBroker = ComposioBroker()
    return Manifest(
        name=NAME,
        version=VERSION,
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
