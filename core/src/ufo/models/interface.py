"""The model interface: the ModelClient protocol and the wire types it speaks."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from ufo.schema.records import DEFAULT_REASONING_EFFORT, ReasoningEffort, Usage


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageSource(BaseModel):
    type: Literal["base64"] = "base64"
    media_type: str
    data: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


ToolResultContent = Annotated[TextBlock | ImageBlock, Field(discriminator="type")]


class ToolResultBlock(BaseModel):
    """A tool's result for the model. `content` is plain text for the common case; a tool that
    returns visual output (a read of an image or PDF, a browser screenshot) carries a tuple of
    text and image blocks — the shape Anthropic's `tool_result.content` accepts natively and the
    OpenAI client lifts into a trailing user image message."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | tuple[ToolResultContent, ...]
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock, Field(discriminator="type")
]

MAX_IMAGES_PER_MESSAGE = 20
MAX_IMAGES_PER_REQUEST = 100
IMAGE_OMITTED_TEXT = "[image omitted: over the provider image limit]"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | tuple[ContentBlock, ...]


class ToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ModelRequest(BaseModel):
    """`reasoning` is the extended-thinking depth each client renders in its provider's shape — the
    Anthropic `thinking` block plus `output_config.effort`, the OpenAI/OpenRouter `reasoning`/effort
    param. `max_tokens` is reasoning-inclusive: thinking draws from it, so the visible answer gets
    what thinking leaves. `off` omits the thinking parameters entirely. `tool_choice` compels the
    named tool as the round's single act — it must name an offered tool, and the request runs with
    reasoning off (Anthropic rejects a forced tool choice under extended thinking)."""

    model: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int
    tools: tuple[ToolSchema, ...] = ()
    tool_choice: str | None = None
    reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT

    @model_validator(mode="after")
    def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> "ModelRequest":
        if self.tool_choice is None:
            return self
        if all(tool.name != self.tool_choice for tool in self.tools):
            raise ValueError(f"tool_choice {self.tool_choice!r} names no offered tool")
        if self.reasoning != "off":
            raise ValueError("a forced tool_choice request must run with reasoning off")
        return self


class TextDelta(BaseModel):
    text: str


class ToolCallStart(BaseModel):
    id: str
    name: str


class ToolCallDelta(BaseModel):
    id: str
    partial_json: str


ModelEvent = TextDelta | ToolCallStart | ToolCallDelta | Usage


class ModelResponseTruncated(RuntimeError):
    """The model hit its max_tokens budget mid-response (Anthropic stop_reason=max_tokens /
    OpenAI finish_reason=length). Raised loudly rather than delivering a cut-off completion as a
    finished turn — the budget is reasoning-inclusive, so raise max_tokens if it recurs."""


class ModelRefusal(RuntimeError):
    """The provider declined the completion outright (Anthropic stop_reason=refusal). Raised
    loudly rather than delivering the zero-content stream as an empty success — a refusal is
    deterministic per request, so retrying or degrading would only mislabel the failure."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...


PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
AUTO_MODEL = "auto"


def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Drop the OLDEST inline images until each message holds ≤MAX_IMAGES_PER_MESSAGE and the whole
    request holds ≤MAX_IMAGES_PER_REQUEST — Anthropic's caps, the tightest across providers, applied
    once to the canonical messages before either client translates them. Images ride both as
    top-level blocks and inside a tool_result's content; both count and both trim. A dropped image
    becomes a short text placeholder so the message stays non-empty and the model knows one was
    elided. Recent images matter most to the current turn, so the tail is what survives."""
    positions = _image_positions(messages)
    if not positions:
        return messages
    keep_request = set(positions[-MAX_IMAGES_PER_REQUEST:])
    keep_message: set[tuple[int, int, int | None]] = set()
    for message_index in {position[0] for position in positions}:
        in_message = [position for position in positions if position[0] == message_index]
        keep_message |= set(in_message[-MAX_IMAGES_PER_MESSAGE:])
    drop = set(positions) - (keep_request & keep_message)
    if not drop:
        return messages
    return tuple(_trim_message(index, message, drop) for index, message in enumerate(messages))


def _image_positions(
    messages: tuple[Message, ...],
) -> list[tuple[int, int, int | None]]:
    """Every inline image as (message_index, block_index, sub_index) in oldest-first order, where
    sub_index is None for a top-level image block and the index within a tool_result's content
    tuple for a nested one."""
    positions: list[tuple[int, int, int | None]] = []
    for message_index, message in enumerate(messages):
        if isinstance(message.content, str):
            continue
        for block_index, block in enumerate(message.content):
            match block:
                case ImageBlock():
                    positions.append((message_index, block_index, None))
                case ToolResultBlock(content=tuple(parts)):
                    positions.extend(
                        (message_index, block_index, sub_index)
                        for sub_index, part in enumerate(parts)
                        if isinstance(part, ImageBlock)
                    )
    return positions


def _trim_message(
    message_index: int, message: Message, drop: set[tuple[int, int, int | None]]
) -> Message:
    if isinstance(message.content, str):
        return message
    blocks: list[ContentBlock] = []
    for block_index, block in enumerate(message.content):
        if (message_index, block_index, None) in drop:
            blocks.append(TextBlock(text=IMAGE_OMITTED_TEXT))
        elif isinstance(block, ToolResultBlock) and isinstance(block.content, tuple):
            blocks.append(
                block.model_copy(
                    update={
                        "content": tuple(
                            TextBlock(text=IMAGE_OMITTED_TEXT)
                            if (message_index, block_index, sub_index) in drop
                            else part
                            for sub_index, part in enumerate(block.content)
                        )
                    }
                )
            )
        else:
            blocks.append(block)
    return message.model_copy(update={"content": tuple(blocks)})
