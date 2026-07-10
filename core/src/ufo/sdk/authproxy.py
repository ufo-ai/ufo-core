"""Public re-export: the auth-proxy seam an extension implements or registers to contribute a
credential backend for feed-sync sources.

An extension pairs an `AuthProxySpec` (`ufo.ext.manifest`) with a backend name a deploy selects
through `config.connectors.auth_backend`, and implements `AuthProxy` — resolving the `Credential` a
connector authenticates a provider with. The selected backend is the fallback for providers no
installed broker claims; a brokered provider resolves through its own broker's `credential`. The
concrete shapes live in `ufo.connectors`, reached only here."""

from ufo.connectors import (
    AuthProxy as AuthProxy,
)
from ufo.connectors import (
    Credential as Credential,
)
from ufo.ext.manifest import (
    AuthProxySpec as AuthProxySpec,
)
