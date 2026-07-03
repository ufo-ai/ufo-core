"""Anthropic Messages API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anthropic

from selfhost.models import ModelEvent, ModelRequest, ModelResponseTruncated, TextDelta
from selfhost.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3


def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic:
    """SDK client with its own retries disabled: the retry policy lives in AnthropicClient."""
    return anthropic.AsyncAnthropic(
        api_key=api_key, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS
    )


@dataclass(frozen=True)
class AnthropicClient:
    client: anthropic.AsyncAnthropic

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield TextDelta events then exactly one Usage as the final event.

        429/5xx responses retry with retry-after-aware exponential backoff, but only until the
        first event is yielded; any failure after that raises immediately. stop_reason=max_tokens
        is a truncated completion and raises ModelResponseTruncated. An empty completion (no text,
        stop_reason=end_turn) is a retryable provider failure, re-issued up to
        MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result for the turn loop's nudge.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            input_tokens = 0
            cache_read_tokens = 0
            cache_write_tokens = 0
            output_tokens: int | None = None
            stop_reason: str | None = None
            try:
                stream = await self.client.messages.create(
                    model=request.model,
                    system=request.system,
                    messages=[{"role": m.role, "content": m.content} for m in request.messages],
                    max_tokens=request.max_tokens,
                    stream=True,
                )
                async for event in stream:
                    match event:
                        case anthropic.types.RawMessageStartEvent(message=message):
                            input_tokens = message.usage.input_tokens
                            cache_read_tokens = message.usage.cache_read_input_tokens or 0
                            cache_write_tokens = message.usage.cache_creation_input_tokens or 0
                        case anthropic.types.RawContentBlockDeltaEvent(
                            delta=anthropic.types.TextDelta(text=text)
                        ):
                            yielded = True
                            yield TextDelta(text=text)
                        case anthropic.types.RawMessageDeltaEvent(delta=delta, usage=usage):
                            output_tokens = usage.output_tokens
                            stop_reason = delta.stop_reason
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
