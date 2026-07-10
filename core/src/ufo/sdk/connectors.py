"""Public re-export: the connector seam an extension implements to register a brokered provider.

An extension bundles an `OAuthProvider` (its authorize/exchange handoff) and a `ConnectorBroker`
(its catalog, server-side execution, and feed-sync credential) in a `ConnectorProvider` Manifest
point; core drives the handoff behind `/connect`, threads the merged `ConnectorRegistry` onto the
turn's ToolContext for the dynamic connector tools, and routes feed-sync credentials through it —
never knowing any broker's mechanics. The concrete shapes live in `ufo.grants` and
`ufo.connectors`, reached only through here."""

from ufo.connectors import (
    BrokerSearch as BrokerSearch,
)
from ufo.connectors import (
    BrokerTool as BrokerTool,
)
from ufo.connectors import (
    ConnectorBroker as ConnectorBroker,
)
from ufo.connectors import (
    ConnectorEntry as ConnectorEntry,
)
from ufo.connectors import (
    ConnectorRegistry as ConnectorRegistry,
)
from ufo.connectors import (
    UnknownBrokerTool as UnknownBrokerTool,
)
from ufo.grants import (
    OAuthAccount as OAuthAccount,
)
from ufo.grants import (
    OAuthProvider as OAuthProvider,
)
