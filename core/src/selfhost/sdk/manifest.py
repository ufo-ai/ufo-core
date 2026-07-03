"""Public re-export: extensions import their Manifest types from here, never from core internals.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from selfhost.ext.manifest import (
    ConnectorProvider as ConnectorProvider,
)
from selfhost.ext.manifest import (
    CredentialSlot as CredentialSlot,
)
from selfhost.ext.manifest import (
    Deny as Deny,
)
from selfhost.ext.manifest import (
    HookContext as HookContext,
)
from selfhost.ext.manifest import (
    HookEvent as HookEvent,
)
from selfhost.ext.manifest import (
    HookOutcome as HookOutcome,
)
from selfhost.ext.manifest import (
    HookPayload as HookPayload,
)
from selfhost.ext.manifest import (
    HookSpec as HookSpec,
)
from selfhost.ext.manifest import (
    InjectContext as InjectContext,
)
from selfhost.ext.manifest import (
    InjectionTarget as InjectionTarget,
)
from selfhost.ext.manifest import (
    Manifest as Manifest,
)
from selfhost.ext.manifest import (
    ModifyInput as ModifyInput,
)
from selfhost.ext.manifest import (
    ModifyOutput as ModifyOutput,
)
from selfhost.ext.manifest import (
    OnboardingStep as OnboardingStep,
)
from selfhost.ext.manifest import (
    OnInbound as OnInbound,
)
from selfhost.ext.manifest import (
    PostToolUse as PostToolUse,
)
from selfhost.ext.manifest import (
    PreToolUse as PreToolUse,
)
from selfhost.ext.manifest import (
    PromptSection as PromptSection,
)
from selfhost.ext.manifest import (
    RouteSpec as RouteSpec,
)
from selfhost.ext.manifest import (
    SubagentProfile as SubagentProfile,
)
