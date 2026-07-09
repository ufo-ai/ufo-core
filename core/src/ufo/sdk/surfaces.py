"""Public re-export: a surface extension registers a `SurfaceSpec` (its `SurfaceRoute`s and, for a
durable surface, its two-phase writeback) and types its handlers against the privileged
`SurfaceContext` and the `Writeback` (with its `SharedArtifact`s) it delivers.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.credentials import (
    CredentialSlotUnset as CredentialSlotUnset,
)
from ufo.ext.surface import (
    SharedArtifact as SharedArtifact,
)
from ufo.ext.surface import (
    SurfaceContext as SurfaceContext,
)
from ufo.ext.surface import (
    SurfaceRoute as SurfaceRoute,
)
from ufo.ext.surface import (
    SurfaceSpec as SurfaceSpec,
)
from ufo.ext.surface import (
    Writeback as Writeback,
)
from ufo.schema.records import (
    AskQuestion as AskQuestion,
)
from ufo.schema.records import (
    AskUserInput as AskUserInput,
)
from ufo.schema.records import (
    QuestionOption as QuestionOption,
)
