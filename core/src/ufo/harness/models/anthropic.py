"""Anthropic Messages API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from ufo.harness.models.interface import (
    ContentBlock,
    ImageBlock,
    ImageSource,
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
from ufo.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
ANTHROPIC_OAUTH_TOKEN_PREFIX = "sk-ant-oat"
ANTHROPIC_OAUTH_BETA = "claude-code-20250219,oauth-2025-04-20"
OAUTH_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3
STABLE_PREFIX_CACHE_TTL = "1h"
STREAM_TRANSPORT_ERRORS = (
    anthropic.APITimeoutError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
STREAM_STATUS_ERRORS = (anthropic.APIStatusError,)


def anthropic_sdk_client(credential: str) -> anthropic.AsyncAnthropic:
    """SDK client with its own retries disabled: the retry policy lives in AnthropicClient.

    A member who signed in with their Anthropic account holds an OAuth access token rather than an
    API key, and the two authenticate differently on the same wire: a token is a bearer under the
    OAuth beta, a key is `x-api-key`. The prefix is what tells them apart, so one slot carries
    whichever the member connected and the branch is a wire fact rather than a second setting."""
    if is_oauth_credential(credential):
        return anthropic.AsyncAnthropic(
            auth_token=credential,
            default_headers={"anthropic-beta": ANTHROPIC_OAUTH_BETA},
            max_retries=0,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
    return anthropic.AsyncAnthropic(
        api_key=credential, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS
    )


def is_oauth_credential(credential: str) -> bool:
    """Whether this credential is a member's OAuth access token rather than an API key."""
    return credential.startswith(ANTHROPIC_OAUTH_TOKEN_PREFIX)


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
    """Canonical content as Anthropic content blocks. A reasoning item is the OpenAI wire's shape of
    the same idea and is dropped: only the provider that produced a round's reasoning can be handed
    it back, and neither provider parses the other's."""
    if isinstance(content, str):
        return content
    blocks: list[dict[str, object]] = []
    for block in content:
        match block:
            case ThinkingBlock(thinking=thinking, signature=signature):
                blocks.append({"type": "thinking", "thinking": thinking, "signature": signature})
            case RedactedThinkingBlock(data=data):
                blocks.append({"type": "redacted_thinking", "data": data})
            case ReasoningItemBlock():
                continue
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
    spec: ModelSpec
    oauth: bool = False

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield text and tool-call events then exactly one Usage as the final event. The round's
        reasoning blocks are yielded whole — each thinking block's text and signature together, and
        every redacted block the provider encrypted — once the stream closes, in the provider's own
        block order and just ahead of the Usage, so the engine can echo the sequence back verbatim
        on the round's assistant message, which the API requires when a reasoning round's tool
        results come back on the same model. A missing or reordered block is a modified sequence and
        is rejected there, so both kinds ride one ordered list. Held until the stream closes because
        reasoning is not live output — no surface streams it — and handing it over early would close
        both retry paths for a round that has yet to answer: a re-issue would deliver the abandoned
        attempt's reasoning alongside the new attempt's.

        Every provider failure except a deterministic 4xx client error (400-499 other than 429)
        retries with retry-after-aware exponential backoff, and request timeouts or a dropped
        connection retry on the same backoff and shared attempt budget (each retry logged,
        exhaustion logged and re-raising the fault) — both only until visible output is yielded;
        any failure after that raises immediately. A streamed request surfaces its timeout or a
        peer disconnect as a raw httpx error during iteration (the SDK wraps only the create
        call), so the retry catches both. A mid-stream
        error event (overloaded, or a transient api_error) arrives on the already-200 stream
        response and so carries status_code 200 — keying retry off the single non-retryable case
        (a deterministic 4xx) catches it where a 5xx allowlist would let a 200-coded fault through.
        A 401 is the provider refusing the key this spec resolved, so it raises that spec's
        credential fault instead of the SDK's auth error — the round says which slot or env to
        replace, and the verdict is deterministic per key, so nothing retries it.
        stop_reason=max_tokens is a truncated completion and raises ModelResponseTruncated;
        stop_reason=refusal raises ModelRefusal (deterministic per request — never retried, never
        an empty success). stop_reason=tool_use is a normal stop. An empty completion (no text and
        no tool call — reasoning alone is not an answer — with stop_reason=end_turn) is a retryable
        provider failure, re-issued up to MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty
        result for the turn loop's nudge — a tool-call-only response has yielded and never
        degrades.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_use_ids: dict[int, str] = {}
            thinking_parts: dict[int, list[str]] = {}
            thinking_signatures: dict[int, str] = {}
            reasoning: list[ThinkingBlock | RedactedThinkingBlock] = []
            input_tokens = 0
            cache_read_tokens = 0
            cache_write_5m_tokens = 0
            cache_write_1h_tokens = 0
            output_tokens: int | None = None
            stop_reason: str | None = None
            system_cache = {"type": "ephemeral", "ttl": STABLE_PREFIX_CACHE_TTL}
            conversation_cache = {"type": "ephemeral", "ttl": request.conversation_cache_ttl}
            create_kwargs: dict[str, Any] = {
                "model": request.model,
                "system": [
                    *([{"type": "text", "text": OAUTH_SYSTEM_PREFIX}] if self.oauth else []),
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": system_cache,
                    },
                ],
                "messages": [
                    {"role": m.role, "content": anthropic_content(m.content)}
                    for m in trim_images(request.messages)
                ],
                "max_tokens": request.max_tokens,
                "stream": True,
                "cache_control": conversation_cache,
            }
            effort = self.spec.wire_reasoning(request.reasoning, request.tools)
            if effort == "off" and self.spec.reasoning.default_on:
                create_kwargs["thinking"] = {"type": "disabled"}
            elif effort not in (None, "off"):
                create_kwargs["thinking"] = {"type": "adaptive"}
                if effort != "auto":
                    create_kwargs["output_config"] = {"effort": effort}
            if request.tools:
                create_kwargs["tools"] = [
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in request.tools
                ]
                create_kwargs["tools"][-1]["cache_control"] = system_cache
                create_kwargs["tool_choice"] = (
                    {"type": "auto", "disable_parallel_tool_use": False}
                    if request.tool_choice is None
                    else {
                        "type": "tool",
                        "name": request.tool_choice,
                        "disable_parallel_tool_use": True,
                    }
                )
            try:
                stream = await self.client.messages.create(**create_kwargs)
                stream_started = False
                async for event in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    match event:
                        case anthropic.types.RawMessageStartEvent(message=message):
                            input_tokens = message.usage.input_tokens
                            cache_read_tokens = message.usage.cache_read_input_tokens or 0
                            creation = message.usage.cache_creation
                            if creation is None:
                                cache_creation_tokens = (
                                    message.usage.cache_creation_input_tokens or 0
                                )
                                cache_write_1h_tokens = cache_creation_tokens
                            else:
                                cache_write_5m_tokens = creation.ephemeral_5m_input_tokens
                                cache_write_1h_tokens = creation.ephemeral_1h_input_tokens
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
                        case anthropic.types.RawContentBlockStartEvent(
                            content_block=anthropic.types.ThinkingBlock(
                                thinking=initial, signature=signature
                            ),
                            index=index,
                        ):
                            thinking_parts[index] = [initial]
                            thinking_signatures[index] = signature
                        case anthropic.types.RawContentBlockStartEvent(
                            content_block=anthropic.types.RedactedThinkingBlock(data=data)
                        ):
                            reasoning.append(RedactedThinkingBlock(data=data))
                        case anthropic.types.RawContentBlockDeltaEvent(
                            delta=anthropic.types.ThinkingDelta(thinking=part), index=index
                        ):
                            thinking_parts[index].append(part)
                        case anthropic.types.RawContentBlockDeltaEvent(
                            delta=anthropic.types.SignatureDelta(signature=signature), index=index
                        ):
                            thinking_signatures[index] = signature
                        case anthropic.types.RawContentBlockStopEvent(index=index) if (
                            index in thinking_parts
                        ):
                            signature = thinking_signatures.pop(index)
                            if not signature:
                                raise RuntimeError(
                                    "Anthropic thinking block closed without a signature"
                                )
                            reasoning.append(
                                ThinkingBlock(
                                    thinking="".join(thinking_parts.pop(index)),
                                    signature=signature,
                                )
                            )
                        case anthropic.types.RawMessageDeltaEvent(delta=delta, usage=usage):
                            output_tokens = usage.output_tokens
                            stop_reason = delta.stop_reason
            except STREAM_TRANSPORT_ERRORS as error:
                if (
                    input_tokens
                    or cache_read_tokens
                    or cache_write_5m_tokens
                    or cache_write_1h_tokens
                ):
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or 0,
                        cache_read_tokens=cache_read_tokens,
                        cache_write_5m_tokens=cache_write_5m_tokens,
                        cache_write_1h_tokens=cache_write_1h_tokens,
                    )
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
                if (
                    input_tokens
                    or cache_read_tokens
                    or cache_write_5m_tokens
                    or cache_write_1h_tokens
                ):
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or 0,
                        cache_read_tokens=cache_read_tokens,
                        cache_write_5m_tokens=cache_write_5m_tokens,
                        cache_write_1h_tokens=cache_write_1h_tokens,
                    )
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
                deterministic_client_error = (
                    400 <= error.status_code < 500 and error.status_code != 429
                )
                if yielded or deterministic_client_error or attempt > MAX_PROVIDER_RETRIES:
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
            if stop_reason == "max_tokens":
                yield Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or 0,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_5m_tokens=cache_write_5m_tokens,
                    cache_write_1h_tokens=cache_write_1h_tokens,
                )
                raise ModelResponseTruncated(
                    "Anthropic completion truncated at the max_tokens budget "
                    "(stop_reason=max_tokens)"
                )
            if stop_reason == "refusal":
                yield Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or 0,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_5m_tokens=cache_write_5m_tokens,
                    cache_write_1h_tokens=cache_write_1h_tokens,
                )
                raise ModelRefusal("Anthropic declined the completion (stop_reason=refusal)")
            if output_tokens is None:
                if (
                    input_tokens
                    or cache_read_tokens
                    or cache_write_5m_tokens
                    or cache_write_1h_tokens
                ):
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=0,
                        cache_read_tokens=cache_read_tokens,
                        cache_write_5m_tokens=cache_write_5m_tokens,
                        cache_write_1h_tokens=cache_write_1h_tokens,
                    )
                raise RuntimeError("model stream produced no usage")
            if (
                not yielded
                and stop_reason == "end_turn"
                and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES
            ):
                empty_attempt += 1
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                yield Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_5m_tokens=cache_write_5m_tokens,
                    cache_write_1h_tokens=cache_write_1h_tokens,
                )
                continue
            for block in reasoning:
                yield block
            yield Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_5m_tokens=cache_write_5m_tokens,
                cache_write_1h_tokens=cache_write_1h_tokens,
            )
            return
