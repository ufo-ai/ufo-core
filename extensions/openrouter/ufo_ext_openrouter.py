"""OpenRouter model-provider extension: the OpenAI-wire router as a `ModelClient` backend, plus
image generation as a tool.

Core ships direct Anthropic + OpenAI clients; OpenRouter is a router over many upstreams, so per
spec it is an extension, never core. It speaks the OpenAI Chat Completions wire against
`openrouter.ai/api/v1`, so it reuses the SDK's `openai_messages` translation and `openai_sdk_client`
factory and adds only what is OpenRouter's own: an id->slug projection, a reasoning-effort budget on
`extra_body`, and a dead-provider re-route that excludes an upstream returning an empty completion.
The manifest enumerates one complete `ModelSpec` per slug it offers — price, cutoff, context window,
reasoning — so the registry serves those ids exactly like any other, with no catch-all router.

Image models are not in that registry: `ModelClient.complete` yields text, tool calls and token
usage, and a registered spec is a brain an agent can be pinned to, so an image model is instead one
`generate_image` tool over OpenRouter's dedicated Image API on the same key and bill. It prices its
own call — the unit is an image, not a token — and books it onto the turn through
`ToolContext.meter_images`, the ledger's `images` dimension."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, get_args

import httpx
import openai
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage
from pydantic import BaseModel, Field, model_validator

from ufo.sdk.accounting import MICRO_USD_PER_USD
from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo.sdk.models import (
    ModelEvent,
    ModelPrice,
    ModelRequest,
    ModelResponseTruncated,
    ModelSpec,
    ReasoningSupport,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    Usage,
    openai_messages,
    openai_sdk_client,
)
from ufo.sdk.tools import ImageContent, TextContent, ToolContext, ToolDef, ToolResult

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

OPENROUTER_CONTEXT_WINDOW = 200_000
_REASONS = ReasoningSupport(supported=True, tools_with_reasoning=True)

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


@dataclass(frozen=True)
class ImageModelLimits:
    """What one allowlisted model's providers actually serve, and what an image lists at when the
    response prices nothing. OpenRouter rejects a generation parameter the serving provider does not
    offer, so a model's bounds differ from the tool's own caps and are held on the way in."""

    max_images: int
    aspect_ratios: frozenset[str]
    list_micro_usd: int


IMAGE_MODELS: dict[ImageModel, ImageModelLimits] = {
    "bytedance-seed/seedream-4.5": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=EVERY_ASPECT_RATIO,
        list_micro_usd=40_000,
    ),
    "openai/gpt-image-2": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=EVERY_ASPECT_RATIO,
        list_micro_usd=130_000,
    ),
    "black-forest-labs/flux.2-klein-4b": ImageModelLimits(
        max_images=1,
        aspect_ratios=EVERY_ASPECT_RATIO,
        list_micro_usd=14_000,
    ),
    "black-forest-labs/flux.2-pro": ImageModelLimits(
        max_images=1,
        aspect_ratios=EVERY_ASPECT_RATIO,
        list_micro_usd=30_000,
    ),
    "recraft/recraft-v4.1": ImageModelLimits(
        max_images=MAX_IMAGES_PER_CALL,
        aspect_ratios=frozenset({"1:1", "4:3", "3:4", "16:9", "9:16"}),
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
    derives from max_tokens when the model's spec supports it; `off` omits it."""

    client: openai.AsyncOpenAI
    spec: ModelSpec

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
        effort = self.spec.default_reasoning(request.reasoning, request.tools)
        if effort not in ("off", "auto"):
            extra_body["reasoning"] = {"effort": effort}
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


def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient:
    return OpenRouterModelClient(
        client=openai_sdk_client(key, base_url=OPENROUTER_BASE_URL),
        spec=spec,
    )


def _openrouter(
    id: str,
    price: ModelPrice,
    cutoff: str,
    context_window: int = OPENROUTER_CONTEXT_WINDOW,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_NAME,
        client=_model_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=context_window,
        reasoning=_REASONS,
        api_surface="chat",
        key_slot=OPENROUTER_KEY_SLOT,
        key_env=OPENROUTER_API_KEY_ENV,
    )


OPENROUTER_MODEL_SPECS = (
    _openrouter(
        "google/gemini-2.5-pro",
        ModelPrice(input=1_000_000, output=10_000_000, cache_read=0, cache_write=0),
        "2025-01",
    ),
    _openrouter(
        "z-ai/glm-5.2",
        ModelPrice(input=1_000_000, output=3_000_000, cache_read=0, cache_write=0),
        "2026-03",
    ),
    _openrouter(
        "moonshotai/kimi-k3",
        ModelPrice(input=3_000_000, output=15_000_000, cache_read=300_000, cache_write=0),
        "2026-04",
        context_window=1_000_000,
    ),
)


class GenerateImageInput(BaseModel):
    prompt: str = Field(
        max_length=MAX_IMAGE_PROMPT_CHARS,
        description="What to draw. Describe subject, composition, lighting and any text verbatim.",
    )
    name: str = Field(
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
    resolution: Literal["1K", "2K"] | None = Field(
        default=None,
        description=(
            "Resolution tier. Only bytedance-seed/seedream-4.5 takes one; omit it for the others."
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
    user_description: str = Field(
        description="What you are generating, in plain language for the activity timeline."
    )

    @model_validator(mode="after")
    def _within_model_limits(self) -> "GenerateImageInput":
        """Hold the call to what the chosen model serves. OpenRouter answers 400 for a generation
        parameter its provider does not offer, so a combination the allowlist already knows is
        unservable is refused here, where the model reads the reason and can pick another."""
        limits = IMAGE_MODELS[self.model]
        if self.n > limits.max_images:
            raise ValueError(f"{self.model} draws at most {limits.max_images} image(s) per call")
        if self.aspect_ratio is not None and self.aspect_ratio not in limits.aspect_ratios:
            raise ValueError(
                f"{self.model} does not take aspect_ratio {self.aspect_ratio}; it accepts "
                f"{', '.join(sorted(limits.aspect_ratios))}"
            )
        return self


class OpenRouterImageError(RuntimeError):
    """OpenRouter answered success with a body this tool cannot use — no image in it, or an image
    over the byte cap. Raised so the turn reports the failure instead of saving a broken file."""


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
                json=args.model_dump(exclude_none=True, exclude={"name", "user_description"}),
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
        path = f"{IMAGE_DIR}/{args.name}-{index}{suffix}"
        await ctx.sandbox.write_file(path, image.raw)
        return path

    def _charge(self, body: object, args: GenerateImageInput, images: int) -> int:
        """What this generation cost, in micro-USD: OpenRouter's own charge, else the upstream
        charge a BYOK call reports in its place, else the model's list rate per image. A reported
        zero prices nothing, so it falls through rather than metering the turn at nothing."""
        usage = body.get("usage") if isinstance(body, dict) else None
        details = usage.get("cost_details") if isinstance(usage, dict) else None
        for cost in (
            usage.get("cost") if isinstance(usage, dict) else None,
            details.get("upstream_inference_cost") if isinstance(details, dict) else None,
        ):
            if isinstance(cost, int | float) and not isinstance(cost, bool) and cost > 0:
                return round(float(cost) * MICRO_USD_PER_USD)
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
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        models=OPENROUTER_MODEL_SPECS,
        tools=(GENERATE_IMAGE_TOOL,),
        credentials=(
            CredentialSlot(
                name=OPENROUTER_KEY_SLOT,
                description=(
                    "The OpenRouter API key this workspace's models and image generation run on. "
                    "Read host-side and never exposed to chat or the sandbox; unset, the "
                    f"platform's {OPENROUTER_API_KEY_ENV} serves the deploy."
                ),
            ),
        ),
    )
