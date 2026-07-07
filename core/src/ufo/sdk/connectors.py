"""Public re-export: the OAuth descriptor an extension implements to register a connector.

An extension bundles an `OAuthProvider` (its authorize/exchange handoff) with its connector tools in
a `ConnectorProvider` Manifest point; core drives the handoff behind `/connect` and never knows the
provider's mechanics. The concrete shapes live in `ufo.grants`, reached only through here."""

from ufo.grants import (
    OAuthAccount as OAuthAccount,
)
from ufo.grants import (
    OAuthProvider as OAuthProvider,
)
