"""Public re-export: scoped context and identity available to extension handlers."""

from ufo.runtime.access.credentials import (
    CredentialSlotUnset as CredentialSlotUnset,
)
from ufo.runtime.agent_scope import (
    agent_current as agent_current,
)
from ufo.runtime.ext.context import (
    AgentArchived as AgentArchived,
)
from ufo.runtime.ext.context import (
    ConversationFacts as ConversationFacts,
)
from ufo.runtime.ext.context import (
    CredentialAccess as CredentialAccess,
)
from ufo.runtime.ext.context import (
    ExtensionContext as ExtensionContext,
)
from ufo.runtime.ext.context import (
    JsonValue as JsonValue,
)
from ufo.runtime.ext.context import (
    MemberContextRecord as MemberContextRecord,
)
from ufo.runtime.ext.context import (
    MemberReach as MemberReach,
)
from ufo.runtime.ext.context import (
    ModelAccess as ModelAccess,
)
from ufo.runtime.ext.context import (
    PageRecord as PageRecord,
)
from ufo.runtime.ext.context import (
    PageState as PageState,
)
from ufo.runtime.ext.context import (
    ScopedStore as ScopedStore,
)
from ufo.runtime.ext.context import (
    SourceReader as SourceReader,
)
from ufo.runtime.ext.context import (
    SourceRecord as SourceRecord,
)
from ufo.runtime.ext.context import (
    Trajectory as Trajectory,
)
from ufo.runtime.ext.context import (
    TurnOutcome as TurnOutcome,
)
from ufo.runtime.ext.context import (
    UndeclaredCredentialSlot as UndeclaredCredentialSlot,
)
from ufo.runtime.ext.context import (
    WorkspaceAgent as WorkspaceAgent,
)
from ufo.runtime.ext.context import (
    trajectory_workspaces as trajectory_workspaces,
)
from ufo.runtime.ext.surface import (
    SurfaceInstallationAccess as SurfaceInstallationAccess,
)
from ufo.schema.records import (
    AgentChange as AgentChange,
)
from ufo.schema.records import (
    ProposalRef as ProposalRef,
)
