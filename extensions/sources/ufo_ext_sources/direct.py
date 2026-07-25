"""The `direct` auth-proxy backend: bring-your-own-key credentials for feed-sync.

A provider no installed broker can grant — or one a deploy would rather hold the key for itself — is
synced from a member-added API key: it lands encrypted in the credential store under the provider's
name, and the source registers against `DIRECT_ACCOUNT`, the handle routing its every run here. The
sync job runs host-side in the jobs role, so it MAY read that key in-process (exactly as the BYOK
model and embed backends read theirs through `context.credentials`): `credential` reads the slot
named for the connector's provider and returns it as a `bearer`. The secret is decrypted in-process
and used only to authenticate the provider HTTP the sync issues — it NEVER reaches the sandbox or
the agent surface, which is the invariant this preserves. `CredentialAccess` reads only the slots
the `sources` manifest declared, and a `Credential` is never logged."""

from dataclasses import dataclass
from uuid import UUID

from ufo.sdk.authproxy import Credential
from ufo.sdk.context import CredentialAccess


@dataclass(frozen=True)
class DirectAuthProxy:
    """Resolves a member-added provider key from the credential store, host-side. The key lives in
    the slot named for the connector's `provider`; the `account` handle is `DIRECT_ACCOUNT` — it
    routed the source here and carries nothing further, since the key itself is the auth. Reads only
    through the workspace-scoped `CredentialAccess`."""

    credentials: CredentialAccess

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer=await self.credentials.get(provider))
