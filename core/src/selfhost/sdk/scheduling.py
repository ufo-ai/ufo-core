"""Public re-export: the schedule store an extension reaches through its ExtensionContext, and the
scheduled-task value object its methods return.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from selfhost.scheduling import (
    ScheduledTask as ScheduledTask,
)
from selfhost.scheduling import (
    ScheduleStore as ScheduleStore,
)
