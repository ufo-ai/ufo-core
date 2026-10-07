"""Public re-export: scoped context and identity available to extension handlers, and the context
boundary an extension implements to replace the window its own way.

A boundary extension builds its strategy from the `BoundaryInputs` the loop hands it, so the types
those inputs carry — the window arithmetic, the workspace-scoped blob store its record persists to,
and the turn and agent its hooks fire with — are public here beside the seam itself."""

from ufo.blob import (
    WorkspaceBlobStore as WorkspaceBlobStore,
)
from ufo.harness.context import (
    CompactionHarness as CompactionHarness,
)
from ufo.harness.context import (
    ContextRemaining as ContextRemaining,
)
from ufo.harness.context import (
    ContextWindow as ContextWindow,
)
from ufo.runtime.access.credentials import (
    CredentialSlotUnset as CredentialSlotUnset,
)
from ufo.runtime.agent_scope import (
    agent_current as agent_current,
)
from ufo.runtime.context_boundary import (
    CONTEXT_ROLLOVER_FLAG as CONTEXT_ROLLOVER_FLAG,
)
from ufo.runtime.context_boundary import (
    BoundaryInputs as BoundaryInputs,
)
from ufo.runtime.context_boundary import (
    BoundaryOutcome as BoundaryOutcome,
)
from ufo.runtime.context_boundary import (
    ContextBoundary as ContextBoundary,
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
    DeployCredentials as DeployCredentials,
)
from ufo.runtime.ext.context import (
    ExtensionContext as ExtensionContext,
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
from ufo.runtime.ext.json_value import (
    JsonValue as JsonValue,
)
from ufo.runtime.ext.source_reader import (
    SourceReader as SourceReader,
)
from ufo.runtime.ext.surface import (
    SurfaceInstallationAccess as SurfaceInstallationAccess,
)
from ufo.runtime.workspace import (
    WorkspaceUnbound as WorkspaceUnbound,
)
from ufo.schema.records import (
    SUBAGENT_SURFACE as SUBAGENT_SURFACE,
)
from ufo.schema.records import (
    Agent as Agent,
)
from ufo.schema.records import (
    AgentChange as AgentChange,
)
from ufo.schema.records import (
    FiredBy as FiredBy,
)
from ufo.schema.records import (
    ProposalRef as ProposalRef,
)
from ufo.schema.records import (
    Turn as Turn,
)
from ufo.schema.records import (
    TurnRuntimeConfig as TurnRuntimeConfig,
)
