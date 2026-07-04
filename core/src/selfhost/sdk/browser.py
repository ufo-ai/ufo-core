"""Public re-export: the browser seam an extension implements or reuses to contribute a backend.

An extension pairs a `BrowserBackendSpec` (`selfhost.sdk.manifest`) with a backend name a deploy
selects through `config.browser.backend`. A backend yields a per-turn `BrowserSurface` — the browser
tools' eleven methods. Two shapes slot in here: reuse core's `BuaBackend` with a different
`BrowserCdpProvider` — only the CDP endpoint changes (browserbase), built with a
`StaticBrowserCdpProvider`/`BrowserCdpProviderChain` over a resolved `BrowserCdp`; or implement
`BrowserBackend`/`BrowserSurface` outright — the whole surface changes (browser-use). The concrete
shapes live in `selfhost.browser.*`, reached only here."""

from selfhost.browser.backend import (
    BrowserBackend as BrowserBackend,
)
from selfhost.browser.backend import (
    BrowserSurface as BrowserSurface,
)
from selfhost.browser.backend import (
    BuaBackend as BuaBackend,
)
from selfhost.browser.cdp_provider import (
    BrowserCdpProviderChain as BrowserCdpProviderChain,
)
from selfhost.browser.cdp_provider import (
    StaticBrowserCdpProvider as StaticBrowserCdpProvider,
)
from selfhost.browser.find import (
    FindCompleter as FindCompleter,
)
from selfhost.browser.wire import (
    BrowserCdp as BrowserCdp,
)
from selfhost.browser.wire import (
    BrowserCdpProvider as BrowserCdpProvider,
)
