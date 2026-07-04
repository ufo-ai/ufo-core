"""Public re-export: the model seam — the wire types a Trajectory carries and a model leg speaks,
the `ModelClient` protocol a model-provider extension implements, and the OpenAI-wire building
blocks (message translation, SDK client factory) such an extension reuses to speak a compatible
endpoint. An extension types against these here rather than reaching into core internals."""

from selfhost.accounting import (
    ModelPrice as ModelPrice,
)
from selfhost.models.interface import (
    ContentBlock as ContentBlock,
)
from selfhost.models.interface import (
    ImageBlock as ImageBlock,
)
from selfhost.models.interface import (
    ImageSource as ImageSource,
)
from selfhost.models.interface import (
    Message as Message,
)
from selfhost.models.interface import (
    ModelClient as ModelClient,
)
from selfhost.models.interface import (
    ModelEvent as ModelEvent,
)
from selfhost.models.interface import (
    ModelRequest as ModelRequest,
)
from selfhost.models.interface import (
    ModelResponseTruncated as ModelResponseTruncated,
)
from selfhost.models.interface import (
    TextBlock as TextBlock,
)
from selfhost.models.interface import (
    TextDelta as TextDelta,
)
from selfhost.models.interface import (
    ToolCallDelta as ToolCallDelta,
)
from selfhost.models.interface import (
    ToolCallStart as ToolCallStart,
)
from selfhost.models.interface import (
    ToolResultBlock as ToolResultBlock,
)
from selfhost.models.interface import (
    ToolUseBlock as ToolUseBlock,
)
from selfhost.models.openai import (
    openai_messages as openai_messages,
)
from selfhost.models.openai import (
    openai_sdk_client as openai_sdk_client,
)
from selfhost.schema.records import (
    Usage as Usage,
)
