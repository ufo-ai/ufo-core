"""What the pipedream extension declares: one brokered connector per catalog entry — the
Pipedream-backed OAuth descriptor behind `/connect`, the member-facing label, and the shared
`PipedreamBroker` serving its actions, server-side execution, and feed-sync credential — plus the
browser bridge route the consent leg redirects through. `serve` folds every connector into the
connect registry and the `ConnectorRegistry` the dynamic connector tools (the `connectors`
extension) and the sync runner route through, beside Composio's providers. This allowlist holds
connectors Composio's open namespace cannot broker because its shared client cannot pass their
consent (Gmail: Google blocks restricted Gmail scopes, so a deploy's own Google OAuth client rides
Pipedream Connect); a provider no broker holds managed auth for reaches the agent as a keyed
connector instead. Pipedream holds each account's token server-side, so no secret ever reaches this
deploy."""

from ufo.sdk.connectors import ConnectorBroker, connect_bridge_workspace
from ufo.sdk.manifest import ConnectorProvider, Manifest, RouteSpec
from ufo_ext_pipedream.broker import PipedreamBroker
from ufo_ext_pipedream.client import CONNECTORS, PIPEDREAM_TRANSFER_HOSTS
from ufo_ext_pipedream.provider import OAUTH_ROUTE_PATH, PipedreamOAuthProvider, oauth_route

NAME = "pipedream"
VERSION = "0.1.0"


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
