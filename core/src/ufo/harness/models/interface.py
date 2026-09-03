"""The model interface: the ModelClient protocol and the wire types it speaks."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from ufo.schema.records import DEFAULT_REASONING_EFFORT, ReasoningEffort, Usage

ConversationCacheTtl = Literal["5m", "1h"]


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


class ThinkingBlock(BaseModel):
    """A reasoning block the model streamed ahead of its text and tool calls, carried opaquely:
    the provider requires it echoed back unchanged when the conversation continues on the same
    model, and the signature is what authenticates it there. The text is whatever that model's
    display default returns — a summary on the 4.6-era models, empty on the ones that omit it — so
    it is real data either way. Doubles as the ModelEvent a client yields once the block is
    whole."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str


class RedactedThinkingBlock(BaseModel):
    """A reasoning block the provider encrypted before returning it: `data` is the whole block and
    there is no readable text. It rides the round's reasoning sequence in the position the provider
    streamed it and is echoed back unchanged like any other — a sequence missing one block, or
    reordering two, is a modified sequence the provider rejects."""

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str


class ReasoningItemBlock(BaseModel):
    """A reasoning item an OpenAI-wire model returned on the Responses surface, carried whole so the
    next request can replay it: the provider asks for every reasoning item that came back with a
    function call to be sent again alongside that call's output. The request asks for
    `encrypted_content` so the item carries the reasoning itself and resolves on its own bytes
    rather than on state a `store=False` request left nowhere. `summary` is the provider's summary
    parts in their own order — the readable side, empty when the request asked for no summary — and
    is a required field of the echoed item either way."""

    type: Literal["reasoning"] = "reasoning"
    id: str
    encrypted_content: str
    summary: tuple[str, ...] = ()


ReasoningBlock = Annotated[
    ThinkingBlock | RedactedThinkingBlock | ReasoningItemBlock, Field(discriminator="type")
]

ToolResultContent = Annotated[TextBlock | ImageBlock, Field(discriminator="type")]


class ToolResultBlock(BaseModel):
    """A tool's result for the model. `content` is plain text for the common case; a tool that
    returns visual output (a read of an image or PDF, a browser screenshot) carries a tuple of
    text and image blocks — the shape Anthropic's `tool_result.content` accepts natively and the
    OpenAI client lifts into a trailing user image message. `activity` records that the call passed
    requester binding and entered dispatch, which is when its member-facing activity generation
    starts. `activity_text` holds the result when it finished before the transcript was written."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | tuple[ToolResultContent, ...]
    is_error: bool = False
    activity: bool = False
    activity_text: str = ""


ContentBlock = Annotated[
    TextBlock
    | ImageBlock
    | ToolUseBlock
    | ToolResultBlock
    | ThinkingBlock
    | RedactedThinkingBlock
    | ReasoningItemBlock,
    Field(discriminator="type"),
]

MAX_IMAGES_PER_MESSAGE = 20
MAX_IMAGES_PER_REQUEST = 100
MAX_IMAGE_BYTES_PER_REQUEST = 20 * 1024 * 1024
IMAGE_OMITTED_TEXT = "[image omitted: over the provider image limit]"
IMAGE_UNSUPPORTED_TEXT = "[image omitted: model accepts text input only]"


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
    what thinking leaves. `auto` sends Anthropic's adaptive thinking with no effort parameter, so
    the model calibrates depth per request; the OpenAI-wire clients have no such mode, so there
    `auto` is the parameter-free request the provider answers at its own default. `off` switches
    reasoning off, which each wire says in its own shape: Anthropic omits the thinking parameters,
    the OpenAI wire sends effort `none` and OpenRouter `enabled: false` — a wire that reads an
    absent parameter as its default effort has to state it. `tool_choice` compels the named tool as
    the round's single act — it must name an offered tool. `conversation_cache_ttl` is
    how long the changing conversation tail stays warm; the Anthropic client keeps the tools and
    system prefix for one hour, and clients whose provider caches on its own ignore it.
    `session_id` names the series this call shares a prompt prefix with — the conversation for a
    turn's rounds, the job for a background call. A router pins a session to one upstream so that
    provider's cache is the one the next call meets, and a router client raises on a request that
    names none; a client that speaks to a single provider has one cache and never reads it, so the
    calls that reach only those leave it unset. `defer_long_retry` lets a durable background caller
    end its workflow when a provider gives a long retry delay; callers that must answer inline keep
    the retry inside their request."""

    model: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int
    conversation_cache_ttl: ConversationCacheTtl
    session_id: str | None = Field(default=None, min_length=1)
    tools: tuple[ToolSchema, ...] = ()
    tool_choice: str | None = None
    reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT
    defer_long_retry: bool = False

    @model_validator(mode="after")
    def _forced_choice_names_an_offered_tool(self) -> "ModelRequest":
        if self.tool_choice is None:
            return self
        if all(tool.name != self.tool_choice for tool in self.tools):
            raise ValueError(f"tool_choice {self.tool_choice!r} names no offered tool")
        return self


class TextDelta(BaseModel):
    text: str


class ToolCallStart(BaseModel):
    id: str
    name: str


class ToolCallDelta(BaseModel):
    id: str
    partial_json: str


@dataclass(frozen=True)
class ModelStreamStart:
    """The first event received from one provider stream attempt."""


ModelEvent = (
    ModelStreamStart
    | TextDelta
    | ToolCallStart
    | ToolCallDelta
    | ThinkingBlock
    | RedactedThinkingBlock
    | ReasoningItemBlock
    | Usage
)


class ModelResponseTruncated(RuntimeError):
    """The model hit its max_tokens budget mid-response (Anthropic stop_reason=max_tokens /
    OpenAI finish_reason=length). Raised loudly rather than delivering a cut-off completion as a
    finished turn — the budget is reasoning-inclusive, so raise max_tokens if it recurs."""


class ModelRefusal(RuntimeError):
    """The provider declined the completion outright (Anthropic stop_reason=refusal). Raised
    loudly rather than delivering the zero-content stream as an empty success — a refusal is
    deterministic per request, so retrying or degrading would only mislabel the failure."""


class ModelAccountRateLimited(RuntimeError):
    """The provider rate-limited the account serving this call, and the client's own backoff did
    not outlast the limit. Typed rather than left as the provider SDK's status error because the
    verdict is about the account and not the request: a turn that holds a second account of the
    member's moves onto it, and a turn that holds none names the account the member must fix."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...


PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
AUTO_MODEL = "auto"


def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Drop the OLDEST inline images until each message holds ≤MAX_IMAGES_PER_MESSAGE and the whole
    request holds ≤MAX_IMAGES_PER_REQUEST — Anthropic's caps, the tightest across providers, applied
    once to the canonical messages before either client translates them. Kept images then spend a
    request-wide byte budget (MAX_IMAGE_BYTES_PER_REQUEST, newest first) so a stack of bounded
    scans cannot push the request past the provider's size cap. Images ride both as top-level
    blocks and inside a tool_result's content; both count and both trim. A dropped image becomes
    a short text placeholder so the message stays non-empty and the model knows one was elided.
    Recent images matter most to the current turn, so the tail is what survives."""
    positions = _image_positions(messages)
    if not positions:
        return messages
    keep_request = set(positions[-MAX_IMAGES_PER_REQUEST:])
    keep_message: set[tuple[int, int, int | None]] = set()
    for message_index in {position[0] for position in positions}:
        in_message = [position for position in positions if position[0] == message_index]
        keep_message |= set(in_message[-MAX_IMAGES_PER_MESSAGE:])
    kept = [position for position in positions if position in (keep_request & keep_message)]
    budget = MAX_IMAGE_BYTES_PER_REQUEST
    within_budget: set[tuple[int, int, int | None]] = set()
    for position in reversed(kept):
        budget -= _image_data_len(messages, position)
        if budget < 0:
            break
        within_budget.add(position)
    drop = set(positions) - within_budget
    if not drop:
        return messages
    return tuple(
        _trim_message(index, message, drop, IMAGE_OMITTED_TEXT)
        for index, message in enumerate(messages)
    )


def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Replace every image with an explicit text marker for a model that accepts text input only."""
    drop = set(_image_positions(messages))
    if not drop:
        return messages
    return tuple(
        _trim_message(index, message, drop, IMAGE_UNSUPPORTED_TEXT)
        for index, message in enumerate(messages)
    )


def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int:
    message_index, block_index, sub_index = position
    block = messages[message_index].content[block_index]
    match block, sub_index:
        case ImageBlock(source=source), None:
            return len(source.data)
        case ToolResultBlock(content=tuple(parts)), int():
            part = parts[sub_index]
            match part:
                case ImageBlock(source=source):
                    return len(source.data)
    raise RuntimeError(f"position {position} does not address an image block")


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
    message_index: int,
    message: Message,
    drop: set[tuple[int, int, int | None]],
    replacement: str,
) -> Message:
    if isinstance(message.content, str):
        return message
    blocks: list[ContentBlock] = []
    for block_index, block in enumerate(message.content):
        if (message_index, block_index, None) in drop:
            blocks.append(TextBlock(text=replacement))
        elif isinstance(block, ToolResultBlock) and isinstance(block.content, tuple):
            blocks.append(
                block.model_copy(
                    update={
                        "content": tuple(
                            TextBlock(text=replacement)
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
