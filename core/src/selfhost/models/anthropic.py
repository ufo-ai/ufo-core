"""Anthropic Messages API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anthropic

from selfhost.models import ModelEvent, ModelRequest, TextDelta
from selfhost.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0


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

        429/5xx responses retry with retry-after-aware exponential backoff, but only
        until the first event is yielded; any failure after that raises immediately.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        while True:
            yielded = False
            try:
                async for event in self._stream(request):
                    yielded = True
                    yield event
                return
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

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        stream = await self.client.messages.create(
            model=request.model,
            system=request.system,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            max_tokens=request.max_tokens,
            stream=True,
        )
        input_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        output_tokens: int | None = None
        async for event in stream:
            match event:
                case anthropic.types.RawMessageStartEvent(message=message):
                    input_tokens = message.usage.input_tokens
                    cache_read_tokens = message.usage.cache_read_input_tokens or 0
                    cache_write_tokens = message.usage.cache_creation_input_tokens or 0
                case anthropic.types.RawContentBlockDeltaEvent(
                    delta=anthropic.types.TextDelta(text=text)
                ):
                    yield TextDelta(text=text)
                case anthropic.types.RawMessageDeltaEvent(usage=usage):
                    output_tokens = usage.output_tokens
        if output_tokens is None:
            raise RuntimeError("model stream produced no usage")
        yield Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
