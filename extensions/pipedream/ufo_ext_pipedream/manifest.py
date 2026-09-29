"""What the pipedream extension declares: one brokered connector per catalog entry — the
Pipedream-backed OAuth descriptor behind `/connect`, the member-facing label, the shared
`PipedreamBroker` serving its actions, server-side execution, and feed-sync credential, and for
GitHub the CLI credential the sandbox rides — plus the browser bridge route the consent leg
redirects through. `serve` folds every connector into the connect registry and the
`ConnectorRegistry` the dynamic connector tools (the `connectors` extension) and the sync runner
route through, beside Composio's providers. This allowlist holds connectors Composio's open
namespace does not serve — one the sandbox needs the real token for (GitHub: clone, push, and `gh`
ride the member's own token, which Pipedream releases for the deploy's own OAuth client), a consent
Composio's shared client cannot pass (Gmail: Google blocks restricted Gmail scopes, so a deploy's
own Google OAuth client rides Pipedream Connect), a toolkit Composio withholds by judgment where
Pipedream's actions cover the gap (Linear), or a provider Composio holds no managed credentials for
while Pipedream operates its own OAuth client (Ramp, Brex, Xero, DocuSign, PandaDoc); a provider no
broker holds managed auth for reaches the agent as a keyed connector instead. Every other account's
token stays with Pipedream."""

from pathlib import Path

from ufo.sdk.connectors import ConnectorBroker, connect_bridge_workspace
from ufo.sdk.manifest import ConnectorProvider, Manifest, PromptSection, RouteSpec
from ufo_ext_pipedream.broker import PipedreamBroker
from ufo_ext_pipedream.client import (
    CONNECTORS,
    PIPEDREAM_CLIENT_ID_ENV,
    PIPEDREAM_CLIENT_SECRET_ENV,
    PIPEDREAM_PROJECT_ID_ENV,
    PIPEDREAM_TRANSFER_HOSTS,
)
from ufo_ext_pipedream.provider import OAUTH_ROUTE_PATH, PipedreamOAuthProvider, oauth_route
from ufo_ext_pipedream.token import cli_credential

NAME = "pipedream"
VERSION = "0.1.0"
SECTION_NAME = "github_cli"
SECTION_BODY = (Path(__file__).parent / "prompts" / "github_cli_section.md").read_text().strip()


def manifest() -> Manifest:
    broker: ConnectorBroker = PipedreamBroker()
    return Manifest(
        name=NAME,
        version=VERSION,
        connectors=tuple(
            ConnectorProvider(
                oauth=PipedreamOAuthProvider(provider=provider, host=spec.host, app=spec.app),
                label=spec.label,
                broker=broker,
                transfer_hosts=PIPEDREAM_TRANSFER_HOSTS,
                cli=cli_credential(spec),
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
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        deploy_keys=(
            PIPEDREAM_CLIENT_ID_ENV,
            PIPEDREAM_CLIENT_SECRET_ENV,
            PIPEDREAM_PROJECT_ID_ENV,
            "PIPEDREAM_ATTIO_OAUTH_APP_ID",
            "PIPEDREAM_LINEAR_OAUTH_APP_ID",
        ),
    )
