"""Public re-export: the auth-proxy seam an extension implements or registers to contribute a
credential backend for feed-sync sources.

An extension pairs an `AuthProxySpec` (`selfhost.ext.manifest`) with a backend name a deploy selects
through `config.connectors.auth_backend`, and implements `AuthProxy` — resolving the `Credential` a
connector authenticates a provider with. The concrete shapes live in `selfhost.connectors`, reached
only here."""

from selfhost.connectors import (
    AuthProxy as AuthProxy,
)
from selfhost.connectors import (
    Credential as Credential,
)
from selfhost.ext.manifest import (
    AuthProxySpec as AuthProxySpec,
)
