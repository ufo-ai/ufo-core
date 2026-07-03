"""Public re-export: extensions declare tools and write handlers against these, not core internals.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from selfhost.tools.context import (
    TextContent as TextContent,
)
from selfhost.tools.context import (
    ToolContext as ToolContext,
)
from selfhost.tools.context import (
    ToolResult as ToolResult,
)
from selfhost.tools.registry import (
    ToolDef as ToolDef,
)
