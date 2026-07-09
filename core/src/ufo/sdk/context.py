"""Public re-export: the capability-scoped context (and its value types) a handler receives.

Extensions type their job/handler signatures against these; the concrete shapes live in
`ufo.ext.context`, reached only through this surface."""

from ufo.ext.context import (
    CredentialAccess as CredentialAccess,
)
from ufo.ext.context import (
    ExtensionContext as ExtensionContext,
)
from ufo.ext.context import (
    JsonValue as JsonValue,
)
from ufo.ext.context import (
    ModelAccess as ModelAccess,
)
from ufo.ext.context import (
    ScopedStore as ScopedStore,
)
from ufo.ext.context import (
    Trajectory as Trajectory,
)
from ufo.ext.context import (
    UndeclaredCredentialSlot as UndeclaredCredentialSlot,
)
from ufo.ext.context import (
    trajectory_workspaces as trajectory_workspaces,
)
from ufo.schema.records import (
    AgentChange as AgentChange,
)
from ufo.schema.records import (
    ProposalRef as ProposalRef,
)
