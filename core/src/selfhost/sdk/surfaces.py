"""Public re-export: a surface extension registers a `SurfaceSpec` and types its handlers against
the privileged `SurfaceContext` and the `Writeback` (with its `SharedArtifact`s) it delivers.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from selfhost.ext.surface import (
    SharedArtifact as SharedArtifact,
)
from selfhost.ext.surface import (
    SurfaceContext as SurfaceContext,
)
from selfhost.ext.surface import (
    SurfaceSpec as SurfaceSpec,
)
from selfhost.ext.surface import (
    Writeback as Writeback,
)
