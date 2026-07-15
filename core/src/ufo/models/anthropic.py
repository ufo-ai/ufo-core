"""Anthropic Messages API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from ufo.models.interface import (
    ContentBlock,
    ImageBlock,
    ImageSource,
    ModelEvent,
    ModelRequest,
    ModelResponseTruncated,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolResultContent,
    ToolUseBlock,
    trim_images,
)
from ufo.o11y import log
from ufo.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3

CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}


def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic:
    """SDK client with its own retries disabled: the retry policy lives in AnthropicClient."""
    return anthropic.AsyncAnthropic(
        api_key=api_key, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS
    )


def _anthropic_image(source: ImageSource) -> dict[str, object]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": source.media_type, "data": source.data},
    }


def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]:
    match part:
        case TextBlock(text=text):
            return {"type": "text", "text": text}
        case ImageBlock(source=source):
            return _anthropic_image(source)


def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]:
    if isinstance(content, str):
        return content
    blocks: list[dict[str, object]] = []
    for block in content:
        match block:
            case TextBlock(text=text):
                blocks.append({"type": "text", "text": text})
            case ImageBlock(source=source):
                blocks.append(_anthropic_image(source))
            case ToolUseBlock(id=block_id, name=name, input=block_input):
                blocks.append(
                    {"type": "tool_use", "id": block_id, "name": name, "input": block_input}
                )
            case ToolResultBlock(tool_use_id=tool_use_id, content=result, is_error=is_error):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result
                        if isinstance(result, str)
                        else [_anthropic_tool_result_part(part) for part in result],
                        "is_error": is_error,
                    }
                )
    return blocks


@dataclass(frozen=True)
class AnthropicClient:
    client: anthropic.AsyncAnthropic

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield text and tool-call events then exactly one Usage as the final event.

        429/5xx responses retry with retry-after-aware exponential backoff, and request timeouts
        retry on the same backoff and shared attempt budget (each retry logged, exhaustion logged
        and re-raising the provider's APITimeoutError) — both only until the first event is
        yielded; any failure after that raises immediately. stop_reason=max_tokens
        is a truncated completion and raises ModelResponseTruncated. stop_reason=tool_use is a
        normal stop. An empty completion (no event, stop_reason=end_turn) is a retryable provider
        failure, re-issued up to MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result
        for the turn loop's nudge — a tool-call-only response has yielded and never degrades.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_use_ids: dict[int, str] = {}
            input_tokens = 0
            cache_read_tokens = 0
            cache_write_tokens = 0
            output_tokens: int | None = None
            stop_reason: str | None = None
            create_kwargs: dict[str, Any] = {
                "model": request.model,
                "system": [
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": CACHE_CONTROL,
                    }
                ],
                "messages": [
                    {"role": m.role, "content": anthropic_content(m.content)}
                    for m in trim_images(request.messages)
                ],
                "max_tokens": request.max_tokens,
                "stream": True,
                "cache_control": CACHE_CONTROL,
            }
            if request.reasoning != "off":
                create_kwargs["thinking"] = {"type": "adaptive"}
                create_kwargs["output_config"] = {"effort": request.reasoning}
            if request.tools:
                create_kwargs["tools"] = [
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in request.tools
                ]
                create_kwargs["tools"][-1]["cache_control"] = CACHE_CONTROL
                create_kwargs["tool_choice"] = {
                    "type": "auto",
                    "disable_parallel_tool_use": False,
                }
            try:
                stream = await self.client.messages.create(**create_kwargs)
                async for event in stream:
                    match event:
                        case anthropic.types.RawMessageStartEvent(message=message):
                            input_tokens = message.usage.input_tokens
                            cache_read_tokens = message.usage.cache_read_input_tokens or 0
                            cache_write_tokens = message.usage.cache_creation_input_tokens or 0
                        case anthropic.types.RawContentBlockStartEvent(
                            content_block=anthropic.types.ToolUseBlock(id=block_id, name=name),
                            index=index,
                        ):
                            tool_use_ids[index] = block_id
                            yielded = True
                            yield ToolCallStart(id=block_id, name=name)
                        case anthropic.types.RawContentBlockDeltaEvent(
                            delta=anthropic.types.TextDelta(text=text)
                        ):
                            yielded = True
                            yield TextDelta(text=text)
                        case anthropic.types.RawContentBlockDeltaEvent(
                            delta=anthropic.types.InputJSONDelta(partial_json=partial_json),
                            index=index,
                        ):
                            yielded = True
                            yield ToolCallDelta(id=tool_use_ids[index], partial_json=partial_json)
                        case anthropic.types.RawMessageDeltaEvent(delta=delta, usage=usage):
                            output_tokens = usage.output_tokens
                            stop_reason = delta.stop_reason
            except anthropic.APITimeoutError:
                attempt += 1
                if yielded or attempt > MAX_PROVIDER_RETRIES:
                    log(
                        "model.provider_timeout",
                        provider="anthropic",
                        model=request.model,
                        attempts=attempt,
                    )
                    raise
                log(
                    "model.provider_timeout_retry",
                    provider="anthropic",
                    model=request.model,
                    attempt=attempt,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            except anthropic.APIStatusError as error:
                attempt += 1
                retryable = error.status_code == 429 or error.status_code >= 500
                if yielded or not retryable or attempt > MAX_PROVIDER_RETRIES:
                    raise
                header = error.response.headers.get("retry-after")
                try:
                    wait = max(float(header), 0.0) if header is not None else delay
                except ValueError:
                    wait = delay
                await asyncio.sleep(wait)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            if stop_reason == "max_tokens":
                raise ModelResponseTruncated(
                    "Anthropic completion truncated at the max_tokens budget "
                    "(stop_reason=max_tokens)"
                )
            if output_tokens is None:
                raise RuntimeError("model stream produced no usage")
            if (
                not yielded
                and stop_reason == "end_turn"
                and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES
            ):
                empty_attempt += 1
                continue
            yield Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )
            return
