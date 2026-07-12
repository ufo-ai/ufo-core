"""OpenRouter model-provider extension: the OpenAI-wire router as a `ModelClient` backend.

Core ships direct Anthropic + OpenAI clients; OpenRouter is a router over many upstreams, so per
spec it is an extension, never core. It speaks the OpenAI Chat Completions wire against
`openrouter.ai/api/v1`, so it reuses the SDK's `openai_messages` translation and `openai_sdk_client`
factory and adds only what is OpenRouter's own: an id->slug projection, a reasoning-effort budget on
`extra_body`, and a dead-provider re-route that excludes an upstream returning an empty completion.
The manifest contributes one catch-all model provider — core's direct clients claim bare
Anthropic/OpenAI ids first, so this serves everything else — plus the rates for the slugs it
pins."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import openai
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage

from ufo.sdk.manifest import Manifest, ModelProviderSpec
from ufo.sdk.models import (
    ModelEvent,
    ModelPrice,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    Usage,
    openai_messages,
    openai_sdk_client,
)

NAME = "openrouter"
VERSION = "0.1.0"
PROVIDER_NAME = "openrouter"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_KEY_SLOT = "openrouter_api_key"
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")

MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3

# OpenRouter slug-pinned rates in micro-USD per million tokens (openrouter.ai public prices).
OPENROUTER_PRICES: tuple[tuple[str, ModelPrice], ...] = (
    (
        "google/gemini-2.5-pro",
        ModelPrice(input=1_000_000, output=10_000_000, cache_read=0, cache_write=0),
    ),
    ("z-ai/glm-5.2", ModelPrice(input=1_000_000, output=3_000_000, cache_read=0, cache_write=0)),
)


def openrouter_slug(model: str) -> str:
    """Project a model id onto an OpenRouter `provider/model` slug. An id that already carries a `/`
    is a slug and passes through; a bare OpenAI/Anthropic id gains its provider prefix; any other
    bare id passes through verbatim for OpenRouter to route."""
    if "/" in model:
        return model
    if model.startswith(OPENAI_MODEL_PREFIXES):
        return f"openai/{model}"
    if model.startswith("claude-"):
        return f"anthropic/{model}"
    return model


def _chunk_provider(chunk: ChatCompletionChunk) -> str | None:
    """The upstream OpenRouter routed to, from its `provider` response extension, so a dead
    completion's provider can be excluded from the re-route."""
    extra = chunk.model_extra
    provider = extra.get("provider") if extra else None
    return str(provider) if provider else None


def _usage_of(usage: CompletionUsage) -> Usage:
    details = usage.prompt_tokens_details
    cached_tokens = (details.cached_tokens or 0) if details is not None else 0
    if cached_tokens > usage.prompt_tokens:
        raise ValueError("cached prompt tokens exceed total prompt tokens")
    return Usage(
        input_tokens=usage.prompt_tokens - cached_tokens,
        output_tokens=usage.completion_tokens,
        cache_read_tokens=cached_tokens,
    )


@dataclass(frozen=True)
class OpenRouterModelClient:
    """The OpenRouter backend behind the `ModelClient` protocol: it streams ModelEvents from the
    Chat Completions wire, ending with one Usage, exactly as core's OpenAIClient does, and adds
    OpenRouter's own behavior. 429/5xx retry with retry-after-aware backoff but only until the first
    event yields; finish_reason=length raises ModelResponseTruncated. A normal completion that
    returned no text and no tool calls is a dead upstream — the client re-issues excluding that
    provider up to MAX_EMPTY_PROVIDER_RETRIES, then degrades to the empty result for the turn loop's
    nudge. The request's `reasoning` effort rides `extra_body` as the thinking budget OpenRouter
    derives from max_tokens; `off` omits it."""

    client: openai.AsyncOpenAI

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        ignore_providers: set[str] = set()
        while True:
            yielded = False
            tool_call_ids: dict[int, str] = {}
            usage: Usage | None = None
            finish_reason: str | None = None
            provider: str | None = None
            try:
                stream = await self.client.chat.completions.create(
                    **self._create_kwargs(request, frozenset(ignore_providers))
                )
                async for chunk in stream:
                    provider = _chunk_provider(chunk) or provider
                    if chunk.usage is not None:
                        usage = _usage_of(chunk.usage)
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
                    "OpenRouter completion truncated at the max_tokens budget "
                    "(finish_reason=length)"
                )
            if usage is None:
                raise RuntimeError("model stream produced no usage")
            dead = finish_reason == "stop" and not yielded
            if dead and provider is not None and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES:
                empty_attempt += 1
                ignore_providers.add(provider)
                continue
            yield usage
            return

    def _create_kwargs(
        self, request: ModelRequest, ignore_providers: frozenset[str]
    ) -> dict[str, Any]:
        extra_body: dict[str, Any] = {}
        if request.reasoning != "off":
            extra_body["reasoning"] = {"effort": request.reasoning}
        if ignore_providers:
            extra_body["provider"] = {"ignore": sorted(ignore_providers)}
        kwargs: dict[str, Any] = {
            "model": openrouter_slug(request.model),
            "messages": openai_messages(request.system, request.messages),
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": extra_body,
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        return kwargs


def _model_client(model: str, key: str) -> OpenRouterModelClient:
    return OpenRouterModelClient(client=openai_sdk_client(key, base_url=OPENROUTER_BASE_URL))


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        models=(
            ModelProviderSpec(
                name=PROVIDER_NAME,
                matches=lambda model: True,
                client=_model_client,
                key_slot=OPENROUTER_KEY_SLOT,
                key_env=OPENROUTER_API_KEY_ENV,
                prices=OPENROUTER_PRICES,
            ),
        ),
    )
