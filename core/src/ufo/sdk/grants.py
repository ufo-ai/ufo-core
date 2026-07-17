"""Public re-export: the grant audit view the connector object kind reads, never core internals.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.grants import (
    GrantSummary as GrantSummary,
)
from ufo.grants import (
    grant_summaries as grant_summaries,
)
