"""What the connectors extension declares: the dynamic Composio tool surface (search a connector's
real tools, describe them, execute one server-side, and Tool-Router semantic search), one
Composio-backed OAuth descriptor per registered provider behind `/connect`, the browser bridge route
the consent leg redirects through, and the Composio-fed content sources. The tools are
workspace-global — they search across every connector — so they are declared once as plain tools,
not per provider; the OAuth descriptors are per provider so `/connect` is live for each. `serve`
sources the tools + providers into its registry and the turn's tool set, and the source backends
into the sync driver's backend map (so a registered account syncs offline into memory): the
hand-written Asana source plus one framework backend per registered connector (`github`)."""

from pathlib import Path

from selfhost.sdk.manifest import (
    ConnectorProvider,
    Manifest,
    PromptSection,
    RouteSpec,
    SourceProvider,
)
from selfhost_ext_connectors.backend import ConnectorBackend
from selfhost_ext_connectors.composio import CONNECTORS
from selfhost_ext_connectors.provider import (
    OAUTH_ROUTE_PATH,
    ComposioOAuthProvider,
    oauth_route,
)
from selfhost_ext_connectors.registry import CONNECTORS as SOURCE_CONNECTORS
from selfhost_ext_connectors.sources import ASANA_BACKEND, AsanaSource
from selfhost_ext_connectors.tools import CONNECTOR_TOOLS

NAME = "connectors"
VERSION = "0.1.0"

SECTION_NAME = "external_tools"
SECTION_BODY = (Path(__file__).parent / "connectors_section.md").read_text().strip()


def manifest() -> Manifest:
    sources = (
        SourceProvider(backend=ASANA_BACKEND, source=AsanaSource()),
        *(
            SourceProvider(backend=name, source=ConnectorBackend(connector=cls()))
            for name, cls in SOURCE_CONNECTORS.items()
        ),
    )
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
        sources=sources,
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
