"""What the connectors extension declares: the dynamic Composio tool surface (search a connector's
real tools, describe them, execute one server-side, and Tool-Router semantic search), one
Composio-backed OAuth descriptor per registered provider behind `/connect`, the browser bridge route
the consent leg redirects through, and the Composio auth-proxy backend feed-sync sources resolve
credentials through. The tools are workspace-global — they search across every connector — so they
are declared once as plain tools, not per provider; the OAuth descriptors are per provider so
`/connect` is live for each. Composio is ONE backend of core's pluggable auth-proxy seam (the
`sources` extension holds the connector framework and the direct/BYOK backend): `serve` sources the
tools + providers into its registry and the turn's tool set, and builds this backend when a deploy
selects `[connectors] auth_backend = "composio"`."""

from pathlib import Path

from selfhost.sdk.authproxy import AuthProxySpec
from selfhost.sdk.manifest import (
    ConnectorProvider,
    Manifest,
    PromptSection,
    RouteSpec,
)
from selfhost_ext_connectors.authproxy import ComposioAuthProxy
from selfhost_ext_connectors.composio import CONNECTORS
from selfhost_ext_connectors.provider import (
    OAUTH_ROUTE_PATH,
    ComposioOAuthProvider,
    oauth_route,
)
from selfhost_ext_connectors.tools import CONNECTOR_TOOLS

NAME = "connectors"
VERSION = "0.1.0"
COMPOSIO_BACKEND = "composio"

SECTION_NAME = "external_tools"
SECTION_BODY = (Path(__file__).parent / "connectors_section.md").read_text().strip()


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
        auth_proxies=(
            AuthProxySpec(backend=COMPOSIO_BACKEND, build=lambda credentials: ComposioAuthProxy()),
        ),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
