"""OpenRouter model-provider extension: the OpenAI-wire router as a `ModelClient` backend, plus
image and video generation as tools.

Core ships direct Anthropic + OpenAI clients; OpenRouter is a router over many upstreams, so per
spec it is an extension, never core. It speaks the OpenAI Chat Completions wire against
`openrouter.ai/api/v1`, so it uses the SDK's message translation and client factory. It owns the
id-to-slug projection, reasoning budget, dead-provider reroute, and the text envelope for
schema-reference JSON that OpenRouter rejects in Google tool-result messages.
The manifest enumerates one complete `ModelSpec` per slug it offers — price, cutoff, context window,
reasoning — so the registry serves those ids exactly like any other, with no catch-all router.

Image and video models are not in that registry: `ModelClient.complete` yields text, tool calls and
token usage, and a registered spec is a brain an agent can be pinned to, so they are instead a
`generate_image` tool over OpenRouter's dedicated Image API and a `generate_video` tool over its
asynchronous Video API, both on the same key and bill. Each prices its own call — the unit is an
image or an output second, not a token — and books it onto the turn through
`ToolContext.meter_images` and `ToolContext.meter_videos`, the ledger's `images` and `videos`
dimensions."""

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args

import httpx
import openai
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.sdk.accounting import MICRO_USD_PER_USD
from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo.sdk.models import (
    OPENAI_TOOL_ERROR_PREFIX,
    Message,
    ModelEvent,
    ModelPrice,
    ModelRequest,
    ModelResponseTruncated,
    ModelSpec,
    ModelStreamInterrupted,
    ModelStreamStart,
    ReasoningSupport,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    Usage,
    omit_images,
    openai_messages,
    openai_sdk_client,
)
from ufo.sdk.o11y import emit_metric, log
from ufo.sdk.objects import ARTIFACT_KIND
from ufo.sdk.tools import (
    ImageContent,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
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
GEMINI_ABORT_RETRY_MODEL = "google/gemini-3.7-flash"
GEMINI_ABORT_ERROR = "The operation was aborted"
JSON_REFERENCE_KEYS = frozenset({"$ref", "$dynamicRef"})
GENERATION_PATH = "/generation"
GENERATION_TIMEOUT_SECONDS = 10.0
GENERATION_404_RETRIES = 5
GENERATION_404_RETRY_SECONDS = 2.0
"""The generation ledger is eventually consistent: a lookup right after the stream ends can 404
before the row is indexed, and a turn that already streamed its output must not die on that
window. A transport fault on a lookup — the endpoint dropping the connection mid-body, a timeout —
retries on the same window, since the GET is cheap and a whole re-run of the round is not. A 404
or a transport fault that survives the bounded retries is raised as ModelStreamInterrupted so the
engine re-runs the round once; a second exhaustion fails the turn."""

OPENROUTER_CONTEXT_WINDOW = 200_000
_REASONS = ReasoningSupport(supported=True, tools_with_reasoning=True)
_REQUIRED_REASONS = ReasoningSupport(
    supported=True, tools_with_reasoning=True, default_on=True, can_disable=False
)

GLM_PROVIDERS = ("baseten", "fireworks", "morph", "together")
PROVIDER_ALLOWLIST = {
    "z-ai/glm-5.3": GLM_PROVIDERS,
    "z-ai/glm-5.3-flash": GLM_PROVIDERS,
}
"""The upstreams a slug may be served by, as OpenRouter's `provider.only`. A GLM slug names two
dozen routes that differ in quantization, price and context window, while the spec books one rate
and one window for the id — so which route takes a call is a real difference, and these four serve
both ids. `only` is a hard allowlist: a request whose permitted set serves the model nowhere
answers 404 rather than routing outside it, which is why `allow_fallbacks` stays unset. That field
belongs to `order`; against `only` it collapses the list to its single top route, which then
carries every upstream 429 for the slug alone. This table pins routes and nothing else: the
dead-provider re-route's `ignore` subtracts from the pinned set for a slug named here and is the
whole `provider` object for one that is not, because an exclusion has to reach the wire for every
id or the re-issue lands back on the upstream that answered empty."""

IMAGES_PATH = "/images"
IMAGE_TIMEOUT_SECONDS = 300.0
IMAGE_DIR = "generated-images"
MAX_IMAGES_PER_CALL = 4
MAX_IMAGE_PROMPT_CHARS = 4_000
MAX_IMAGE_NAME_CHARS = 64
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_ERROR_CHARS = 1_000
DEFAULT_IMAGE_MEDIA_TYPE = "image/png"
IMAGE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
IMAGE_TRANSPORT: httpx.AsyncBaseTransport | None = None

ImageModel = Literal[
    "bytedance-seed/seedream-4.5",
    "openai/gpt-image-2",
    "black-forest-labs/flux.2-klein-4b",
    "black-forest-labs/flux.2-pro",
    "recraft/recraft-v4.1",
]
DEFAULT_IMAGE_MODEL: ImageModel = "bytedance-seed/seedream-4.5"

AspectRatio = Literal["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"]
EVERY_ASPECT_RATIO: frozenset[str] = frozenset(get_args(AspectRatio))

Resolution = Literal["2K", "4K"]
EVERY_RESOLUTION: frozenset[str] = frozenset(get_args(Resolution))
DEFAULT_RESOLUTION: Resolution = "2K"


@dataclass(frozen=True)
class ImageModelLimits:
    """What one allowlisted model's providers actually serve, and what an image lists at when the
    response prices nothing. OpenRouter rejects a generation parameter the serving provider does not
    offer, so a model's bounds differ from the tool's own caps and are held on the way in. Empty
    `resolutions` means the model derives its own size and takes no tier.

    A tier a provider names is not thereby a tier it draws: Seed's parameter list advertises `1K`,
    and then refuses the generation because it renders at least 3,686,400 output pixels and 1K is
    1,048,576 at any aspect ratio. Acceptance is settled at two layers and only the second one
    draws, so what belongs here is what came back as an image."""

    max_images: int
    aspect_ratios: frozenset[str]
    resolutions: frozenset[str]
    list_micro_usd: int


IMAGE_MODELS: dict[ImageModel, ImageModelLimits] = {
    "bytedance-seed/seedream-4.5": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=EVERY_ASPECT_RATIO,
        resolutions=EVERY_RESOLUTION,
        list_micro_usd=40_000,
    ),
    "openai/gpt-image-2": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=EVERY_ASPECT_RATIO,
        resolutions=frozenset(),
        list_micro_usd=130_000,
    ),
    "black-forest-labs/flux.2-klein-4b": ImageModelLimits(
        max_images=1,
        aspect_ratios=EVERY_ASPECT_RATIO,
        resolutions=frozenset(),
        list_micro_usd=14_000,
    ),
    "black-forest-labs/flux.2-pro": ImageModelLimits(
        max_images=1,
        aspect_ratios=EVERY_ASPECT_RATIO,
        resolutions=frozenset(),
        list_micro_usd=30_000,
    ),
    "recraft/recraft-v4.1": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=frozenset({"1:1", "4:3", "3:4", "16:9", "9:16"}),
        resolutions=frozenset(),
        list_micro_usd=35_000,
    ),
}

GENERATE_IMAGE_DESCRIPTION = (
    "Generate images from a text prompt and save them into the workspace under "
    f"{IMAGE_DIR}/. Models: {DEFAULT_IMAGE_MODEL} (default) for photoreal and product work; "
    "openai/gpt-image-2 when text inside the image has to read correctly; "
    "black-forest-labs/flux.2-klein-4b for the cheapest usable output; "
    "black-forest-labs/flux.2-pro for the quality tier; recraft/recraft-v4.1 for logo and design "
    f"work. Each call is billed per image and metered against the workspace. Up to "
    f"{MAX_IMAGES_PER_CALL} images per call, and one at a time on the flux.2 models. The result "
    "names the saved files; deliver one to a member with share_file."
)


VIDEOS_PATH = "/videos"
VIDEO_REQUEST_TIMEOUT_SECONDS = 300.0
VIDEO_POLL_INTERVAL_SECONDS = 5.0
VIDEO_POLL_TIMEOUT_SECONDS = 1200.0
"""Sized to observed generation plus queue time, not a round number: OpenRouter's own hailuo-3
example reports 162s for a 5-second 2K clip — about 32s of work per output second — so the 30-second
maximum this tool offers lands near 960s. The headroom over that is queue time: MiniMax runs at most
2 concurrent tasks on a free account and 15 on a paid one, so a job can sit pending behind others
before generation starts. A wait past this is a job that is not coming back, and reporting that
beats holding the turn open."""
VIDEO_DIR = "generated-videos"
VIDEO_SUFFIX = ".mp4"
MAX_VIDEO_PROMPT_CHARS = 4_000
MAX_VIDEO_NAME_CHARS = 64
MAX_VIDEO_BYTES = 256 * 1024 * 1024
MAX_VIDEO_ERROR_CHARS = 1_000
VIDEO_TRANSPORT: httpx.AsyncBaseTransport | None = None

VIDEO_PENDING_STATUSES = frozenset({"pending", "in_progress"})
VIDEO_COMPLETED_STATUS = "completed"

VideoModel = Literal["minimax/hailuo-3", "bytedance/seedance-2.5"]
DEFAULT_VIDEO_MODEL: VideoModel = "minimax/hailuo-3"

VideoAspectRatio = Literal["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
EVERY_VIDEO_ASPECT_RATIO: frozenset[str] = frozenset(get_args(VideoAspectRatio))
VideoResolution = Literal["480p", "720p", "2K"]
MIN_VIDEO_SECONDS = 4
MAX_VIDEO_SECONDS = 30
"""The widest take any allowlisted model films, which is what the duration field offers; a model
filming a narrower range narrows the call through its own `VIDEO_MODELS` row."""
DEFAULT_VIDEO_SECONDS = 5


@dataclass(frozen=True)
class VideoModelLimits:
    """What one allowlisted model's providers actually serve, and what a second of its output lists
    at when the job prices nothing. OpenRouter rejects a generation parameter the serving provider
    does not offer, so a model's bounds differ from the tool's own caps and are held on the way in:
    the field offers the widest take and every tier any model films, and a row narrows both.

    `second_micro_usd` is the resolution tiers the model films, each mapped to its list rate, since
    a rate is only a rate at a size. Hailuo bills a flat $0.13 per output second at its one 2K tier.
    Seedance bills $0.0000107 per video token, tokens being `width * height * seconds * 24 / 1024`,
    so its rate rises with the frame: $0.107/s at 480p and $0.233/s at 720p. A tier's rate is taken
    at the largest frame the tier serves (992x432 and 1112x834), since the aspect ratio decides the
    frame within a tier and a list rate that trails the real charge would under-bill the workspace.

    `default_resolution` is what a call naming no tier films — the tier that yields a take a member
    can be given, not the cheapest one."""

    min_seconds: int
    max_seconds: int
    aspect_ratios: frozenset[str]
    default_resolution: VideoResolution
    second_micro_usd: dict[VideoResolution, int]


VIDEO_MODELS: dict[VideoModel, VideoModelLimits] = {
    "minimax/hailuo-3": VideoModelLimits(
        min_seconds=5,
        max_seconds=15,
        aspect_ratios=EVERY_VIDEO_ASPECT_RATIO,
        default_resolution="2K",
        second_micro_usd={"2K": 130_000},
    ),
    "bytedance/seedance-2.5": VideoModelLimits(
        min_seconds=4,
        max_seconds=30,
        aspect_ratios=EVERY_VIDEO_ASPECT_RATIO,
        default_resolution="720p",
        second_micro_usd={"480p": 107_471, "720p": 232_577},
    ),
}

GENERATE_VIDEO_DESCRIPTION = (
    "Generate a video from a text prompt and save it into the workspace under "
    f"{VIDEO_DIR}/. Models: {DEFAULT_VIDEO_MODEL} (default, MiniMax H3) films 5-15 seconds of 2K; "
    "bytedance/seedance-2.5 films 4-30 seconds at 480p or 720p, for a take longer than 15 seconds "
    "or cheaper than 2K. Both carry a native stereo audio track. Each call is billed per output "
    "second at the chosen model and resolution and metered against the workspace, so a longer take "
    f"or a bigger frame costs proportionally more; the default is {DEFAULT_VIDEO_SECONDS} seconds. "
    "Generation takes minutes and this tool waits for it. The result names the saved file; deliver "
    "it to a member with share_file."
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


def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage:
    details = usage.prompt_tokens_details
    cached_tokens = (details.cached_tokens or 0) if details is not None else 0
    extra = details.model_extra if details is not None else None
    cache_write = extra.get("cache_write_tokens") if extra is not None else None
    if cache_write is None:
        reported_cache_write_tokens = 0
    elif isinstance(cache_write, int) and not isinstance(cache_write, bool):
        reported_cache_write_tokens = cache_write
    else:
        raise ValueError("cache_write_tokens is not an integer")
    cache_write_tokens = reported_cache_write_tokens if cache_write_30m_rate else 0
    if cached_tokens > usage.prompt_tokens:
        raise ValueError("cached prompt tokens exceed total prompt tokens")
    if cached_tokens + reported_cache_write_tokens > usage.prompt_tokens:
        raise ValueError("cached and cache-write prompt tokens exceed total prompt tokens")
    return Usage(
        input_tokens=usage.prompt_tokens - cached_tokens - cache_write_tokens,
        output_tokens=usage.completion_tokens,
        cache_read_tokens=cached_tokens,
        cache_write_30m_tokens=cache_write_tokens,
    )


class _GenerationData(BaseModel):
    cancelled: bool
    finish_reason: str | None
    native_tokens_prompt: int
    native_tokens_completion: int
    native_tokens_cached: int | None = 0


class _GenerationResponse(BaseModel):
    data: _GenerationData


def _contains_json_reference(value: object) -> bool:
    pending = [value]
    while pending:
        match pending.pop():
            case dict() as item:
                if JSON_REFERENCE_KEYS & item.keys():
                    return True
                pending.extend(item.values())
            case list() as item:
                pending.extend(item)
            case _:
                continue
    return False


def _openrouter_messages(
    model: str,
    system: str,
    messages: tuple[Message, ...],
    accepts_image_input: bool,
) -> list[dict[str, object]]:
    if not accepts_image_input:
        messages = omit_images(messages)
    rendered = openai_messages(system, messages)
    if not openrouter_slug(model).startswith("google/"):
        return rendered
    error_results = {
        block.tool_use_id
        for message in messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.is_error
    }
    for message in rendered:
        match message:
            case {"role": "tool", "content": str() as content}:
                if "$" not in content and "\\u" not in content:
                    continue
                result_content = content
                if message.get("tool_call_id") in error_results:
                    result_content = content.removeprefix(OPENAI_TOOL_ERROR_PREFIX)
                try:
                    result = json.loads(result_content)
                except json.JSONDecodeError:
                    continue
                except (ValueError, RecursionError):
                    message["content"] = json.dumps({"text": content})
                    continue
                if _contains_json_reference(result):
                    message["content"] = json.dumps({"text": content})
            case _:
                continue
    return rendered


@dataclass(frozen=True)
class _OpenRouterRetry:
    spec: ModelSpec
    model: str
    delay: float = INITIAL_RETRY_DELAY_SECONDS
    attempt: int = 0
    abort_retried: bool = False

    async def status(self, error: openai.APIStatusError, yielded: bool) -> "_OpenRouterRetry":
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
        await asyncio.sleep(wait)
        return replace(
            self,
            delay=min(self.delay * 2, MAX_RETRY_DELAY_SECONDS),
            attempt=attempt,
        )

    def stream_error(self, error: openai.APIError, yielded: bool) -> "_OpenRouterRetry":
        if type(error) is not openai.APIError:
            raise error
        is_gemini_abort = (
            str(error) == GEMINI_ABORT_ERROR and self.model == GEMINI_ABORT_RETRY_MODEL
        )
        if is_gemini_abort and not yielded and not self.abort_retried:
            log(
                "model.provider_abort_retry",
                provider=self.spec.provider,
                model=self.model,
                attempt=1,
            )
            emit_metric(
                "model_provider_retry_total",
                provider=self.spec.provider,
                model=self.model,
                kind="abort",
            )
            return replace(self, abort_retried=True)
        raise ModelStreamInterrupted(
            "stream_error",
            f"OpenRouter injected an error into the SSE stream: {error}",
        ) from error


class _OpenRouterStream:
    def __init__(self, cache_write_30m_priced: bool) -> None:
        self.cache_write_30m_priced = cache_write_30m_priced
        self.yielded = False
        self.tool_call_ids: dict[int, str] = {}
        self.usage: Usage | None = None
        self.finish_reason: str | None = None
        self.provider: str | None = None
        self.generation_id: str | None = None

    def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]:
        self.generation_id = chunk.id or self.generation_id
        self.provider = _chunk_provider(chunk) or self.provider
        if chunk.usage is not None:
            self.usage = _usage_of(chunk.usage, self.cache_write_30m_priced)
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


@dataclass(frozen=True)
class OpenRouterModelClient:
    """The OpenRouter backend behind the `ModelClient` protocol: it streams ModelEvents from the
    Chat Completions wire, ending with one Usage, exactly as core's OpenAIClient does, and adds
    OpenRouter's own behavior. 429/5xx retry with retry-after-aware backoff but only until visible
    output yields; finish_reason=length raises ModelResponseTruncated. A fault on the live stream —
    an error frame OpenRouter injects when its upstream stalls or dies (the exact APIError class), a
    raw timeout or peer disconnect mid-body, or a usage lookup the ledger never answers (a 404 past
    the indexing window, a transport fault past the same window) — raises
    ModelStreamInterrupted whether or not output already yielded: the engine discards the partial
    round and re-runs it once, and a second interruption fails the turn. A normal completion that
    returned no text and no tool calls is a dead upstream — the client re-issues excluding that
    provider up to MAX_EMPTY_PROVIDER_RETRIES, then degrades to the empty result for the turn loop's
    nudge. The exclusion rides `provider.ignore` for every id, and it has to: a re-issue carrying
    no exclusion is byte-identical to the call the dead upstream answered empty, and the sticky
    routing key below pins it straight back to that upstream. An id in PROVIDER_ALLOWLIST rides the
    exclusion in the same `provider` object as `only`, four routes against three exclusions at
    most; every other id — every Gemini one — sends `ignore` as its whole provider preference, so
    the pin stays where a slug names one. An unpinned slug can run out of upstreams before those
    retries do — every Gemini id serves from two — and OpenRouter then refuses the call outright; a
    404 on a call carrying exclusions of ours and no pin is a refusal those exclusions asked for,
    so the round degrades to the same empty result rather than failing the turn. Under a pin the
    exclusions cannot cover the slug, and the first call of a round carries none at all, so a 404
    there is the account's own routing policy and keeps its plain raise. Gemini 3.7 Flash's exact
    no-output abort retries once immediately.
    The request's `reasoning` effort rides `extra_body` as the thinking budget OpenRouter derives
    from max_tokens when the model's spec supports it; `off` rides there too, as `enabled: false`,
    because an omitted parameter leaves the upstream model reasoning at its own default effort
    through a reasoning-inclusive budget.

    `session_id` rides `extra_body` as OpenRouter's sticky routing key, and it is what makes prompt
    caching work here at all: a slug names many upstream providers, each holding a cache of its own,
    and a call that lands on a different one than the last pays the full prompt again. Named, the
    key pins the series from its first successful call; unnamed, OpenRouter derives one by hashing
    the opening messages, which a conversation loses the moment compaction rewrites its head — so a
    request that names none is refused here, before the call. The field is optional on the request
    because a direct client holds one cache and never reads it, which leaves this the only place
    that can tell an unnamed series from one that does not matter: a caller that reaches a router
    without naming its series gets an error rather than a cache that quietly never hits."""

    client: openai.AsyncOpenAI
    spec: ModelSpec
    key: str
    generation_transport: httpx.AsyncBaseTransport | None = None

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        retry = _OpenRouterRetry(self.spec, request.model)
        slug = openrouter_slug(request.model)
        empty_attempt = 0
        ignore_providers: set[str] = set()
        while True:
            state = _OpenRouterStream(bool(self.spec.price.cache_write_30m))
            try:
                stream = await self.client.chat.completions.create(
                    **self._create_kwargs(request, frozenset(ignore_providers))
                )
                stream_started = False
                async for chunk in stream:
                    if not stream_started:
                        stream_started = True
                        yield ModelStreamStart()
                    for event in state.accept(chunk):
                        yield event
            except openai.APIStatusError as error:
                if state.usage is not None:
                    yield state.usage
                nowhere_left = error.status_code == 404 and slug not in PROVIDER_ALLOWLIST
                if ignore_providers and nowhere_left:
                    log(
                        "model.provider_exclusions_exhausted",
                        provider=self.spec.provider,
                        model=request.model,
                        excluded=sorted(ignore_providers),
                    )
                    return
                retry = await retry.status(error, state.yielded)
                continue
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as error:
                if state.usage is not None:
                    yield state.usage
                raise ModelStreamInterrupted(
                    "stream_transport",
                    f"OpenRouter stream died mid-round ({type(error).__name__}): {error}",
                ) from error
            except openai.APIError as error:
                if state.usage is not None:
                    yield state.usage
                retry = retry.stream_error(error, state.yielded)
                continue
            if state.finish_reason == "length":
                if state.usage is not None:
                    yield state.usage
                raise ModelResponseTruncated(
                    "OpenRouter completion truncated at the max_tokens budget "
                    "(finish_reason=length)"
                )
            usage = await self._finish_usage(state)
            dead = state.finish_reason == "stop" and not state.yielded
            if dead and state.provider is not None and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES:
                empty_attempt += 1
                ignore_providers.add(state.provider)
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

    async def _finish_usage(self, state: _OpenRouterStream) -> Usage:
        usage = state.usage
        if usage is None and state.finish_reason is not None and state.generation_id is not None:
            usage = await self._generation_usage(state.generation_id, state.finish_reason)
        if usage is None:
            raise RuntimeError("model stream produced no usage")
        return usage

    async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None:
        async with httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL,
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=GENERATION_TIMEOUT_SECONDS,
            transport=self.generation_transport,
        ) as client:
            for attempt in range(GENERATION_404_RETRIES + 1):
                last = attempt == GENERATION_404_RETRIES
                try:
                    response = await client.get(GENERATION_PATH, params={"id": generation_id})
                except httpx.TransportError as error:
                    if last:
                        raise ModelStreamInterrupted(
                            "generation_lookup",
                            f"OpenRouter generation lookup for {generation_id} failed: {error}",
                        ) from error
                    emit_metric(
                        "model_provider_retry_total",
                        provider=self.spec.provider,
                        model=self.spec.id,
                        kind="generation_lookup",
                    )
                    await asyncio.sleep(GENERATION_404_RETRY_SECONDS)
                    continue
                if response.status_code != 404 or last:
                    break
                emit_metric(
                    "model_provider_retry_total",
                    provider=self.spec.provider,
                    model=self.spec.id,
                    kind="generation_404",
                )
                await asyncio.sleep(GENERATION_404_RETRY_SECONDS)
        if response.status_code == 404:
            raise ModelStreamInterrupted(
                "generation_missing",
                f"OpenRouter never indexed generation {generation_id}: still 404 after "
                f"{GENERATION_404_RETRIES + 1} lookups",
            )
        response.raise_for_status()
        generation = _GenerationResponse.model_validate(response.json()).data
        if generation.cancelled or generation.finish_reason != finish_reason:
            return None
        cached = generation.native_tokens_cached or 0
        if cached > generation.native_tokens_prompt:
            raise ValueError("cached generation tokens exceed prompt tokens")
        emit_metric(
            "model_provider_retry_total",
            provider=self.spec.provider,
            model=self.spec.id,
            kind="usage",
        )
        return Usage(
            input_tokens=generation.native_tokens_prompt - cached,
            output_tokens=generation.native_tokens_completion,
            cache_read_tokens=cached,
        )

    def _create_kwargs(
        self, request: ModelRequest, ignore_providers: frozenset[str]
    ) -> dict[str, Any]:
        if request.session_id is None:
            raise RuntimeError(
                "an OpenRouter request names no session_id; a router pins a prompt cache to a "
                "session, so name the series this call belongs to — the conversation for a turn's "
                "rounds, the job for a background call"
            )
        slug = openrouter_slug(request.model)
        extra_body: dict[str, Any] = {"session_id": request.session_id}
        effort = self.spec.wire_reasoning(request.reasoning, request.tools)
        if effort == "off":
            extra_body["reasoning"] = {"enabled": False}
        elif effort not in (None, "auto"):
            extra_body["reasoning"] = {"effort": effort}
        provider: dict[str, Any] = {}
        if slug in PROVIDER_ALLOWLIST:
            provider["only"] = list(PROVIDER_ALLOWLIST[slug])
        if ignore_providers:
            provider["ignore"] = sorted(ignore_providers)
        if provider:
            extra_body["provider"] = provider
        kwargs: dict[str, Any] = {
            "model": slug,
            "messages": _openrouter_messages(
                request.model,
                request.system,
                request.messages,
                self.spec.accepts_image_input,
            ),
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
        if request.tool_choice is not None:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice},
            }
            kwargs["parallel_tool_calls"] = False
        return kwargs


def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient:
    return OpenRouterModelClient(
        client=openai_sdk_client(key, base_url=OPENROUTER_BASE_URL),
        spec=spec,
        key=key,
    )


def _openrouter(
    id: str,
    price: ModelPrice,
    cutoff: str,
    context_window: int = OPENROUTER_CONTEXT_WINDOW,
    reasoning: ReasoningSupport = _REASONS,
    accepts_image_input: bool = True,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_NAME,
        client=_model_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=context_window,
        reasoning=reasoning,
        api_surface="chat",
        key_slot=OPENROUTER_KEY_SLOT,
        key_env=OPENROUTER_API_KEY_ENV,
        accepts_image_input=accepts_image_input,
    )


OPENROUTER_MODEL_SPECS = (
    _openrouter(
        "google/gemini-2.5-pro",
        ModelPrice(1_000_000, 10_000_000, 0, 0, 0),
        "2025-01",
    ),
    _openrouter(
        "google/gemini-3.7-flash",
        ModelPrice(375_000, 1_875_000, 37_500, 0, 0),
        "2026-03",
        context_window=1_048_576,
        reasoning=_REQUIRED_REASONS,
    ),
    _openrouter(
        "z-ai/glm-5.2",
        ModelPrice(1_000_000, 3_000_000, 0, 0, 0),
        "2026-03",
        accepts_image_input=False,
    ),
    _openrouter(
        "z-ai/glm-5.3",
        ModelPrice(1_400_000, 4_400_000, 260_000, 0, 0),
        "2026-03",
        context_window=1_048_576,
        reasoning=_REQUIRED_REASONS,
        accepts_image_input=False,
    ),
    # Every route but Cloudflare's serves 1,048,576 tokens, so the listing's 1,310,720 is one
    # route's window and not this id's. The listing halves these rates under a 0.5 promotional
    # discount that no route is held to. The listing publishes no cutoff; the family's holds.
    _openrouter(
        "z-ai/glm-5.3-flash",
        ModelPrice(150_000, 500_000, 30_000, 0, 0),
        "2026-03",
        context_window=1_048_576,
        reasoning=_REQUIRED_REASONS,
    ),
    _openrouter(
        "moonshotai/kimi-k3",
        ModelPrice(3_000_000, 15_000_000, 300_000, 0, 0),
        "2026-04",
        context_window=1_000_000,
    ),
    _openrouter(
        "anthropic/claude-fable-5.1",
        ModelPrice(10_000_000, 50_000_000, 250_000, 0, 0),
        "2026-01",
        context_window=1_000_000,
        reasoning=_REQUIRED_REASONS,
    ),
    _openrouter(
        "anthropic/claude-fable-5",
        ModelPrice(10_000_000, 50_000_000, 1_000_000, 0, 0),
        "2026-01",
        context_window=1_000_000,
        reasoning=_REQUIRED_REASONS,
    ),
    # The route accepts 1,050,000 tokens, and past 272,000 input tokens it bills 2x input and 1.5x
    # output for the whole request, so the window is the one this rate is true at. The listing shows
    # half of these rates under a 0.5 promotional discount that no route is held to.
    _openrouter(
        "openai/gpt-5.6-sol",
        ModelPrice(4_000_000, 20_000_000, 400_000, 0, 0, 5_000_000),
        "2026-02",
        context_window=272_000,
    ),
)


class GenerateImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(
        max_length=MAX_IMAGE_PROMPT_CHARS,
        description="What to draw. Describe subject, composition, lighting and any text verbatim.",
    )
    file_name: str = Field(
        max_length=MAX_IMAGE_NAME_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="File name stem for the saved images, without a directory or extension.",
    )
    model: ImageModel = DEFAULT_IMAGE_MODEL
    n: int = Field(
        default=1,
        ge=1,
        le=MAX_IMAGES_PER_CALL,
        description="How many images to draw. The flux.2 models draw exactly one.",
    )
    resolution: Resolution | None = Field(
        default=None,
        description=(
            f"Resolution tier, defaulting to {DEFAULT_RESOLUTION}. 4K costs more per image; ask "
            "for it when the image will be printed or cropped into. Only "
            "bytedance-seed/seedream-4.5 takes a tier, and it draws nothing smaller than 2K; omit "
            "the field for the other models, which size their own output."
        ),
    )
    aspect_ratio: AspectRatio | None = Field(
        default=None,
        description="Aspect ratio. recraft/recraft-v4.1 takes only 1:1, 4:3, 3:4, 16:9 and 9:16.",
    )
    quality: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Rendering quality, and a higher cost. Only openai/gpt-image-2 takes one; omit it for "
            "the others."
        ),
    )

    @model_validator(mode="after")
    def _within_model_limits(self) -> "GenerateImageInput":
        """Hold the call to what the chosen model serves, and settle its resolution. OpenRouter
        answers 400 for a generation parameter its provider does not offer, so a combination the
        allowlist already knows is unservable is refused here, where the model reads the reason and
        can pick another. A model that takes a tier and was not given one draws at
        `DEFAULT_RESOLUTION` — the cheap middle of the ladder, since an unasked-for size should be
        the one nobody regrets paying for."""
        limits = IMAGE_MODELS[self.model]
        if self.n > limits.max_images:
            raise ValueError(f"{self.model} draws at most {limits.max_images} image(s) per call")
        if self.aspect_ratio is not None and self.aspect_ratio not in limits.aspect_ratios:
            raise ValueError(
                f"{self.model} does not take aspect_ratio {self.aspect_ratio}; it accepts "
                f"{', '.join(sorted(limits.aspect_ratios))}"
            )
        if self.resolution is not None and self.resolution not in limits.resolutions:
            raise ValueError(
                f"{self.model} takes no resolution tier; it sizes its own output"
                if not limits.resolutions
                else f"{self.model} does not take resolution {self.resolution}; it accepts "
                f"{', '.join(sorted(limits.resolutions))}"
            )
        if self.resolution is None and limits.resolutions:
            self.resolution = DEFAULT_RESOLUTION
        return self


class OpenRouterImageError(RuntimeError):
    """OpenRouter answered success with a body this tool cannot use — no image in it, or an image
    over the byte cap. Raised so the turn reports the failure instead of saving a broken file."""


def _reported_cost_micro_usd(usage: object) -> int | None:
    """What OpenRouter says a generation cost, in micro-USD: its own charge, else the upstream
    charge a BYOK call reports in its place — the same money, billed one hop further out. A reported
    zero prices nothing, so it reads as no charge and the caller falls back to a list rate."""
    reported = usage if isinstance(usage, dict) else {}
    details = reported.get("cost_details")
    for cost in (
        reported.get("cost"),
        details.get("upstream_inference_cost") if isinstance(details, dict) else None,
    ):
        if isinstance(cost, int | float) and not isinstance(cost, bool) and cost > 0:
            return round(float(cost) * MICRO_USD_PER_USD)
    return None


@dataclass(frozen=True)
class GeneratedImage:
    media_type: str
    data: str
    raw: bytes


@dataclass(frozen=True)
class OpenRouterImages:
    """One image generation, start to finish: POST the bounded request to OpenRouter's Image API,
    save each returned image into the workspace, meter what it cost onto the turn, and hand the
    model both the paths and the images themselves.

    Cost comes off the response's own `usage.cost` in USD, which is what OpenRouter charged for this
    generation whether the upstream billed per image, per megapixel, or per output image token. An
    OpenRouter account keyed to its own upstream provider reports `cost` as zero and states the
    charge under `cost_details.upstream_inference_cost`, which prices the call instead — the same
    money, billed one hop further out. A response pricing neither is charged the model's list rate
    per image (`IMAGE_MODELS`).

    Only a generation on the platform's key is metered. A workspace holding its own
    `openrouter_api_key` is billed by OpenRouter directly, and an `images` ledger row exports as
    platform-served — `byok` resolves a key slot through the model registry, which has no image
    model — so metering that spend would bill it a second time. A failed generation is never billed
    by OpenRouter and never metered here, because metering happens only after the images are in
    hand. `transport` is the httpx testability seam; production leaves it None."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult:
        key = await self.credentials.get(OPENROUTER_KEY_SLOT)
        workspace_keyed = await self.credentials.stored(OPENROUTER_KEY_SLOT)
        async with httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL,
            timeout=IMAGE_TIMEOUT_SECONDS,
            transport=self.transport,
            headers={"Authorization": f"Bearer {key}"},
        ) as http:
            response = await http.post(
                IMAGES_PATH,
                json=args.model_dump(exclude_none=True, exclude={"file_name"}),
            )
        if response.is_error:
            return ToolResult(
                content=(TextContent(text=self._refusal(args, response)),), is_error=True
            )
        body = response.json()
        images = self._images(args, body)
        paths = [await self._save(ctx, args, index, image) for index, image in enumerate(images, 1)]
        micro_usd = self._charge(body, args, len(images))
        if not workspace_keyed:
            await ctx.meter_images(args.model, len(images), micro_usd)
        return ToolResult(
            content=(
                TextContent(
                    text=json.dumps(
                        {
                            "model": args.model,
                            "files": paths,
                            "machine_generated": True,
                            "cost_micro_usd": micro_usd,
                        }
                    )
                ),
                *(ImageContent(media_type=image.media_type, data=image.data) for image in images),
            )
        )

    def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str:
        """The provider's own words for why it produced nothing — a content refusal, a rejected
        parameter, an exhausted balance. Surfaced as the tool's error text so the model can change
        the prompt and try again, and bounded because the body is the provider's to choose."""
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else None
        )
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        detail = message if isinstance(message, str) and message else response.text
        return (
            f"{args.model} generated no image ({response.status_code}): "
            f"{detail[:MAX_IMAGE_ERROR_CHARS]}"
        )

    def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]:
        """The images the response carries, decoded and size-checked before any of them reaches
        disk — what a provider returns is not ours to size, and an oversized image fails the whole
        call rather than leaving half a generation written. OpenRouter states a `media_type`
        whenever it could identify the format and omits it when it could not, so an entry without
        one is read as PNG, the format every allowlisted model returns by default."""
        data = body.get("data") if isinstance(body, dict) else None
        images: list[GeneratedImage] = []
        for entry in data if isinstance(data, list) else ():
            encoded = entry.get("b64_json") if isinstance(entry, dict) else None
            media_type = entry.get("media_type") if isinstance(entry, dict) else None
            if not isinstance(encoded, str):
                continue
            raw = base64.b64decode(encoded)
            if len(raw) > MAX_IMAGE_BYTES:
                raise OpenRouterImageError(
                    f"{args.model} returned a {len(raw)}-byte image; this tool saves at most "
                    f"{MAX_IMAGE_BYTES}"
                )
            images.append(
                GeneratedImage(
                    media_type=media_type
                    if isinstance(media_type, str)
                    else DEFAULT_IMAGE_MEDIA_TYPE,
                    data=encoded,
                    raw=raw,
                )
            )
        if not images:
            raise OpenRouterImageError("openrouter answered no image data")
        return tuple(images)

    async def _save(
        self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage
    ) -> str:
        """Write one returned image into the workspace, on disk under the member's own workspace."""
        suffix = IMAGE_SUFFIXES.get(image.media_type, IMAGE_SUFFIXES[DEFAULT_IMAGE_MEDIA_TYPE])
        path = f"{IMAGE_DIR}/{args.file_name}-{index}{suffix}"
        await ctx.sandbox.write_file(path, image.raw)
        return path

    def _charge(self, body: object, args: GenerateImageInput, images: int) -> int:
        """What this generation cost, in micro-USD: what OpenRouter reports for it, else the model's
        list rate per image."""
        usage = body.get("usage") if isinstance(body, dict) else None
        reported = _reported_cost_micro_usd(usage)
        if reported is not None:
            return reported
        return IMAGE_MODELS[args.model].list_micro_usd * images


async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("generate_image needs the openrouter extension context")
    return await OpenRouterImages(
        credentials=ctx.ext.credentials, transport=IMAGE_TRANSPORT
    ).generate(ctx, args)


GENERATE_IMAGE_TOOL = ToolDef(
    name="generate_image",
    description=GENERATE_IMAGE_DESCRIPTION,
    input_model=GenerateImageInput,
    handler=_generate_image,
    side_effecting=True,
    bound=ObjectBinding(kind=ARTIFACT_KIND, binding="collection"),
)


class GenerateVideoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(
        max_length=MAX_VIDEO_PROMPT_CHARS,
        description=(
            "What to film. Describe subject, action, camera movement, lighting, and any speech or "
            "sound the shot should carry."
        ),
    )
    file_name: str = Field(
        max_length=MAX_VIDEO_NAME_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="File name stem for the saved video, without a directory or extension.",
    )
    model: VideoModel = DEFAULT_VIDEO_MODEL
    duration: int = Field(
        default=DEFAULT_VIDEO_SECONDS,
        ge=MIN_VIDEO_SECONDS,
        le=MAX_VIDEO_SECONDS,
        description=(
            f"Length in seconds, {DEFAULT_VIDEO_SECONDS} by default. minimax/hailuo-3 films 5-15 "
            "and bytedance/seedance-2.5 films 4-30. Every second is billed, so ask for a longer "
            "take only when the action needs it."
        ),
    )
    resolution: VideoResolution | None = Field(
        default=None,
        description=(
            "Resolution tier, defaulting to the best one the chosen model films. minimax/hailuo-3 "
            "films 2K only; bytedance/seedance-2.5 films 480p or 720p, where 480p costs under half "
            "of 720p per second — ask for it when the take is a draft."
        ),
    )
    aspect_ratio: VideoAspectRatio | None = Field(
        default=None,
        description="Aspect ratio. Omit it to take the model's own framing for the prompt.",
    )
    generate_audio: bool = Field(
        default=True,
        description="Whether the video carries its native audio track. False for a silent take.",
    )

    @model_validator(mode="after")
    def _within_model_limits(self) -> "GenerateVideoInput":
        """Hold the call to what the chosen model serves, and settle its resolution. OpenRouter
        answers 400 for a generation parameter its provider does not offer, so a combination the
        allowlist already knows is unservable is refused here, where the model reads the reason and
        can pick another. The tier is settled rather than left to the provider because it is what a
        second of output is metered at, so an unstated one would price a frame nobody chose."""
        limits = VIDEO_MODELS[self.model]
        if not limits.min_seconds <= self.duration <= limits.max_seconds:
            raise ValueError(
                f"{self.model} films between {limits.min_seconds} and {limits.max_seconds} seconds "
                "per call"
            )
        if self.aspect_ratio is not None and self.aspect_ratio not in limits.aspect_ratios:
            raise ValueError(
                f"{self.model} does not take aspect_ratio {self.aspect_ratio}; it accepts "
                f"{', '.join(sorted(limits.aspect_ratios))}"
            )
        if self.resolution is None:
            self.resolution = limits.default_resolution
        elif self.resolution not in limits.second_micro_usd:
            raise ValueError(
                f"{self.model} does not film at {self.resolution}; it films "
                f"{', '.join(sorted(limits.second_micro_usd))}"
            )
        return self


class OpenRouterVideoError(RuntimeError):
    """OpenRouter accepted the generation and then answered with something this tool cannot use — no
    job to poll, a refused poll or download, a wait past the tool's bound, or a video over the byte
    cap. Raised so the turn reports the failure instead of saving a broken file."""


@dataclass(frozen=True)
class VideoJob:
    """One asynchronous generation as OpenRouter last reported it: the job id to poll and download,
    the status it is at, the reason a failed one gives, and what it charged when it is done."""

    id: str
    status: str
    error: str | None
    cost_micro_usd: int | None


@dataclass(frozen=True)
class OpenRouterVideos:
    """One video generation, start to finish: POST the bounded request to OpenRouter's asynchronous
    Video API, poll the job it returns until the generation settles, download the MP4 into the
    workspace, and meter what it cost onto the turn.

    A video is minutes of provider work, so the API answers 202 with a job rather than the file, and
    the tool holds the call open until the job reaches `completed` or `failed`, bounded by
    `VIDEO_POLL_TIMEOUT_SECONDS`. A `failed` job is the provider's refusal — a rejected prompt, an
    exhausted balance — and reaches the model as tool-result text, the way a rejected request does.

    Cost, key resolution and metering are the image tool's: `usage.cost` off the completed poll is
    what OpenRouter charged, a BYOK account's `cost_details.upstream_inference_cost` prices the call
    when it reports zero, and a job pricing neither is charged the chosen model's list rate for the
    tier it filmed, over the seconds asked for (`VIDEO_MODELS`). Only a generation on the platform's
    key is metered, since a workspace holding its own `openrouter_api_key` is billed directly and a
    `videos` ledger row exports as platform-served. `transport` is the httpx testability seam;
    production leaves it None."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult:
        key = await self.credentials.get(OPENROUTER_KEY_SLOT)
        workspace_keyed = await self.credentials.stored(OPENROUTER_KEY_SLOT)
        async with httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL,
            timeout=VIDEO_REQUEST_TIMEOUT_SECONDS,
            transport=self.transport,
            headers={"Authorization": f"Bearer {key}"},
        ) as http:
            response = await http.post(
                VIDEOS_PATH,
                json=args.model_dump(exclude_none=True, exclude={"file_name"}),
            )
            if response.is_error:
                return ToolResult(
                    content=(TextContent(text=self._refusal(args, response)),), is_error=True
                )
            job = await self._settled(args, http, self._job(response.json()))
            if job.status != VIDEO_COMPLETED_STATUS:
                return ToolResult(
                    content=(TextContent(text=self._failure(args, job)),), is_error=True
                )
            video = await self._download(args, http, job)
        path = await self._save(ctx, args, video)
        micro_usd = self._charge(args, job)
        if not workspace_keyed:
            await ctx.meter_videos(args.model, 1, micro_usd)
        return ToolResult(
            content=(
                TextContent(
                    text=json.dumps(
                        {
                            "model": args.model,
                            "files": [path],
                            "duration_seconds": args.duration,
                            "machine_generated": True,
                            "cost_micro_usd": micro_usd,
                        }
                    ),
                ),
            )
        )

    def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str:
        """The provider's own words for why it started nothing — a content refusal, a rejected
        parameter, an exhausted balance — surfaced as the tool's error text so the model can change
        the prompt and try again, and bounded because the body is the provider's to choose."""
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else None
        )
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        detail = message if isinstance(message, str) and message else response.text
        return (
            f"{args.model} generated no video ({response.status_code}): "
            f"{detail[:MAX_VIDEO_ERROR_CHARS]}"
        )

    def _job(self, body: object) -> VideoJob:
        """The job an accept or a poll describes. A body naming no job or no status is unpollable,
        so it fails the call rather than becoming a wait for something that was never started."""
        reported = body if isinstance(body, dict) else {}
        job_id = reported.get("id")
        status = reported.get("status")
        if not isinstance(job_id, str) or not isinstance(status, str):
            raise OpenRouterVideoError("openrouter answered no video job")
        error = reported.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return VideoJob(
            id=job_id,
            status=status,
            error=message if isinstance(message, str) else None,
            cost_micro_usd=_reported_cost_micro_usd(reported.get("usage")),
        )

    async def _settled(
        self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob
    ) -> VideoJob:
        """The job as it stands once it stops moving, polled at `VIDEO_POLL_INTERVAL_SECONDS`. The
        wait is bounded because a provider job is external work that may never settle, and a turn
        held open forever is worse than a reported failure."""
        deadline = time.monotonic() + VIDEO_POLL_TIMEOUT_SECONDS
        while job.status in VIDEO_PENDING_STATUSES:
            if time.monotonic() >= deadline:
                raise OpenRouterVideoError(
                    f"{args.model} left {job.id} {job.status} after "
                    f"{VIDEO_POLL_TIMEOUT_SECONDS:.0f}s"
                )
            await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)
            poll = await http.get(f"{VIDEOS_PATH}/{job.id}")
            if poll.is_error:
                raise OpenRouterVideoError(
                    f"polling {job.id} answered {poll.status_code}: "
                    f"{poll.text[:MAX_VIDEO_ERROR_CHARS]}"
                )
            job = self._job(poll.json())
        return job

    def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str:
        """Why a job OpenRouter accepted produced nothing, in the provider's own words where it gave
        them, so the model can change the prompt and try again."""
        detail = job.error or f"the job ended {job.status}"
        return f"{args.model} generated no video: {detail[:MAX_VIDEO_ERROR_CHARS]}"

    async def _download(
        self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob
    ) -> bytes:
        """The finished MP4, size-checked before it reaches disk — what a provider returns is not
        ours to size, and an oversized video fails the call rather than filling the workspace."""
        content = await http.get(f"{VIDEOS_PATH}/{job.id}/content", params={"index": 0})
        if content.is_error:
            raise OpenRouterVideoError(
                f"downloading {job.id} answered {content.status_code}: "
                f"{content.text[:MAX_VIDEO_ERROR_CHARS]}"
            )
        raw = content.content
        if not raw:
            raise OpenRouterVideoError("openrouter answered no video data")
        if len(raw) > MAX_VIDEO_BYTES:
            raise OpenRouterVideoError(
                f"{args.model} returned a {len(raw)}-byte video; this tool saves at most "
                f"{MAX_VIDEO_BYTES}"
            )
        return raw

    async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str:
        """Write the finished video into the workspace, on disk under the member's own workspace."""
        path = f"{VIDEO_DIR}/{args.file_name}{VIDEO_SUFFIX}"
        await ctx.sandbox.write_file(path, video)
        return path

    def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int:
        """What this generation cost, in micro-USD: what OpenRouter reports for it, else the model's
        list rate for the tier it filmed over the seconds asked for. A per-second rate is a rate at
        one frame size — a token-billed model costs more per second the bigger the frame — so the
        fallback reads the tier this take filmed, never one rate for the model."""
        if job.cost_micro_usd is not None:
            return job.cost_micro_usd
        limits = VIDEO_MODELS[args.model]
        return limits.second_micro_usd[args.resolution or limits.default_resolution] * args.duration


async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("generate_video needs the openrouter extension context")
    return await OpenRouterVideos(
        credentials=ctx.ext.credentials, transport=VIDEO_TRANSPORT
    ).generate(ctx, args)


GENERATE_VIDEO_TOOL = ToolDef(
    name="generate_video",
    description=GENERATE_VIDEO_DESCRIPTION,
    input_model=GenerateVideoInput,
    handler=_generate_video,
    side_effecting=True,
    bound=ObjectBinding(kind=ARTIFACT_KIND, binding="collection"),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        models=OPENROUTER_MODEL_SPECS,
        tools=(GENERATE_IMAGE_TOOL, GENERATE_VIDEO_TOOL),
        credentials=(
            CredentialSlot(
                name=OPENROUTER_KEY_SLOT,
                description=(
                    "The OpenRouter API key this workspace's models, image generation and video "
                    "generation run on. Read host-side and never exposed to chat or the sandbox; "
                    f"unset, the platform's {OPENROUTER_API_KEY_ENV} serves the deploy."
                ),
            ),
        ),
    )
