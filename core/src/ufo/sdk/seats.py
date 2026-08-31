"""Public re-export: seat state, the validated writes over it, and the candidates builder a
seat-reporting job declares — the rules stay core's, the extension decides when to apply them.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one."""

from ufo.runtime.seats import (
    SeatEntry as SeatEntry,
)
from ufo.runtime.seats import (
    Seats as Seats,
)
from ufo.runtime.seats import (
    member_by_email as member_by_email,
)
from ufo.runtime.seats import (
    member_is_admin as member_is_admin,
)
from ufo.runtime.seats import (
    member_workspaces as member_workspaces,
)
from ufo.runtime.seats import (
    workspace_domain as workspace_domain,
)
