"""Public re-export: the browser transport seam an extension implements or the engine connects.

An extension pairs a `CdpProviderSpec` (`ufo.sdk.manifest`) with a provider name a deploy
selects through `config.browser.cdp_provider`. A `CdpProvider` mints a per-turn `CdpLease` yielding
a `CdpEndpoint` — the CDP URL plus any connection headers — released at turn end (browserbase mints
and releases a fresh hosted session per turn). A lease's `token` is the durable reattach handle an
extension persists, and `CdpProvider.reattach` reconnects to it on a recovered turn or raises
`SessionGone`. `FindCompleter` is the host-side element-ranking hook the browser engine calls back
through. The concrete seam lives in `ufo.browser`, reached only here."""

from ufo.browser import (
    CdpEndpoint as CdpEndpoint,
)
from ufo.browser import (
    CdpLease as CdpLease,
)
from ufo.browser import (
    CdpProvider as CdpProvider,
)
from ufo.browser import (
    FindCompleter as FindCompleter,
)
from ufo.browser import (
    SessionGone as SessionGone,
)
