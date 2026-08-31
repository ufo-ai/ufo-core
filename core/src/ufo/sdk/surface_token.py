"""Public re-export: a surface mints and verifies its own permanent link addresses here, never
holding the deploy's token secret.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.harness.auth.surface_token import (
    mint_surface_token as mint_surface_token,
)
from ufo.harness.auth.surface_token import (
    verify_surface_token as verify_surface_token,
)
