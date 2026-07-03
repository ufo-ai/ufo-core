"""Public re-export: the message wire types a Trajectory carries and a model leg speaks.

An extension reading `ExtensionContext.trajectories` inspects and replays these, so it types
against them here rather than reaching into core internals."""

from selfhost.models.interface import (
    ContentBlock as ContentBlock,
)
from selfhost.models.interface import (
    Message as Message,
)
from selfhost.models.interface import (
    TextBlock as TextBlock,
)
from selfhost.models.interface import (
    ToolResultBlock as ToolResultBlock,
)
from selfhost.models.interface import (
    ToolUseBlock as ToolUseBlock,
)
