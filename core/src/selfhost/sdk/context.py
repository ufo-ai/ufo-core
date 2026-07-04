"""Public re-export: the capability-scoped context (and its value types) a handler receives.

Extensions type their job/handler signatures against these; the concrete shapes live in
`selfhost.ext.context`, reached only through this surface."""

from selfhost.ext.context import (
    CredentialAccess as CredentialAccess,
)
from selfhost.ext.context import (
    ExtensionContext as ExtensionContext,
)
from selfhost.ext.context import (
    JsonValue as JsonValue,
)
from selfhost.ext.context import (
    ScopedStore as ScopedStore,
)
from selfhost.ext.context import (
    Trajectory as Trajectory,
)
from selfhost.ext.context import (
    UndeclaredCredentialSlot as UndeclaredCredentialSlot,
)
from selfhost.schema.records import (
    AgentChange as AgentChange,
)
from selfhost.schema.records import (
    ProposalRef as ProposalRef,
)
