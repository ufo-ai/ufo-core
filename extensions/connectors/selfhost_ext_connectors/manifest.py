"""What the connectors extension declares: one Composio-backed connector per registered provider —
its OAuth descriptor behind `/connect` and its request tool — plus the browser bridge route the
consent leg redirects through, and a Composio-fed content source. `serve` sources the connectors
into its provider registry and the turn's tool set (turning `/connect` live for every provider) and
the source into the sync driver's backend map (so a registered Asana account syncs offline into
memory)."""

from selfhost.sdk.manifest import ConnectorProvider, Manifest, RouteSpec, SourceProvider
from selfhost_ext_connectors.composio import CONNECTORS
from selfhost_ext_connectors.provider import (
    OAUTH_ROUTE_PATH,
    ComposioOAuthProvider,
    oauth_route,
)
from selfhost_ext_connectors.sources import ASANA_BACKEND, AsanaSource
from selfhost_ext_connectors.tools import connector_request_tool

NAME = "connectors"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        connectors=tuple(
            ConnectorProvider(
                oauth=ComposioOAuthProvider(
                    provider=provider, host=spec.host, toolkit=spec.toolkit
                ),
                tools=(connector_request_tool(provider, spec),),
            )
            for provider, spec in CONNECTORS.items()
        ),
        routes=(RouteSpec(method="GET", path=OAUTH_ROUTE_PATH, handler=oauth_route),),
        sources=(SourceProvider(backend=ASANA_BACKEND, source=AsanaSource()),),
    )
