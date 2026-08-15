"""OpenAI-wire client streaming ModelEvents over the Chat Completions or Responses API.

The api surface a model is called on is a fact of its `ModelSpec` (`spec.api_surface`), not a guess
from its id: a model that rejects `tools` + `reasoning_effort` together on `/v1/chat/completions`
(the `gpt-5.6-terra` case, #568) declares `api_surface="responses"` and this client renders the
legal Responses request. One client class serves both surfaces so an OpenAI-compatible extension
(Bedrock Mantle, OpenRouter) reuses it by handing its own spec and base_url."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningItem,
    ResponseReasoningItemParam,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.responses.response_function_call_output_item_list_param import (
    ResponseFunctionCallOutputItemParam,
)
from openai.types.responses.response_function_tool_call_param import ResponseFunctionToolCallParam
from openai.types.responses.response_input_image_content_param import ResponseInputImageContentParam
from openai.types.responses.response_input_image_param import ResponseInputImageParam
from openai.types.responses.response_input_message_content_list_param import (
    ResponseInputContentParam,
)
from openai.types.responses.response_input_param import FunctionCallOutput, ResponseInputItemParam
from openai.types.responses.response_input_text_content_param import ResponseInputTextContentParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam
from openai.types.responses.response_reasoning_item_param import Summary as ReasoningSummaryParam

from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelEvent,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    ModelStreamStart,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolResultContent,
    ToolUseBlock,
    trim_images,
)
from ufo.models.spec import KEY_REJECTED_STATUS, ModelSpec
from ufo.o11y import emit_metric, log
from ufo.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3
STREAM_TRANSPORT_ERRORS = (
    openai.APITimeoutError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
STREAM_STATUS_ERRORS = (openai.APIStatusError,)
REASONING_ENCRYPTED_CONTENT = "reasoning.encrypted_content"


def openai_sdk_client(api_key: str, base_url: str | None = None) -> openai.AsyncOpenAI:
    """SDK client with its own retries disabled: the retry policy lives in OpenAIClient. A base_url
    points the OpenAI-compatible client at another host — an OpenRouter or Bedrock Mantle
    model-provider extension speaks the OpenAI wire against its own endpoint."""
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
    """Canonical messages as Chat Completions messages. Every reasoning block is dropped, whichever
    provider produced it: a chat assistant message carries `content`, `refusal`, and `tool_calls`
    and nothing that holds reasoning, so this surface has no place to put a round's reasoning and
    the model resumes a tool round from the tool result alone."""
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
                case ThinkingBlock() | RedactedThinkingBlock() | ReasoningItemBlock():
                    continue
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


def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]:
    """Canonical messages as Responses API input items: reasoning items, text/image content,
    function calls, and function-call outputs — the shape `/v1/responses` accepts. A message's
    reasoning items go back whole, keeping their order among themselves and landing ahead of the
    round's function calls, which is what lets the model resume the reasoning that chose them; an
    Anthropic thinking block is another wire's shape of the same idea and is dropped."""
    items: list[ResponseInputItemParam] = []
    for message in trim_images(messages):
        if isinstance(message.content, str):
            items.append(EasyInputMessageParam(role=message.role, content=message.content))
            continue
        content: list[ResponseInputContentParam] = []
        for block in message.content:
            match block:
                case ThinkingBlock() | RedactedThinkingBlock():
                    continue
                case ReasoningItemBlock(id=item_id, encrypted_content=encrypted, summary=summary):
                    items.append(
                        ResponseReasoningItemParam(
                            type="reasoning",
                            id=item_id,
                            encrypted_content=encrypted,
                            summary=[
                                ReasoningSummaryParam(type="summary_text", text=part)
                                for part in summary
                            ],
                        )
                    )
                case TextBlock(text=text):
                    content.append(ResponseInputTextParam(type="input_text", text=text))
                case ImageBlock(source=source):
                    content.append(
                        ResponseInputImageParam(
                            type="input_image",
                            detail="auto",
                            image_url=f"data:{source.media_type};base64,{source.data}",
                        )
                    )
                case ToolUseBlock(id=call_id, name=name, input=arguments):
                    if content:
                        items.append(EasyInputMessageParam(role=message.role, content=content))
                        content = []
                    items.append(
                        ResponseFunctionToolCallParam(
                            type="function_call",
                            call_id=call_id,
                            name=name,
                            arguments=json.dumps(arguments),
                        )
                    )
                case ToolResultBlock(tool_use_id=call_id, content=result, is_error=is_error):
                    if content:
                        items.append(EasyInputMessageParam(role=message.role, content=content))
                        content = []
                    if isinstance(result, str):
                        output: str | list[ResponseFunctionCallOutputItemParam] = (
                            f"[tool error] {result}" if is_error else result
                        )
                    else:
                        output = [
                            ResponseInputTextContentParam(
                                type="input_text",
                                text=f"[tool error] {part.text}" if is_error else part.text,
                            )
                            if isinstance(part, TextBlock)
                            else ResponseInputImageContentParam(
                                type="input_image",
                                detail="auto",
                                image_url=(
                                    f"data:{part.source.media_type};base64,{part.source.data}"
                                ),
                            )
                            for part in result
                        ]
                    items.append(
                        FunctionCallOutput(
                            type="function_call_output",
                            call_id=call_id,
                            output=output,
                        )
                    )
        if content:
            items.append(EasyInputMessageParam(role=message.role, content=content))
    return items


def responses_request(request: ModelRequest) -> dict[str, Any]:
    """The `/v1/responses` request. `store=False` keeps the conversation ours — nothing is left on
    the provider between rounds — and `include` is what asks for the encrypted reasoning body that
    a kept conversation then has to replay: without it a reasoning item comes back as an id the
    next request cannot resolve, so the pair travels together and neither is conditional."""
    kwargs: dict[str, Any] = {
        "model": request.model,
        "instructions": request.system,
        "input": responses_input(request.messages),
        "include": [REASONING_ENCRYPTED_CONTENT],
        "max_output_tokens": request.max_tokens,
        "store": False,
        "stream": True,
    }
    if request.reasoning not in ("off", "auto"):
        kwargs["reasoning"] = {"effort": request.reasoning}
    if request.tools:
        kwargs["tools"] = [
            FunctionToolParam(
                type="function",
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
                strict=False,
            )
            for tool in request.tools
        ]
        kwargs["parallel_tool_calls"] = request.tool_choice is None
        if request.tool_choice is not None:
            kwargs["tool_choice"] = {"type": "function", "name": request.tool_choice}
    return kwargs


@dataclass(frozen=True)
class OpenAIClient:
    """An OpenAI-wire backend for one model. `spec.api_surface` selects the Chat Completions or
    Responses request shape; `spec.reasoning` gates whether reasoning is emitted and whether it
    composes with tools on the chat surface (the #568 fix)."""

    client: openai.AsyncOpenAI
    spec: ModelSpec

    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self.spec.api_surface == "responses":
            return self._complete_responses(request)
        return self._complete_chat(request)

    def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        create_kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": openai_messages(request.system, request.messages),
            "max_completion_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        effort = self.spec.default_reasoning(request.reasoning, request.tools)
        if effort not in ("off", "auto"):
            create_kwargs["reasoning_effort"] = effort
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
            create_kwargs["parallel_tool_calls"] = request.tool_choice is None
            if request.tool_choice is not None:
                create_kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": request.tool_choice},
                }
        return create_kwargs

    async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield text and tool-call events then exactly one Usage as the final event.

        429/5xx responses retry with retry-after-aware exponential backoff, and request timeouts
        or a dropped connection (a raw httpx error the SDK does not wrap once streaming starts)
        retry on the same backoff and shared attempt budget (each retry logged, exhaustion logged
        and re-raising the fault) — both only until visible output is yielded; any failure after
        that raises immediately. A 401 is the provider refusing the key this spec resolved, so it
        raises that spec's credential fault instead of the SDK's auth error — the round says which
        slot or env to replace, and the verdict is deterministic per key, so nothing retries it.
        finish_reason=length is a truncated completion and raises ModelResponseTruncated.
        finish_reason=tool_calls is a normal stop. An empty completion (no event,
        finish_reason=stop) is a retryable provider failure, re-issued up to
        MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result for the turn loop's nudge —
        a tool-call-only response has yielded and never degrades.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_call_ids: dict[int, str] = {}
            usage: Usage | None = None
            finish_reason: str | None = None
            try:
                stream = await self.client.chat.completions.create(**self._chat_kwargs(request))
                stream_started = False
                async for chunk in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    if chunk.usage is not None:
                        details = chunk.usage.prompt_tokens_details
                        cached_tokens = (details.cached_tokens or 0) if details is not None else 0
                        if cached_tokens > chunk.usage.prompt_tokens:
                            raise RuntimeError("cached prompt tokens exceed total prompt tokens")
                        usage = Usage(
                            input_tokens=chunk.usage.prompt_tokens - cached_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read_tokens=cached_tokens,
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
            except STREAM_TRANSPORT_ERRORS as error:
                attempt += 1
                if yielded or attempt > MAX_PROVIDER_RETRIES:
                    log(
                        "model.provider_transport_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt,
                        error_class=type(error).__name__,
                    )
                    raise
                log(
                    "model.provider_transport_retry",
                    provider=self.spec.provider,
                    model=request.model,
                    attempt=attempt,
                    error_class=type(error).__name__,
                    wait_seconds=delay,
                )
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="transport",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            except STREAM_STATUS_ERRORS as error:
                if error.status_code == KEY_REJECTED_STATUS:
                    log(
                        "model.provider_status_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt + 1,
                        status_code=error.status_code,
                    )
                    raise self.spec.key_rejected() from error
                attempt += 1
                retryable = error.status_code == 429 or error.status_code >= 500
                if yielded or not retryable or attempt > MAX_PROVIDER_RETRIES:
                    log(
                        "model.provider_status_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt,
                        status_code=error.status_code,
                    )
                    raise
                header = error.response.headers.get("retry-after")
                try:
                    wait = max(float(header), 0.0) if header is not None else delay
                except ValueError:
                    wait = delay
                log(
                    "model.provider_status_retry",
                    provider=self.spec.provider,
                    model=request.model,
                    attempt=attempt,
                    status_code=error.status_code,
                    wait_seconds=wait,
                )
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="status",
                )
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
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                continue
            yield usage
            return

    async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """The Responses-API twin of `_complete_chat` for a model whose spec sets
        `api_surface="responses"` — same retry, truncation, refusal, and empty-completion contract,
        translated to the Responses streaming events. Reasoning is gated by the spec exactly as the
        chat path is: an unsupported reasoning or reasoning-with-tools combination never emits the
        thinking parameters.

        The round's reasoning items are collected whole off their done events — the event that
        carries the encrypted body, which the added event does not — and yielded once the stream
        closes, keeping their order among themselves and arriving just ahead of the Usage, so the
        engine can send them back on the assistant message that carries this round's function
        calls: what the provider asks for so the model resumes its reasoning when the results
        return. Held until the stream closes because reasoning is not live output and a re-issued
        attempt must not deliver the abandoned attempt's items, and reasoning alone never counts as
        having yielded: an answerless round stays an empty completion and is retried."""
        effort = self.spec.default_reasoning(request.reasoning, request.tools)
        request = request.model_copy(update={"reasoning": effort})
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_call_ids: dict[str, str] = {}
            tool_call_arguments: set[str] = set()
            reasoning: list[ReasoningItemBlock] = []
            usage: Usage | None = None
            try:
                stream = await self.client.responses.create(**responses_request(request))
                stream_started = False
                async for event in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    match event:
                        case ResponseTextDeltaEvent(delta=text):
                            yielded = True
                            yield TextDelta(text=text)
                        case ResponseOutputItemAddedEvent(
                            item=ResponseFunctionToolCall(id=item_id, call_id=call_id, name=name)
                        ):
                            if item_id is None:
                                raise RuntimeError("OpenAI function call has no item id")
                            tool_call_ids[item_id] = call_id
                            yielded = True
                            yield ToolCallStart(id=call_id, name=name)
                        case ResponseFunctionCallArgumentsDeltaEvent(
                            item_id=item_id, delta=partial_json
                        ):
                            tool_call_arguments.add(item_id)
                            yielded = True
                            yield ToolCallDelta(
                                id=tool_call_ids[item_id], partial_json=partial_json
                            )
                        case ResponseFunctionCallArgumentsDoneEvent(
                            item_id=item_id, arguments=arguments
                        ) if item_id not in tool_call_arguments:
                            yielded = True
                            yield ToolCallDelta(id=tool_call_ids[item_id], partial_json=arguments)
                        case ResponseOutputItemDoneEvent(
                            item=ResponseReasoningItem(
                                id=item_id, summary=parts, encrypted_content=encrypted
                            )
                        ):
                            if encrypted is None:
                                raise RuntimeError("OpenAI reasoning item has no encrypted content")
                            reasoning.append(
                                ReasoningItemBlock(
                                    id=item_id,
                                    encrypted_content=encrypted,
                                    summary=tuple(part.text for part in parts),
                                )
                            )
                        case ResponseRefusalDeltaEvent(delta=refusal):
                            raise ModelRefusal(f"OpenAI declined the completion: {refusal}")
                        case ResponseCompletedEvent(response=response):
                            raw = response.usage
                            if raw is None:
                                raise RuntimeError("model stream produced no usage")
                            cached_tokens = raw.input_tokens_details.cached_tokens
                            if cached_tokens > raw.input_tokens:
                                raise RuntimeError(
                                    "cached prompt tokens exceed total prompt tokens"
                                )
                            usage = Usage(
                                input_tokens=raw.input_tokens - cached_tokens,
                                output_tokens=raw.output_tokens,
                                cache_read_tokens=cached_tokens,
                            )
                        case ResponseIncompleteEvent(response=response):
                            reason = response.incomplete_details
                            if reason is not None and reason.reason == "max_output_tokens":
                                raise ModelResponseTruncated(
                                    "OpenAI response truncated at the max_output_tokens budget"
                                )
                            if reason is not None and reason.reason == "content_filter":
                                raise ModelRefusal(
                                    "OpenAI declined the completion (content_filter)"
                                )
                            raise RuntimeError("OpenAI returned an incomplete response")
                        case ResponseFailedEvent(response=response):
                            message = response.error.message if response.error else "unknown error"
                            raise RuntimeError(f"OpenAI response failed: {message}")
                        case ResponseErrorEvent(message=message):
                            raise RuntimeError(f"OpenAI response failed: {message}")
            except STREAM_TRANSPORT_ERRORS as error:
                attempt += 1
                if yielded or attempt > MAX_PROVIDER_RETRIES:
                    log(
                        "model.provider_transport_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt,
                        error_class=type(error).__name__,
                    )
                    raise
                log(
                    "model.provider_transport_retry",
                    provider=self.spec.provider,
                    model=request.model,
                    attempt=attempt,
                    error_class=type(error).__name__,
                    wait_seconds=delay,
                )
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="transport",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            except STREAM_STATUS_ERRORS as error:
                if error.status_code == KEY_REJECTED_STATUS:
                    log(
                        "model.provider_status_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt + 1,
                        status_code=error.status_code,
                    )
                    raise self.spec.key_rejected() from error
                attempt += 1
                retryable = error.status_code == 429 or error.status_code >= 500
                if yielded or not retryable or attempt > MAX_PROVIDER_RETRIES:
                    log(
                        "model.provider_status_error",
                        provider=self.spec.provider,
                        model=request.model,
                        attempts=attempt,
                        status_code=error.status_code,
                    )
                    raise
                header = error.response.headers.get("retry-after")
                try:
                    wait = max(float(header), 0.0) if header is not None else delay
                except ValueError:
                    wait = delay
                log(
                    "model.provider_status_retry",
                    provider=self.spec.provider,
                    model=request.model,
                    attempt=attempt,
                    status_code=error.status_code,
                    wait_seconds=wait,
                )
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="status",
                )
                await asyncio.sleep(wait)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            if usage is None:
                raise RuntimeError("model stream produced no usage")
            if not yielded and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES:
                empty_attempt += 1
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                continue
            for block in reasoning:
                yield block
            yield usage
            return
