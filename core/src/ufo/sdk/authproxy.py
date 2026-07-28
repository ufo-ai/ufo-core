"""Public re-export: the auth-proxy seam an extension implements or registers to contribute a
credential backend for feed-sync sources.

An extension pairs an `AuthProxySpec` (`ufo.ext.manifest`) with a backend name and implements
`AuthProxy` — resolving the `Credential` a connector authenticates a provider with. A sole backend
is automatic; `config.connectors.auth_backend` selects among several. The selected backend resolves
every source registered with `DIRECT_ACCOUNT` — the handle a source carries when the member set the
provider's credential instead of connecting an account — while a source holding a broker connection
resolves through that broker's `credential`. The concrete shapes live in `ufo.connectors`, reached
only here."""

from ufo.connectors import (
    DIRECT_ACCOUNT as DIRECT_ACCOUNT,
)
from ufo.connectors import (
    AuthProxy as AuthProxy,
)
from ufo.connectors import (
    Credential as Credential,
)
from ufo.ext.manifest import (
    AuthProxySpec as AuthProxySpec,
)
