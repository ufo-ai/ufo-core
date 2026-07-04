"""The model leg the proposer, replay, and grader run on: one bounded, non-streaming completion
returning text. The production leg speaks the Anthropic Messages API from a credential-slot key; a
test injects a deterministic stand-in against the same protocol."""

from dataclasses import dataclass
from typing import Protocol

import anthropic

from selfhost.sdk.models import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

MAX_OUTPUT_TOKENS = 2048
PROVIDER_TIMEOUT_SECONDS = 60.0


class ModelLeg(Protocol):
    async def complete(self, system: str, messages: tuple[Message, ...]) -> str: ...


def _wire_block(block: ContentBlock) -> dict[str, object]:
    match block:
        case TextBlock(text=text):
            return {"type": "text", "text": text}
        case ImageBlock(source=source):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source.media_type,
                    "data": source.data,
                },
            }
        case ToolUseBlock(id=block_id, name=name, input=block_input):
            return {"type": "tool_use", "id": block_id, "name": name, "input": block_input}
        case ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content
                if isinstance(content, str)
                else [_wire_block(part) for part in content],
                "is_error": is_error,
            }


def _wire_message(message: Message) -> dict[str, object]:
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    return {"role": message.role, "content": [_wire_block(block) for block in message.content]}


@dataclass(frozen=True)
class AnthropicModelLeg:
    """The production leg: its own SDK retries disabled, bounded by `max_output_tokens`, returning
    the concatenated text of one Messages completion."""

    client: anthropic.AsyncAnthropic
    model: str
    max_output_tokens: int = MAX_OUTPUT_TOKENS

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        response = await self.client.messages.create(
            model=self.model,
            system=system,
            max_tokens=self.max_output_tokens,
            messages=[_wire_message(message) for message in messages],
        )
        return "".join(block.text for block in response.content if block.type == "text")
