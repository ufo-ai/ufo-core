"""Public re-export: the browser transport seam an extension implements or the engine connects.

An extension pairs a `CdpProviderSpec` (`selfhost.sdk.manifest`) with a provider name a deploy
selects through `config.browser.cdp_provider`. A `CdpProvider` mints a per-turn `CdpLease` yielding
a `CdpEndpoint` — the CDP URL plus any connection headers — released at turn end (browserbase mints
and releases a fresh hosted session per turn). A lease's `token` is the durable reattach handle an
extension persists, and `CdpProvider.reattach` reconnects to it on a recovered turn or raises
`SessionGone`. `FindCompleter` is the host-side element-ranking hook the browser engine calls back
through. The concrete seam lives in `selfhost.browser`, reached only here."""

from selfhost.browser import (
    CdpEndpoint as CdpEndpoint,
)
from selfhost.browser import (
    CdpLease as CdpLease,
)
from selfhost.browser import (
    CdpProvider as CdpProvider,
)
from selfhost.browser import (
    FindCompleter as FindCompleter,
)
from selfhost.browser import (
    SessionGone as SessionGone,
)
