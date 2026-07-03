"""What the connectors extension declares: the dynamic Composio tool surface (search a connector's
real tools, describe them, execute one server-side), one Composio-backed OAuth descriptor per
registered provider behind `/connect`, the browser bridge route the consent leg redirects through,
and a Composio-fed content source. The tools are workspace-global — they search across every
connector — so they are declared once as plain tools, not per provider; the OAuth descriptors are
per provider so `/connect` is live for each. `serve` sources the tools + providers into its registry
and the turn's tool set, and the source into the sync driver's backend map (so a registered Asana
account syncs offline into memory)."""

from selfhost.sdk.manifest import ConnectorProvider, Manifest, RouteSpec, SourceProvider
from selfhost_ext_connectors.composio import CONNECTORS
from selfhost_ext_connectors.provider import (
    OAUTH_ROUTE_PATH,
    ComposioOAuthProvider,
    oauth_route,
)
from selfhost_ext_connectors.sources import ASANA_BACKEND, AsanaSource
from selfhost_ext_connectors.tools import CONNECTOR_TOOLS

NAME = "connectors"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=CONNECTOR_TOOLS,
        connectors=tuple(
            ConnectorProvider(
                oauth=ComposioOAuthProvider(
                    provider=provider, host=spec.host, toolkit=spec.toolkit
                ),
            )
            for provider, spec in CONNECTORS.items()
        ),
        routes=(RouteSpec(method="GET", path=OAUTH_ROUTE_PATH, handler=oauth_route),),
        sources=(SourceProvider(backend=ASANA_BACKEND, source=AsanaSource()),),
    )
