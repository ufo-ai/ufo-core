"""The `direct` auth-proxy backend: bring-your-own-key credentials for feed-sync.

A provider no installed broker can grant — or one a deploy would rather hold the key for itself — is
synced from a member-added API key: it lands encrypted in the credential store under the provider's
name, and the workspace's own connection to that provider — the one holding no account handle —
routes every run here. The sync job runs host-side in the jobs role, so it MAY read that key
in-process (exactly as the BYOK model and embed backends read theirs through `context.credentials`):
`credential` reads the slot named for the connector's provider and returns it as a `bearer`. The
secret is decrypted in-process and used only to authenticate the provider HTTP the sync issues — it
NEVER reaches the sandbox or the agent surface, which is the invariant this preserves.
`CredentialAccess` reads only the slots the `sources` manifest declared, and a `Credential` is never
logged.

A provider that authenticates with headers rather than a bearer (Datadog's API key beside its
application key) declares `key_headers` on its connector, and `credential` reads the slot named
beside each header. Those slots are this backend's own and no manifest injects them, which is what
keeps the invariant above true of a two-key provider: the slots an extension swaps onto the sandbox
wire carry different names, so a member filling a feed key grants the agent nothing. A slot with
nothing in it answers `GrantUnusable`, which the sync seam records as a skip naming what to fill —
the run issues no request it already knows the provider will refuse."""

from dataclasses import dataclass
from uuid import UUID

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import GrantUnusable
from ufo.sdk.context import CredentialAccess, CredentialSlotUnset
from ufo_ext_sources.registry import CONNECTORS


@dataclass(frozen=True)
class DirectAuthProxy:
    """Resolves a member-added provider key from the credential store, host-side. The key lives in
    the slot named for the connector's `provider` — one bearer, or the fields its `key_headers`
    names — and the key itself is the auth: there is no account to authenticate as. Reads only
    through the workspace-scoped `CredentialAccess`."""

    credentials: CredentialAccess

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        connector = CONNECTORS.get(provider)
        key_headers = {} if connector is None else connector.key_headers
        if not key_headers:
            return Credential(bearer=await self.credentials.get(provider))
        headers: dict[str, str] = {}
        for header, slot in key_headers.items():
            try:
                headers[header] = await self.credentials.get(slot)
            except CredentialSlotUnset as unset:
                raise GrantUnusable(
                    f"{provider} needs the {slot} credential and nothing is in it"
                ) from unset
        return Credential(headers=headers)
