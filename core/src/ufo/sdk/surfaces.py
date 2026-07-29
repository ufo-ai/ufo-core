"""Public re-export: a surface extension registers a `SurfaceSpec` (its `SurfaceRoute`s and, for a
durable surface, its two-phase writeback) and types its handlers against the privileged
`SurfaceContext` and the `Writeback` (with its `SharedArtifact`s) it delivers.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.credentials import (
    CredentialRequestInvalid as CredentialRequestInvalid,
)
from ufo.credentials import (
    CredentialRequestState as CredentialRequestState,
)
from ufo.credentials import (
    CredentialSlotUnset as CredentialSlotUnset,
)
from ufo.ext.surface import (
    OPERATOR_EMAIL_DOMAIN as OPERATOR_EMAIL_DOMAIN,
)
from ufo.ext.surface import (
    Admitted as Admitted,
)
from ufo.ext.surface import (
    AgentSummary as AgentSummary,
)
from ufo.ext.surface import (
    ConversationSummary as ConversationSummary,
)
from ufo.ext.surface import (
    LedgerEntry as LedgerEntry,
)
from ufo.ext.surface import (
    SharedArtifact as SharedArtifact,
)
from ufo.ext.surface import (
    SurfaceAuth as SurfaceAuth,
)
from ufo.ext.surface import (
    SurfaceContext as SurfaceContext,
)
from ufo.ext.surface import (
    SurfaceDeliveryError as SurfaceDeliveryError,
)
from ufo.ext.surface import (
    SurfaceIdentityContext as SurfaceIdentityContext,
)
from ufo.ext.surface import (
    SurfaceInstallationConflict as SurfaceInstallationConflict,
)
from ufo.ext.surface import (
    SurfaceRoute as SurfaceRoute,
)
from ufo.ext.surface import (
    SurfaceSpec as SurfaceSpec,
)
from ufo.ext.surface import (
    SurfaceWorkspaceUnknown as SurfaceWorkspaceUnknown,
)
from ufo.ext.surface import (
    TurnDetail as TurnDetail,
)
from ufo.ext.surface import (
    WorkspaceFile as WorkspaceFile,
)
from ufo.ext.surface import (
    Writeback as Writeback,
)
from ufo.grants import (
    ConnectRequestInvalid as ConnectRequestInvalid,
)
from ufo.schema.records import (
    AskQuestion as AskQuestion,
)
from ufo.schema.records import (
    AskUserInput as AskUserInput,
)
from ufo.schema.records import (
    ConnectRequest as ConnectRequest,
)
from ufo.schema.records import (
    CredentialPrompt as CredentialPrompt,
)
from ufo.schema.records import (
    CredentialRequest as CredentialRequest,
)
from ufo.schema.records import (
    QuestionOption as QuestionOption,
)
from ufo.schema.records import (
    TerminalFrame as TerminalFrame,
)
from ufo.schema.records import (
    Turn as Turn,
)
from ufo.schema.records import (
    TurnContext as TurnContext,
)
from ufo.transcript import (
    CompactionRecord as CompactionRecord,
)
from ufo.transcript import (
    CompactionSummary as CompactionSummary,
)
from ufo.transcript import (
    Conversation as Conversation,
)
from ufo.transcript import (
    TranscriptDecodeError as TranscriptDecodeError,
)
