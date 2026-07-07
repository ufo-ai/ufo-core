"""The Composio auth-proxy backend: one backend of core's pluggable auth-proxy seam.

Composio brokers the provider credential and never exposes it, so a feed-sync source cannot hold a
token and call the provider directly. `ComposioAuthProxy.credential` derives this workspace's broker
user, confirms the account is active and owned by it (`connected_account` reads the account's
metadata, NOT its token — the confused-deputy guard, so a foreign account id is refused before a
page is fetched), and returns a `Credential` carrying a `ComposioProxyTransport`. That transport
rewrites each provider request through Composio's proxy-execute, which injects the credential
server-side — the token stays at Composio and the grant stores only the connected-account id, never
a secret. Registered through the connectors extension's `auth_proxies` Manifest point under the
`composio` backend name; a deploy selects it with `[connectors] auth_backend`."""

from dataclasses import dataclass
from uuid import UUID

import httpx

from ufo.sdk.authproxy import Credential
from ufo_ext_connectors import composio
from ufo_ext_connectors.composio_proxy import ComposioProxyTransport


@dataclass(frozen=True)
class ComposioAuthProxy:
    """Resolves a connector credential as a Composio-proxying transport, the account's ownership
    confirmed from metadata first. Holds no state — the broker client and its key are read per
    call, so a test's transport override is honoured and no connection leaks."""

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
        client = composio.composio_client()
        await client.connected_account(account, broker_user)
        return Credential(
            transport=ComposioProxyTransport(
                api_base=composio.COMPOSIO_API_BASE,
                api_key=client.api_key,
                connected_account_id=account,
                inner=client.transport or httpx.AsyncHTTPTransport(),
            )
        )
