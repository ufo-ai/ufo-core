"""OpenAI-wire client streaming ModelEvents over the Chat Completions or Responses API.

The api surface a model is called on is a fact of its `ModelSpec` (`spec.api_surface`), not a guess
from its id: a model that rejects `tools` + `reasoning_effort` together on `/v1/chat/completions`
(the `gpt-5.6-terra` case, #568) declares `api_surface="responses"` and this client renders the
legal Responses request. One client class serves both surfaces so an OpenAI-compatible extension
(Bedrock Mantle, OpenRouter) reuses it by handing its own spec and base_url.

The credential decides the host: a member who signs in with their ChatGPT account holds an account
token, which api.openai.com refuses and the ChatGPT Codex backend answers for — and that backend
serves Responses alone, so a client built for such a token calls that surface whatever the model's
spec declares."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, cast

import httpx
import openai
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import PromptTokensDetails
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
    ResponseStreamEvent,
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
from openai.types.responses.response_output_text_param import ResponseOutputTextParam
from openai.types.responses.response_reasoning_item_param import Summary as ReasoningSummaryParam
from openai.types.responses.response_usage import InputTokensDetails, ResponseUsage
from openai.types.shared.reasoning_effort import ReasoningEffort as OpenAIEffort

from ufo.harness.models.interface import (
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
from ufo.harness.models.spec import KEY_REJECTED_STATUS, ModelSpec
from ufo.harness.o11y import emit_metric, log
from ufo.harness.rounds import ModelStreamInterrupted
from ufo.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3
OPENAI_TOOL_ERROR_PREFIX = "[tool error] "
STREAM_TRANSPORT_ERRORS = (
    openai.APITimeoutError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
STREAM_STATUS_ERRORS = (openai.APIStatusError,)
REASONING_ENCRYPTED_CONTENT = "reasoning.encrypted_content"
REASONING_OFF_EFFORT: OpenAIEffort = "none"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_ACCOUNT_HEADER = "chatgpt-account-id"
CODEX_ORIGINATOR = "ufo"
CODEX_RESPONSES_BETA = "responses=experimental"
CODEX_STREAM_ACCEPT = "text/event-stream"
CHATGPT_AUTH_CLAIM = "https://api.openai.com/auth"
CHATGPT_ACCOUNT_CLAIM = "chatgpt_account_id"


def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int:
    if details is None or details.model_extra is None:
        return 0
    value = details.model_extra.get("cache_write_tokens")
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError("OpenAI cache_write_tokens is not an integer")
    return value


def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage:
    details = raw.input_tokens_details
    cached_tokens = details.cached_tokens
    reported_cache_write_tokens = _cache_write_tokens(details)
    cache_write_tokens = reported_cache_write_tokens if cache_write_30m_priced else 0
    if cached_tokens > raw.input_tokens:
        raise RuntimeError("cached prompt tokens exceed total prompt tokens")
    if cached_tokens + reported_cache_write_tokens > raw.input_tokens:
        raise RuntimeError("cached and cache-write prompt tokens exceed total prompt tokens")
    return Usage(
        input_tokens=raw.input_tokens - cached_tokens - cache_write_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=cached_tokens,
        cache_write_30m_tokens=cache_write_tokens,
    )


def _chat_usage(raw: openai.types.CompletionUsage, cache_write_30m_priced: bool) -> Usage:
    details = raw.prompt_tokens_details
    cached_tokens = (details.cached_tokens or 0) if details is not None else 0
    reported_cache_write_tokens = _cache_write_tokens(details)
    cache_write_tokens = reported_cache_write_tokens if cache_write_30m_priced else 0
    if cached_tokens > raw.prompt_tokens:
        raise RuntimeError("cached prompt tokens exceed total prompt tokens")
    if cached_tokens + reported_cache_write_tokens > raw.prompt_tokens:
        raise RuntimeError("cached and cache-write prompt tokens exceed total prompt tokens")
    return Usage(
        input_tokens=raw.prompt_tokens - cached_tokens - cache_write_tokens,
        output_tokens=raw.completion_tokens,
        cache_read_tokens=cached_tokens,
        cache_write_30m_tokens=cache_write_tokens,
    )


def openai_sdk_client(
    api_key: str, base_url: str | None = None, default_headers: dict[str, str] | None = None
) -> openai.AsyncOpenAI:
    """SDK client with its own retries disabled: the retry policy lives in OpenAIClient. A base_url
    points the OpenAI-compatible client at another host — an OpenRouter or Bedrock Mantle
    model-provider extension speaks the OpenAI wire against its own endpoint — and default_headers
    carries whatever that host demands on every request beyond the bearer."""
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=default_headers,
        max_retries=0,
        timeout=PROVIDER_TIMEOUT_SECONDS,
    )


def chatgpt_account_id(credential: str) -> str | None:
    """The ChatGPT account a member's account token was issued for, read from its own JWT claims,
    or None for a platform API key — which is not a JWT and carries no claims. One slot holds
    whichever the member connected, so the claim is what separates the two wires."""
    parts = credential.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    claims = payload.get(CHATGPT_AUTH_CLAIM) if isinstance(payload, dict) else None
    account = claims.get(CHATGPT_ACCOUNT_CLAIM) if isinstance(claims, dict) else None
    return account if isinstance(account, str) and account else None


def codex_sdk_client(credential: str, account: str) -> openai.AsyncOpenAI:
    """SDK client for the ChatGPT Codex backend, which is what serves a member's account token. It
    answers only when the account the token was issued for, the calling app, and the Responses beta
    ride every request alongside the bearer.

    `Accept` is the fourth: the SDK hardcodes `application/json` on every request and never lifts it
    for a stream, so without this the backend is free to answer a JSON body, the stream yields no
    events, and the turn dies reporting no usage rather than anything a member can act on. The
    capitalization is load-bearing — a lowercase key is appended beside the SDK's rather than
    replacing it, and the request goes out asking for both."""
    headers = {
        CODEX_ACCOUNT_HEADER: account,
        "originator": CODEX_ORIGINATOR,
        "OpenAI-Beta": CODEX_RESPONSES_BETA,
        "Accept": CODEX_STREAM_ACCEPT,
    }
    return openai_sdk_client(credential, base_url=CODEX_BASE_URL, default_headers=headers)


def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float:
    header = error.response.headers.get("retry-after")
    try:
        retry_after = max(float(header), 0.0) if header is not None else delay
    except ValueError:
        retry_after = delay
    return max(retry_after, delay)


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
                            "content": f"{OPENAI_TOOL_ERROR_PREFIX}{text}" if is_error else text,
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
        content: list[ResponseInputContentParam | ResponseOutputTextParam] = []
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
                    content.append(
                        ResponseOutputTextParam(type="output_text", text=text, annotations=[])
                        if message.role == "assistant"
                        else ResponseInputTextParam(type="input_text", text=text)
                    )
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
                        items.append(
                            cast(
                                ResponseInputItemParam,
                                {"role": message.role, "content": content},
                            )
                        )
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
                        items.append(
                            cast(
                                ResponseInputItemParam,
                                {"role": message.role, "content": content},
                            )
                        )
                        content = []
                    if isinstance(result, str):
                        output: str | list[ResponseFunctionCallOutputItemParam] = (
                            f"{OPENAI_TOOL_ERROR_PREFIX}{result}" if is_error else result
                        )
                    else:
                        output = [
                            ResponseInputTextContentParam(
                                type="input_text",
                                text=(
                                    f"{OPENAI_TOOL_ERROR_PREFIX}{part.text}"
                                    if is_error
                                    else part.text
                                ),
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
            items.append(cast(ResponseInputItemParam, {"role": message.role, "content": content}))
    return items


def responses_request(
    request: ModelRequest, effort: OpenAIEffort, codex: bool = False
) -> dict[str, Any]:
    """The `/v1/responses` request, carrying the `effort` the client resolved against the model's
    spec — None sends no reasoning parameter. `store=False` keeps the conversation ours — nothing
    is left on the provider between rounds — and `include` is what asks for the encrypted reasoning
    body that a kept conversation then has to replay: without it a reasoning item comes back as an
    id the next request cannot resolve, so the pair travels together, on every wire this surface
    serves.

    The Codex backend a member's ChatGPT account reaches refuses `max_output_tokens` outright, so
    that budget is the platform wire's alone. The rounds it bounds are bounded there by the same
    round limit every profile carries."""
    kwargs: dict[str, Any] = {
        "model": request.model,
        "instructions": request.system,
        "input": responses_input(request.messages),
        "stream": True,
        "include": [REASONING_ENCRYPTED_CONTENT],
        "store": False,
    }
    if not codex:
        kwargs["max_output_tokens"] = request.max_tokens
    if effort is not None:
        kwargs["reasoning"] = {"effort": effort}
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
class _OpenAIRetry:
    spec: ModelSpec
    model: str
    attempt: int = 0
    delay: float = INITIAL_RETRY_DELAY_SECONDS

    async def transport(self, error: Exception, yielded: bool) -> _OpenAIRetry:
        attempt = self.attempt + 1
        if yielded or attempt > MAX_PROVIDER_RETRIES:
            log(
                "model.provider_transport_error",
                provider=self.spec.provider,
                model=self.model,
                attempts=attempt,
                error_class=type(error).__name__,
            )
            if yielded:
                raise ModelStreamInterrupted(
                    "stream_transport",
                    f"OpenAI stream died mid-round ({type(error).__name__}): {error}",
                ) from error
            raise error
        log(
            "model.provider_transport_retry",
            provider=self.spec.provider,
            model=self.model,
            attempt=attempt,
            error_class=type(error).__name__,
            wait_seconds=self.delay,
        )
        emit_metric(
            "model_provider_retry_total",
            provider=self.spec.provider,
            model=self.model,
            kind="transport",
        )
        await asyncio.sleep(self.delay)
        return replace(
            self,
            attempt=attempt,
            delay=min(self.delay * 2, MAX_RETRY_DELAY_SECONDS),
        )

    async def status(self, error: openai.APIStatusError, yielded: bool) -> _OpenAIRetry:
        if error.status_code == KEY_REJECTED_STATUS:
            log(
                "model.provider_status_error",
                provider=self.spec.provider,
                model=self.model,
                attempts=self.attempt + 1,
                status_code=error.status_code,
            )
            raise self.spec.key_rejected() from error
        attempt = self.attempt + 1
        retryable = error.status_code == 429 or error.status_code >= 500
        if yielded or not retryable or attempt > MAX_PROVIDER_RETRIES:
            log(
                "model.provider_status_error",
                provider=self.spec.provider,
                model=self.model,
                attempts=attempt,
                status_code=error.status_code,
            )
            if yielded and retryable:
                raise ModelStreamInterrupted(
                    "stream_error",
                    f"OpenAI errored the stream mid-round ({error.status_code}): {error}",
                ) from error
            raise error
        wait = _status_retry_wait(error, self.delay)
        log(
            "model.provider_status_retry",
            provider=self.spec.provider,
            model=self.model,
            attempt=attempt,
            status_code=error.status_code,
            wait_seconds=wait,
        )
        emit_metric(
            "model_provider_retry_total",
            provider=self.spec.provider,
            model=self.model,
            kind="status",
        )
        await asyncio.sleep(wait)
        return replace(
            self,
            attempt=attempt,
            delay=min(self.delay * 2, MAX_RETRY_DELAY_SECONDS),
        )


class _ChatStream:
    def __init__(self, cache_write_30m_priced: bool) -> None:
        self.cache_write_30m_priced = cache_write_30m_priced
        self.yielded = False
        self.tool_call_ids: dict[int, str] = {}
        self.usage: Usage | None = None
        self.finish_reason: str | None = None

    def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]:
        if chunk.usage is not None:
            self.usage = _chat_usage(chunk.usage, self.cache_write_30m_priced)
        if not chunk.choices:
            return ()
        choice = chunk.choices[0]
        if choice.finish_reason is not None:
            self.finish_reason = choice.finish_reason
        events: list[ModelEvent] = []
        if choice.delta.content:
            events.append(TextDelta(text=choice.delta.content))
        for call in choice.delta.tool_calls or ():
            if call.index not in self.tool_call_ids:
                self.tool_call_ids[call.index] = call.id or ""
                events.append(
                    ToolCallStart(
                        id=call.id or "",
                        name=(call.function.name or "") if call.function else "",
                    )
                )
            if call.function is not None and call.function.arguments:
                events.append(
                    ToolCallDelta(
                        id=self.tool_call_ids[call.index],
                        partial_json=call.function.arguments,
                    )
                )
        self.yielded = self.yielded or bool(events)
        return tuple(events)

    def finish(self) -> tuple[Usage, Exception | None]:
        """The round's usage and its terminal error. A stream that carried no usage still raises
        that terminal error rather than the missing-usage fault: the engine recovers a truncated
        round on the ModelResponseTruncated class alone."""
        error = (
            ModelResponseTruncated(
                "OpenAI completion truncated at the max_tokens budget (finish_reason=length)"
            )
            if self.finish_reason == "length"
            else None
        )
        if self.usage is None:
            if error is not None:
                raise error
            raise RuntimeError("model stream produced no usage")
        return self.usage, error


class _ResponsesStream:
    def __init__(self, cache_write_30m_priced: bool) -> None:
        self.cache_write_30m_priced = cache_write_30m_priced
        self.yielded = False
        self.tool_call_ids: dict[str, str] = {}
        self.tool_call_arguments: set[str] = set()
        self.reasoning: list[ReasoningItemBlock] = []
        self.usage: Usage | None = None
        self.terminal_error: Exception | None = None

    def accept(self, event: ResponseStreamEvent) -> tuple[ModelEvent, ...]:
        emitted: tuple[ModelEvent, ...] = ()
        match event:
            case ResponseTextDeltaEvent(delta=text):
                emitted = (TextDelta(text=text),)
            case ResponseOutputItemAddedEvent(
                item=ResponseFunctionToolCall(id=item_id, call_id=call_id, name=name)
            ):
                if item_id is None:
                    raise RuntimeError("OpenAI function call has no item id")
                self.tool_call_ids[item_id] = call_id
                emitted = (ToolCallStart(id=call_id, name=name),)
            case ResponseFunctionCallArgumentsDeltaEvent(item_id=item_id, delta=partial_json):
                self.tool_call_arguments.add(item_id)
                emitted = (
                    ToolCallDelta(id=self.tool_call_ids[item_id], partial_json=partial_json),
                )
            case ResponseFunctionCallArgumentsDoneEvent(item_id=item_id, arguments=arguments) if (
                item_id not in self.tool_call_arguments
            ):
                emitted = (ToolCallDelta(id=self.tool_call_ids[item_id], partial_json=arguments),)
            case ResponseOutputItemDoneEvent(item=ResponseReasoningItem() as item):
                self._record_reasoning(item)
            case ResponseRefusalDeltaEvent(delta=refusal):
                self.terminal_error = ModelRefusal(f"OpenAI declined the completion: {refusal}")
            case ResponseCompletedEvent(response=response):
                if response.usage is None:
                    raise RuntimeError("model stream produced no usage")
                self.usage = _responses_usage(response.usage, self.cache_write_30m_priced)
            case ResponseIncompleteEvent(response=response):
                self._record_incomplete(response)
            case ResponseFailedEvent(response=response):
                if response.usage is not None:
                    self.usage = _responses_usage(response.usage, self.cache_write_30m_priced)
                message = response.error.message if response.error else "unknown error"
                self.terminal_error = RuntimeError(f"OpenAI response failed: {message}")
            case ResponseErrorEvent(message=message):
                self.terminal_error = RuntimeError(f"OpenAI response failed: {message}")
        self.yielded = self.yielded or bool(emitted)
        return emitted

    def _record_reasoning(self, item: ResponseReasoningItem) -> None:
        if item.encrypted_content is None:
            raise RuntimeError("OpenAI reasoning item has no encrypted content")
        self.reasoning.append(
            ReasoningItemBlock(
                id=item.id,
                encrypted_content=item.encrypted_content,
                summary=tuple(part.text for part in item.summary),
            )
        )

    def _record_incomplete(self, response: Any) -> None:
        if response.usage is not None:
            self.usage = _responses_usage(response.usage, self.cache_write_30m_priced)
        reason = response.incomplete_details
        if reason is not None and reason.reason == "max_output_tokens":
            self.terminal_error = ModelResponseTruncated(
                "OpenAI response truncated at the max_output_tokens budget"
            )
        elif reason is not None and reason.reason == "content_filter":
            self.terminal_error = ModelRefusal("OpenAI declined the completion (content_filter)")
        else:
            self.terminal_error = RuntimeError("OpenAI returned an incomplete response")

    def finish(self) -> tuple[Usage, Exception | None]:
        """The `_ChatStream.finish` contract on this surface: a truncation or a refusal that
        reported no usage keeps its own class, so the engine recovers the round instead of
        recording an internal fault."""
        if self.usage is None:
            if self.terminal_error is not None:
                raise self.terminal_error
            raise RuntimeError("model stream produced no usage")
        return self.usage, self.terminal_error


@dataclass(frozen=True)
class OpenAIClient:
    """An OpenAI-wire backend for one model. `spec.api_surface` selects the Chat Completions or
    Responses request shape; `spec.reasoning` gates whether reasoning is emitted and whether it
    composes with tools on the chat surface (the #568 fix). `codex` marks a client built for a
    member's ChatGPT account token: that backend serves Responses alone, and the host a credential
    reaches is not a fact the model's spec can carry, so it overrides the declared surface."""

    client: openai.AsyncOpenAI
    spec: ModelSpec
    codex: bool = False

    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self.codex or self.spec.api_surface == "responses":
            return self._complete_responses(request)
        return self._complete_chat(request)

    def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort:
        """The effort this request sends, or None to send no reasoning parameter at all — a model
        that does not reason has nothing to set. `off` is sent as `none`, because on this wire an
        absent parameter is the provider's own default effort: a request that omitted it would
        reason through a `max_tokens` budget the caller sized for the answer alone. A model that
        reasons but whose surface refuses the parameter alongside tools cannot state `off`, so such
        a request raises rather than running at that default."""
        effort = self.spec.wire_reasoning(request.reasoning, request.tools)
        if effort is None:
            if request.reasoning == "off" and self.spec.reasoning.supported:
                raise RuntimeError(
                    f"model {self.spec.id!r} reasons and refuses a reasoning parameter alongside "
                    "tools, so a request carrying tools cannot switch its reasoning off"
                )
            return None
        if effort == "auto":
            return None
        if effort == "off":
            return REASONING_OFF_EFFORT
        return effort

    def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        create_kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": openai_messages(request.system, request.messages),
            "max_completion_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        effort = self._reasoning_effort(request)
        if effort is not None:
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
        and re-raising the fault) — both only until visible output is yielded; the same fault after
        that raises ModelStreamInterrupted so the engine discards the partial round and re-runs it
        once. An error frame injected into the live SSE stream surfaces as the exact APIError class
        and interrupts the round the same way, since the stream is already 200 and the fault is the
        upstream's, not the request's. A 401 is the provider refusing the key this spec resolved:
        it raises that spec's credential fault instead of the SDK's auth error — the round says
        which slot or env to replace, and the verdict is deterministic per key, so nothing retries
        it.
        finish_reason=length is a truncated completion and raises ModelResponseTruncated.
        finish_reason=tool_calls is a normal stop. An empty completion (no event,
        finish_reason=stop) is a retryable provider failure, re-issued up to
        MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result for the turn loop's nudge —
        a tool-call-only response has yielded and never degrades.
        """
        retry = _OpenAIRetry(self.spec, request.model, delay=INITIAL_RETRY_DELAY_SECONDS)
        empty_attempt = 0
        while True:
            state = _ChatStream(bool(self.spec.price.cache_write_30m))
            try:
                stream = await self.client.chat.completions.create(**self._chat_kwargs(request))
                stream_started = False
                async for chunk in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    for event in state.accept(chunk):
                        yield event
            except STREAM_TRANSPORT_ERRORS as error:
                if state.usage is not None:
                    yield state.usage
                retry = await retry.transport(error, state.yielded)
                continue
            except STREAM_STATUS_ERRORS as error:
                if state.usage is not None:
                    yield state.usage
                retry = await retry.status(error, state.yielded)
                continue
            except openai.APIError as error:
                if type(error) is not openai.APIError:
                    raise
                if state.usage is not None:
                    yield state.usage
                raise ModelStreamInterrupted(
                    "stream_error",
                    f"OpenAI injected an error into the stream: {error}",
                ) from error
            usage, terminal_error = state.finish()
            if terminal_error is not None:
                yield usage
                raise terminal_error
            if (
                not state.yielded
                and state.finish_reason == "stop"
                and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES
            ):
                empty_attempt += 1
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                yield usage
                continue
            yield usage
            return

    async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """The Responses-API twin of `_complete_chat` for a model whose spec sets
        `api_surface="responses"` — same retry, truncation, refusal, and empty-completion contract,
        translated to the Responses streaming events. Reasoning is gated by the spec exactly as the
        chat path is: an unsupported reasoning or reasoning-with-tools combination never emits the
        reasoning parameter, and a request that asks for `off` states it as effort `none`.

        The round's reasoning items are collected whole off their done events — the event that
        carries the encrypted body, which the added event does not — and yielded once the stream
        closes, keeping their order among themselves and arriving just ahead of the Usage, so the
        engine can send them back on the assistant message that carries this round's function
        calls: what the provider asks for so the model resumes its reasoning when the results
        return. Held until the stream closes because reasoning is not live output and a re-issued
        attempt must not deliver the abandoned attempt's items, and reasoning alone never counts as
        having yielded: an answerless round stays an empty completion and is retried."""
        effort = self._reasoning_effort(request)
        retry = _OpenAIRetry(self.spec, request.model, delay=INITIAL_RETRY_DELAY_SECONDS)
        empty_attempt = 0
        while True:
            state = _ResponsesStream(bool(self.spec.price.cache_write_30m))
            try:
                stream = await self.client.responses.create(
                    **responses_request(request, effort, self.codex)
                )
                stream_started = False
                async for event in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    for emitted in state.accept(event):
                        yield emitted
            except STREAM_TRANSPORT_ERRORS as error:
                if state.usage is not None:
                    yield state.usage
                retry = await retry.transport(error, state.yielded)
                continue
            except STREAM_STATUS_ERRORS as error:
                if state.usage is not None:
                    yield state.usage
                retry = await retry.status(error, state.yielded)
                continue
            except openai.APIError as error:
                if type(error) is not openai.APIError:
                    raise
                if state.usage is not None:
                    yield state.usage
                raise ModelStreamInterrupted(
                    "stream_error",
                    f"OpenAI injected an error into the stream: {error}",
                ) from error
            usage, terminal_error = state.finish()
            if terminal_error is not None:
                yield usage
                raise terminal_error
            if not state.yielded and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES:
                empty_attempt += 1
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                yield usage
                continue
            for block in state.reasoning:
                yield block
            yield usage
            return
