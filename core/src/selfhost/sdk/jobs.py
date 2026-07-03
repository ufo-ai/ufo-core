"""Public re-export: extensions declare their background jobs from here, never from core internals.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one."""

from selfhost.ext.manifest import (
    JobSpec as JobSpec,
)
