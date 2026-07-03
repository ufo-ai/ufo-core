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
    InjectionTarget as InjectionTarget,
)
from selfhost.ext.manifest import (
    Manifest as Manifest,
)
from selfhost.ext.manifest import (
    OnboardingStep as OnboardingStep,
)
from selfhost.ext.manifest import (
    PromptSection as PromptSection,
)
from selfhost.ext.manifest import (
    RouteSpec as RouteSpec,
)
