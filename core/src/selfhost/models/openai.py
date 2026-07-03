"""OpenAI Chat Completions API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai

from selfhost.models import ModelEvent, ModelRequest, TextDelta
from selfhost.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0


def openai_sdk_client(api_key: str) -> openai.AsyncOpenAI:
    """SDK client with its own retries disabled: the retry policy lives in OpenAIClient."""
    return openai.AsyncOpenAI(api_key=api_key, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class OpenAIClient:
    client: openai.AsyncOpenAI

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

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        stream = await self.client.chat.completions.create(
            model=request.model,
            messages=[
                {"role": "system", "content": request.system},
                *[{"role": m.role, "content": m.content} for m in request.messages],
            ],
            max_tokens=request.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        usage: Usage | None = None
        async for chunk in stream:
            if chunk.usage is not None:
                details = chunk.usage.prompt_tokens_details
                usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    cache_read_tokens=(details.cached_tokens or 0) if details is not None else 0,
                )
            if not chunk.choices:
                continue
            text = chunk.choices[0].delta.content
            if text:
                yield TextDelta(text=text)
        if usage is None:
            raise RuntimeError("model stream produced no usage")
        yield usage
