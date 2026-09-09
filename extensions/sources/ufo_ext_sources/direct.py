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
application key) declares `key_headers` on its connector, and that one slot holds a field per
secret: `keyed_secret_merge` validates and merges each private submission, so the member fills one
field per handoff, and `credential` reads the fields back into headers. A slot still missing a field
answers `GrantUnusable`, which the sync seam records as a skip naming what to fill — the run issues
no request it already knows the provider will refuse."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import GrantUnusable
from ufo.sdk.context import CredentialAccess
from ufo.sdk.credentials import CredentialValueInvalid
from ufo_ext_sources.registry import CONNECTORS


def keyed_secret_merge(fields: tuple[str, ...]) -> Callable[[str | None, str], str]:
    """The value merge for a slot holding several secrets: one private submission is a JSON object
    naming any of `fields`, merged over what the slot already holds."""
    expected = ", ".join(fields)

    def merge(current: str | None, submitted: str) -> str:
        try:
            incoming = json.loads(submitted)
        except ValueError as error:
            raise CredentialValueInvalid(f"expected a JSON object naming {expected}") from error
        if not isinstance(incoming, dict) or not incoming:
            raise CredentialValueInvalid(f"expected a JSON object naming {expected}")
        unknown = sorted(set(incoming) - set(fields))
        if unknown:
            raise CredentialValueInvalid(
                f"{', '.join(unknown)} is not a secret this provider takes; expected {expected}"
            )
        if any(not isinstance(value, str) or not value for value in incoming.values()):
            raise CredentialValueInvalid(f"every value of {expected} is a non-empty string")
        held = {} if current is None else json.loads(current)
        return json.dumps({**held, **incoming}, sort_keys=True)

    return merge


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
        held = json.loads(await self.credentials.get(provider))
        missing = sorted(field for field in key_headers.values() if not held.get(field))
        if missing:
            raise GrantUnusable(
                f"the {provider} credential is missing {', '.join(missing)}; fill the slot with "
                "every secret the provider demands"
            )
        return Credential(headers={header: held[field] for header, field in key_headers.items()})
