"""Anthropic Messages API client streaming ModelEvents."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any

import anthropic
import httpx

from ufo.harness.models.interface import (
    PROVIDER_PARK_THRESHOLD_SECONDS,
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
from ufo.harness.models.spec import (
    INITIAL_RETRY_DELAY_SECONDS,
    KEY_REJECTED_STATUS,
    MAX_PROVIDER_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    RATE_LIMITED_STATUS,
    ModelSpec,
)
from ufo.harness.o11y import emit_metric, log
from ufo.harness.rounds import ModelRetryAfter, ModelStreamInterrupted
from ufo.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
ANTHROPIC_OAUTH_TOKEN_PREFIX = "sk-ant-oat"
ANTHROPIC_OAUTH_BETA = "claude-code-20250219,oauth-2025-04-20"
OAUTH_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
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


TOP_LEVEL_COMBINATORS = ("oneOf", "anyOf", "allOf")


def anthropic_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """One tool's input schema as this provider accepts it. Anthropic refuses a top-level `oneOf`,
    `anyOf` or `allOf` outright — the whole request fails, so one such tool takes down every turn
    that offers it, not only the calls that would have used it. The combinator is a hint to the
    model; the rule it states is enforced by the handler's own validation, which answers the model
    with the same refusal it would have read from the schema."""
    if not any(key in schema for key in TOP_LEVEL_COMBINATORS):
        return schema
    return {key: value for key, value in schema.items() if key not in TOP_LEVEL_COMBINATORS}


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
class _AnthropicRetry:
    spec: ModelSpec
    model: str
    defer_long_retry: bool
    attempt: int = 0
    delay: float = INITIAL_RETRY_DELAY_SECONDS

    async def transport(self, error: Exception, yielded: bool) -> _AnthropicRetry:
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
                    f"Anthropic stream died mid-round ({type(error).__name__}): {error}",
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

    async def status(self, error: anthropic.APIStatusError, yielded: bool) -> _AnthropicRetry:
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
        deterministic_client_error = (
            400 <= error.status_code < 500 and error.status_code != RATE_LIMITED_STATUS
        )
        if yielded or deterministic_client_error or attempt > MAX_PROVIDER_RETRIES:
            log(
                "model.provider_status_error",
                provider=self.spec.provider,
                model=self.model,
                attempts=attempt,
                status_code=error.status_code,
            )
            if yielded and not deterministic_client_error:
                raise ModelStreamInterrupted(
                    "stream_error",
                    f"Anthropic errored the stream mid-round ({error.status_code}): {error}",
                ) from error
            if error.status_code == RATE_LIMITED_STATUS:
                raise self.spec.rate_limited() from error
            raise error
        header = error.response.headers.get("retry-after")
        try:
            wait = max(float(header), 0.0) if header is not None else self.delay
        except ValueError:
            wait = self.delay
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
        if (
            self.defer_long_retry
            and error.status_code == 429
            and wait > PROVIDER_PARK_THRESHOLD_SECONDS
        ):
            raise ModelRetryAfter(wait) from error
        await asyncio.sleep(wait)
        return replace(
            self,
            attempt=attempt,
            delay=min(self.delay * 2, MAX_RETRY_DELAY_SECONDS),
        )


class _AnthropicStream:
    def __init__(self) -> None:
        self.yielded = False
        self.tool_use_ids: dict[int, str] = {}
        self.thinking_parts: dict[int, list[str]] = {}
        self.thinking_signatures: dict[int, str] = {}
        self.reasoning: list[ThinkingBlock | RedactedThinkingBlock] = []
        self.input_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_5m_tokens = 0
        self.cache_write_1h_tokens = 0
        self.output_tokens: int | None = None
        self.stop_reason: str | None = None

    def accept(self, event: object) -> tuple[ModelEvent, ...]:
        emitted: tuple[ModelEvent, ...] = ()
        match event:
            case anthropic.types.RawMessageStartEvent(message=message):
                self._record_input_usage(message.usage)
            case anthropic.types.RawContentBlockStartEvent(
                content_block=anthropic.types.ToolUseBlock(id=block_id, name=name), index=index
            ):
                self.tool_use_ids[index] = block_id
                emitted = (ToolCallStart(id=block_id, name=name),)
            case anthropic.types.RawContentBlockDeltaEvent(
                delta=anthropic.types.TextDelta(text=text)
            ):
                emitted = (TextDelta(text=text),)
            case anthropic.types.RawContentBlockDeltaEvent(
                delta=anthropic.types.InputJSONDelta(partial_json=partial_json), index=index
            ):
                emitted = (ToolCallDelta(id=self.tool_use_ids[index], partial_json=partial_json),)
            case anthropic.types.RawContentBlockStartEvent(
                content_block=anthropic.types.ThinkingBlock(thinking=initial, signature=signature),
                index=index,
            ):
                self.thinking_parts[index] = [initial]
                self.thinking_signatures[index] = signature
            case anthropic.types.RawContentBlockStartEvent(
                content_block=anthropic.types.RedactedThinkingBlock(data=data)
            ):
                self.reasoning.append(RedactedThinkingBlock(data=data))
            case anthropic.types.RawContentBlockDeltaEvent(
                delta=anthropic.types.ThinkingDelta(thinking=part), index=index
            ):
                self.thinking_parts[index].append(part)
            case anthropic.types.RawContentBlockDeltaEvent(
                delta=anthropic.types.SignatureDelta(signature=signature), index=index
            ):
                self.thinking_signatures[index] = signature
            case anthropic.types.RawContentBlockStopEvent(index=index) if (
                index in self.thinking_parts
            ):
                self._close_thinking(index)
            case anthropic.types.RawMessageDeltaEvent(delta=delta, usage=usage):
                self.output_tokens = usage.output_tokens
                self.stop_reason = delta.stop_reason
        self.yielded = self.yielded or bool(emitted)
        return emitted

    def _record_input_usage(self, usage: Any) -> None:
        self.input_tokens = usage.input_tokens
        self.cache_read_tokens = usage.cache_read_input_tokens or 0
        creation = usage.cache_creation
        if creation is None:
            self.cache_write_1h_tokens = usage.cache_creation_input_tokens or 0
            return
        self.cache_write_5m_tokens = creation.ephemeral_5m_input_tokens
        self.cache_write_1h_tokens = creation.ephemeral_1h_input_tokens

    def _close_thinking(self, index: int) -> None:
        signature = self.thinking_signatures.pop(index)
        if not signature:
            raise RuntimeError("Anthropic thinking block closed without a signature")
        self.reasoning.append(
            ThinkingBlock(
                thinking="".join(self.thinking_parts.pop(index)),
                signature=signature,
            )
        )

    def has_usage(self) -> bool:
        return bool(
            self.input_tokens
            or self.cache_read_tokens
            or self.cache_write_5m_tokens
            or self.cache_write_1h_tokens
        )

    def usage(self) -> Usage:
        return Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens or 0,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_5m_tokens=self.cache_write_5m_tokens,
            cache_write_1h_tokens=self.cache_write_1h_tokens,
        )


@dataclass(frozen=True)
class AnthropicClient:
    client: anthropic.AsyncAnthropic
    spec: ModelSpec
    oauth: bool = False

    def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        system_cache = {"type": "ephemeral", "ttl": STABLE_PREFIX_CACHE_TTL}
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
                {"role": message.role, "content": anthropic_content(message.content)}
                for message in trim_images(request.messages)
            ],
            "max_tokens": request.max_tokens,
            "stream": True,
            "cache_control": {"type": "ephemeral", "ttl": request.conversation_cache_ttl},
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
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": anthropic_tool_schema(tool.input_schema),
                }
                for tool in request.tools
            ]
            create_kwargs["tools"][-1]["cache_control"] = system_cache
            if request.tool_choice is None:
                create_kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": False}
            elif self.spec.forced_tool_choice:
                create_kwargs["tool_choice"] = {
                    "type": "tool",
                    "name": request.tool_choice,
                    "disable_parallel_tool_use": True,
                }
            elif len(request.tools) == 1:
                create_kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
            else:
                raise ValueError(
                    f"model {self.spec.id!r} cannot force tool_choice {request.tool_choice!r} "
                    "beside other tools"
                )
        return create_kwargs

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
        the same fault after that raises ModelStreamInterrupted so the engine discards the partial
        round and re-runs it once. A streamed request surfaces its timeout or a
        peer disconnect as a raw httpx error during iteration (the SDK wraps only the create
        call), so the retry catches both. A mid-stream
        error event (overloaded, or a transient api_error) arrives on the already-200 stream
        response and so carries status_code 200 — keying retry off the single non-retryable case
        (a deterministic 4xx) catches it where a 5xx allowlist would let a 200-coded fault through.
        A 429 that outlives the retry budget raises that spec's rate-limit fault instead of the
        SDK's status error, so a caller holding a second account of the member's can move the work
        onto it.
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
        retry = _AnthropicRetry(
            self.spec,
            request.model,
            request.defer_long_retry,
            delay=INITIAL_RETRY_DELAY_SECONDS,
        )
        empty_attempt = 0
        while True:
            state = _AnthropicStream()
            try:
                stream = await self.client.messages.create(**self._request_kwargs(request))
                stream_started = False
                async for event in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    for emitted in state.accept(event):
                        yield emitted
            except STREAM_TRANSPORT_ERRORS as error:
                if state.has_usage():
                    yield state.usage()
                retry = await retry.transport(error, state.yielded)
                continue
            except STREAM_STATUS_ERRORS as error:
                if state.has_usage():
                    yield state.usage()
                retry = await retry.status(error, state.yielded)
                continue
            if state.stop_reason == "max_tokens":
                yield state.usage()
                raise ModelResponseTruncated(
                    "Anthropic completion truncated at the max_tokens budget "
                    "(stop_reason=max_tokens)"
                )
            if state.stop_reason == "refusal":
                yield state.usage()
                raise ModelRefusal("Anthropic declined the completion (stop_reason=refusal)")
            if state.output_tokens is None:
                if state.has_usage():
                    yield state.usage()
                raise RuntimeError("model stream produced no usage")
            if (
                not state.yielded
                and state.stop_reason == "end_turn"
                and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES
            ):
                empty_attempt += 1
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=request.model,
                    kind="empty",
                )
                yield state.usage()
                continue
            for block in state.reasoning:
                yield block
            yield state.usage()
            return
