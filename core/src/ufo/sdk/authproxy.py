"""Public re-export: the auth-proxy seam an extension implements or registers to contribute a
credential backend for feed-sync sources.

An extension pairs an `AuthProxySpec` (`ufo.runtime.ext.manifest`) with a backend name and
implements `AuthProxy` — resolving the `Credential` a connector authenticates a provider with.
A sole backend is automatic; `config.connectors.auth_backend` selects among several. The selected
backend resolves every source hanging off a connection with no account handle — the workspace's own,
where the member set the provider's credential instead of connecting an account — while a source on
a broker connection resolves through that broker's `credential`. The concrete shapes live in
`ufo.runtime.access.connectors`, reached only here."""

from ufo.runtime.access.connectors import (
    AuthProxy as AuthProxy,
)
from ufo.runtime.access.connectors import (
    Credential as Credential,
)
from ufo.runtime.ext.manifest import (
    AuthProxySpec as AuthProxySpec,
)
