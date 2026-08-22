"""Public re-export: scoped context and identity available to extension handlers."""

from ufo.access.credentials import (
    CredentialSlotUnset as CredentialSlotUnset,
)
from ufo.agent_scope import (
    agent_current as agent_current,
)
from ufo.ext.context import (
    ConversationFacts as ConversationFacts,
)
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
    MemberContextRecord as MemberContextRecord,
)
from ufo.ext.context import (
    ModelAccess as ModelAccess,
)
from ufo.ext.context import (
    PageRecord as PageRecord,
)
from ufo.ext.context import (
    PageState as PageState,
)
from ufo.ext.context import (
    ScopedStore as ScopedStore,
)
from ufo.ext.context import (
    SourceReader as SourceReader,
)
from ufo.ext.context import (
    SourceRecord as SourceRecord,
)
from ufo.ext.context import (
    Trajectory as Trajectory,
)
from ufo.ext.context import (
    TurnOutcome as TurnOutcome,
)
from ufo.ext.context import (
    UndeclaredCredentialSlot as UndeclaredCredentialSlot,
)
from ufo.ext.context import (
    trajectory_workspaces as trajectory_workspaces,
)
from ufo.ext.surface import (
    SurfaceInstallationAccess as SurfaceInstallationAccess,
)
from ufo.schema.records import (
    AgentChange as AgentChange,
)
from ufo.schema.records import (
    ProposalRef as ProposalRef,
)
