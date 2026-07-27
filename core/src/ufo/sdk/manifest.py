"""Public re-export: extensions import their Manifest types from here, never from core internals.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.credentials import (
    CredentialSource as CredentialSource,
)
from ufo.credentials import (
    HostChoice as HostChoice,
)
from ufo.ext.manifest import (
    CdpProviderSpec as CdpProviderSpec,
)
from ufo.ext.manifest import (
    ConnectorProvider as ConnectorProvider,
)
from ufo.ext.manifest import (
    CredentialSlot as CredentialSlot,
)
from ufo.ext.manifest import (
    Deny as Deny,
)
from ufo.ext.manifest import (
    EmbedBackendSpec as EmbedBackendSpec,
)
from ufo.ext.manifest import (
    HookContext as HookContext,
)
from ufo.ext.manifest import (
    HookEvent as HookEvent,
)
from ufo.ext.manifest import (
    HookOutcome as HookOutcome,
)
from ufo.ext.manifest import (
    HookPayload as HookPayload,
)
from ufo.ext.manifest import (
    HookSpec as HookSpec,
)
from ufo.ext.manifest import (
    HubSpec as HubSpec,
)
from ufo.ext.manifest import (
    IndexBackendSpec as IndexBackendSpec,
)
from ufo.ext.manifest import (
    InjectContext as InjectContext,
)
from ufo.ext.manifest import (
    InjectionTarget as InjectionTarget,
)
from ufo.ext.manifest import (
    Manifest as Manifest,
)
from ufo.ext.manifest import (
    MemorySearchProviderSpec as MemorySearchProviderSpec,
)
from ufo.ext.manifest import (
    ModifyInput as ModifyInput,
)
from ufo.ext.manifest import (
    ModifyOutput as ModifyOutput,
)
from ufo.ext.manifest import (
    OnboardingStep as OnboardingStep,
)
from ufo.ext.manifest import (
    OpenConnectorNamespace as OpenConnectorNamespace,
)
from ufo.ext.manifest import (
    Pack as Pack,
)
from ufo.ext.manifest import (
    PageChangeBatch as PageChangeBatch,
)
from ufo.ext.manifest import (
    PostCompact as PostCompact,
)
from ufo.ext.manifest import (
    PostToolUse as PostToolUse,
)
from ufo.ext.manifest import (
    PostToolUseFailure as PostToolUseFailure,
)
from ufo.ext.manifest import (
    PreCompact as PreCompact,
)
from ufo.ext.manifest import (
    PromptSection as PromptSection,
)
from ufo.ext.manifest import (
    RouteSpec as RouteSpec,
)
from ufo.ext.manifest import (
    SearchProviderSpec as SearchProviderSpec,
)
from ufo.ext.manifest import (
    SkillSpec as SkillSpec,
)
from ufo.ext.manifest import (
    SourceProvider as SourceProvider,
)
from ufo.ext.manifest import (
    Stop as Stop,
)
from ufo.ext.manifest import (
    SubagentProfile as SubagentProfile,
)
from ufo.ext.manifest import (
    SubagentToolGrant as SubagentToolGrant,
)
from ufo.ext.manifest import (
    UserPromptSubmit as UserPromptSubmit,
)
