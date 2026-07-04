"""OpenAI Chat Completions API client streaming ModelEvents."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai

from selfhost.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
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
from selfhost.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3


def openai_sdk_client(api_key: str, base_url: str | None = None) -> openai.AsyncOpenAI:
    """SDK client with its own retries disabled: the retry policy lives in OpenAIClient. A base_url
    points the OpenAI-compatible client at another host — an OpenRouter or similar model-provider
    extension speaks the OpenAI wire against its own endpoint."""
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS
    )


def _openai_image(source: ImageSource) -> dict[str, object]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{source.media_type};base64,{source.data}"},
    }


def _openai_tool_result(
    result: str | tuple[ToolResultContent, ...],
) -> tuple[str, list[dict[str, object]]]:
    """A tool result split into its text (for the OpenAI `tool` message, which is text-only) and its
    images as `image_url` parts (which OpenAI carries only in a user message, so the caller lifts
    them into a trailing one)."""
    if isinstance(result, str):
        return result, []
    text_parts: list[str] = []
    images: list[dict[str, object]] = []
    for part in result:
        match part:
            case TextBlock(text=text):
                text_parts.append(text)
            case ImageBlock(source=source):
                images.append(_openai_image(source))
    return "\n".join(text_parts), images


def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = [{"role": "system", "content": system}]
    for message in trim_images(messages):
        content = message.content
        if isinstance(content, str):
            out.append({"role": message.role, "content": content})
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, object]] = []
        image_parts: list[dict[str, object]] = []
        lifted_images: list[dict[str, object]] = []
        for block in content:
            match block:
                case TextBlock(text=text):
                    text_parts.append(text)
                case ImageBlock(source=source):
                    image_parts.append(_openai_image(source))
                case ToolUseBlock(id=block_id, name=name, input=block_input):
                    tool_calls.append(
                        {
                            "id": block_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(block_input)},
                        }
                    )
                case ToolResultBlock(tool_use_id=tool_use_id, content=result, is_error=is_error):
                    text, images = _openai_tool_result(result)
                    if not text and images:
                        text = "[image result follows in the next message]"
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": f"[tool error] {text}" if is_error else text,
                        }
                    )
                    lifted_images.extend(images)
        if tool_calls:
            out.append(
                {
                    "role": message.role,
                    "content": "".join(text_parts) or None,
                    "tool_calls": tool_calls,
                }
            )
        elif image_parts:
            joined = "".join(text_parts)
            parts = [{"type": "text", "text": joined}, *image_parts] if joined else image_parts
            out.append({"role": message.role, "content": parts})
        elif text_parts:
            out.append({"role": message.role, "content": "".join(text_parts)})
        if lifted_images:
            out.append({"role": "user", "content": lifted_images})
    return out


@dataclass(frozen=True)
class OpenAIClient:
    client: openai.AsyncOpenAI

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield text and tool-call events then exactly one Usage as the final event.

        429/5xx responses retry with retry-after-aware exponential backoff, but only until the
        first event is yielded; any failure after that raises immediately. finish_reason=length is
        a truncated completion and raises ModelResponseTruncated. finish_reason=tool_calls is a
        normal stop. An empty completion (no event, finish_reason=stop) is a retryable provider
        failure, re-issued up to MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result
        for the turn loop's nudge — a tool-call-only response has yielded and never degrades.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_call_ids: dict[int, str] = {}
            usage: Usage | None = None
            finish_reason: str | None = None
            create_kwargs: dict[str, object] = {
                "model": request.model,
                "messages": openai_messages(request.system, request.messages),
                "max_tokens": request.max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if request.reasoning != "off":
                create_kwargs["reasoning_effort"] = request.reasoning
            if request.tools:
                create_kwargs["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        },
                    }
                    for t in request.tools
                ]
            try:
                stream = await self.client.chat.completions.create(**create_kwargs)
                async for chunk in stream:
                    if chunk.usage is not None:
                        details = chunk.usage.prompt_tokens_details
                        usage = Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read_tokens=(
                                (details.cached_tokens or 0) if details is not None else 0
                            ),
                        )
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    if delta.content:
                        yielded = True
                        yield TextDelta(text=delta.content)
                    for call in delta.tool_calls or ():
                        if call.index not in tool_call_ids:
                            tool_call_ids[call.index] = call.id or ""
                            yielded = True
                            yield ToolCallStart(
                                id=call.id or "",
                                name=call.function.name if call.function else "",
                            )
                        if call.function is not None and call.function.arguments:
                            yielded = True
                            yield ToolCallDelta(
                                id=tool_call_ids[call.index],
                                partial_json=call.function.arguments,
                            )
            except openai.APIStatusError as error:
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
            if finish_reason == "length":
                raise ModelResponseTruncated(
                    "OpenAI completion truncated at the max_tokens budget (finish_reason=length)"
                )
            if usage is None:
                raise RuntimeError("model stream produced no usage")
            if (
                not yielded
                and finish_reason == "stop"
                and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES
            ):
                empty_attempt += 1
                continue
            yield usage
            return
