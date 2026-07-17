"""Public re-export: seat state, the validated writes over it, and the candidates builder a
seat-reporting job declares — the rules stay core's, the extension decides when to apply them.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one."""

from ufo.seats import (
    OwnerSeatRevocation as OwnerSeatRevocation,
)
from ufo.seats import (
    SeatEntry as SeatEntry,
)
from ufo.seats import (
    SeatLimitReached as SeatLimitReached,
)
from ufo.seats import (
    Seats as Seats,
)
from ufo.seats import (
    SeatSnapshot as SeatSnapshot,
)
from ufo.seats import (
    UnknownMember as UnknownMember,
)
from ufo.seats import (
    member_workspaces as member_workspaces,
)
from ufo.seats import (
    owner_conversation as owner_conversation,
)
