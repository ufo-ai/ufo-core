"""The open connector namespace: how Composio brokers every toolkit without registering each.

Composio brokers hundreds of toolkits, so the deploy names none of them — a member connects any by
its slug and the dynamic connector tools discover and execute its tools. `claims` confirms the slug
against Composio's live catalog so a typo fails loud at connect time; `descriptor` builds the pure
OAuth descriptor a validated slug connects through (no provider host — the account's token stays
with Composio and tools execute server-side); `entry` routes the slug to the
one shared `ComposioBroker`; `catalog` searches Composio's toolkit catalog so the discovery tool
surfaces connectable services the closed registry never enumerated. `transfer_hosts` are Composio's
file-store hosts every brokered grant admits, so a tool's file inputs and outputs still cross
through the sandbox. The connect flow and registry gate the explicitly registered connectors (the
CLI exceptions) first, so this namespace serves only the slugs no `ConnectorProvider` claimed."""

from dataclasses import dataclass

from ufo.sdk.connectors import CatalogEntry, ConnectorBroker, ConnectorEntry, OAuthProvider
from ufo_ext_composio import client as composio
from ufo_ext_composio.client import COMPOSIO_TRANSFER_HOSTS, TOOL_SEARCH_LIMIT
from ufo_ext_composio.provider import ComposioOAuthProvider


@dataclass(frozen=True)
class ComposioResolver:
    """Composio's open namespace over one shared broker. Stateless beyond the broker — the client
    and its key are read per call, so a test's transport override is honoured and no connection
    leaks."""

    broker: ConnectorBroker

    @property
    def transfer_hosts(self) -> tuple[str, ...]:
        return COMPOSIO_TRANSFER_HOSTS

    async def claims(self, provider: str) -> bool:
        return await composio.composio_client().toolkit_label(provider) is not None

    def descriptor(self, provider: str) -> OAuthProvider:
        return ComposioOAuthProvider(provider=provider, host="")

    def entry(self, provider: str) -> ConnectorEntry:
        return ConnectorEntry(
            provider=provider, label=provider.replace("_", " ").title(), broker=self.broker
        )

    async def catalog(self, query: str, limit: int = TOOL_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]:
        rows = await composio.composio_client().list_toolkits(query, limit)
        return tuple(CatalogEntry(provider=slug, label=label) for slug, label in rows)
