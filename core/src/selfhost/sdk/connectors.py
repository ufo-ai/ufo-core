"""Public re-export: the OAuth descriptor an extension implements to register a connector.

An extension bundles an `OAuthProvider` (its authorize/exchange handoff) with its connector tools in
a `ConnectorProvider` Manifest point; core drives the handoff behind `/connect` and never knows the
provider's mechanics. The concrete shapes live in `selfhost.grants`, reached only through here."""

from selfhost.grants import (
    OAuthAccount as OAuthAccount,
)
from selfhost.grants import (
    OAuthProvider as OAuthProvider,
)
