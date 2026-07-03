"""OpenAI Chat Completions API client streaming ModelEvents."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai

from selfhost.models import ModelEvent, ModelRequest, ModelResponseTruncated, TextDelta
from selfhost.schema.records import Usage

PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3


def openai_sdk_client(api_key: str) -> openai.AsyncOpenAI:
    """SDK client with its own retries disabled: the retry policy lives in OpenAIClient."""
    return openai.AsyncOpenAI(api_key=api_key, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class OpenAIClient:
    client: openai.AsyncOpenAI

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield TextDelta events then exactly one Usage as the final event.

        429/5xx responses retry with retry-after-aware exponential backoff, but only until the
        first event is yielded; any failure after that raises immediately. finish_reason=length is
        a truncated completion and raises ModelResponseTruncated. An empty completion (no text,
        finish_reason=stop) is a retryable provider failure, re-issued up to
        MAX_EMPTY_PROVIDER_RETRIES before degrading to the empty result for the turn loop's nudge.
        """
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            usage: Usage | None = None
            finish_reason: str | None = None
            try:
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
                    if choice.delta.content:
                        yielded = True
                        yield TextDelta(text=choice.delta.content)
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
