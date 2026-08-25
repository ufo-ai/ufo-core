"""Core's model catalog: one `ModelSpec` per model core ships directly over Anthropic and OpenAI,
and the pricing the ledger stamps against. The single home for core model facts — price, knowledge
cutoff, context window, routing, and reasoning capability. An extension contributes its own specs
through the manifest; the registry folds core's and theirs into one id-keyed table."""

from ufo.models.anthropic import AnthropicClient, anthropic_sdk_client
from ufo.models.interface import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from ufo.models.openai import OpenAIClient, openai_sdk_client
from ufo.models.pricing import ModelPrice, pricing_from
from ufo.models.spec import ApiSurface, ModelSpec, ReasoningSupport

ANTHROPIC_KEY_SLOT = "anthropic_api_key"
OPENAI_KEY_SLOT = "openai_api_key"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
OPENAI_KEY_ENV = "OPENAI_API_KEY"

ANTHROPIC_CONTEXT_WINDOW = 200_000
ANTHROPIC_LONG_CONTEXT_WINDOW = 1_000_000
OPENAI_CONTEXT_WINDOW = 272_000

REASONS_WITH_TOOLS = ReasoningSupport(supported=True, tools_with_reasoning=True)
DEFAULT_REASONS_WITH_TOOLS = ReasoningSupport(
    supported=True, tools_with_reasoning=True, default_on=True
)


def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient:
    return AnthropicClient(client=anthropic_sdk_client(key), spec=spec)


def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient:
    return OpenAIClient(client=openai_sdk_client(key), spec=spec)


def _anthropic(
    id: str,
    price: ModelPrice,
    cutoff: str,
    key_env: str,
    *,
    context_window: int = ANTHROPIC_CONTEXT_WINDOW,
    reasoning: ReasoningSupport = REASONS_WITH_TOOLS,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_ANTHROPIC,
        client=_anthropic_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=context_window,
        reasoning=reasoning,
        api_surface="chat",
        key_slot=ANTHROPIC_KEY_SLOT,
        key_env=key_env,
    )


def _openai(
    id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface = "chat"
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_OPENAI,
        client=_openai_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=OPENAI_CONTEXT_WINDOW,
        reasoning=REASONS_WITH_TOOLS,
        api_surface=api_surface,
        key_slot=OPENAI_KEY_SLOT,
        key_env=key_env,
    )


def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]:
    """Core's direct backends, built with the key-env names the deploy configured. The GPT-5.6
    models are called on the Responses surface (`api_surface="responses"`) — each rejects `tools` +
    `reasoning_effort` together on `/v1/chat/completions` (#568), so they declare the surface that
    renders the legal request rather than tripping a mid-turn 400 — with 30-minute cache writes at
    1.25x base input and cache reads at 0.1x. `claude-opus-5` carries the 1M-token context
    window it ships with, at Opus-tier pricing unchanged from Opus 4.8. Anthropic cache writes are
    1.25x base input at 5m and 2x at 1h; cache reads are 0.1x.

    The GPT-5.6 family accepts 1,050,000 tokens, but a request whose input passes 272,000 is billed
    at 2x input and 1.5x output for the whole request, which one rate per token class cannot
    express. `context_window` is what the turn loop compacts against, so these rows carry the
    272,000 the registered rate is true at rather than the window the provider accepts: a larger
    number would let a turn grow into a tier this table bills at half price."""
    return (
        _anthropic(
            "claude-opus-5",
            ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000),
            "2026-05",
            anthropic_key_env,
            context_window=ANTHROPIC_LONG_CONTEXT_WINDOW,
            reasoning=DEFAULT_REASONS_WITH_TOOLS,
        ),
        _anthropic(
            "claude-opus-4-8",
            ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000),
            "2026-01",
            anthropic_key_env,
        ),
        _anthropic(
            "claude-opus-4-7",
            ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000),
            "2026-01",
            anthropic_key_env,
        ),
        _anthropic(
            "claude-opus-4-6",
            ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000),
            "2025-08",
            anthropic_key_env,
        ),
        _anthropic(
            "claude-sonnet-5",
            ModelPrice(2_000_000, 10_000_000, 200_000, 2_500_000, 4_000_000),
            "2026-01",
            anthropic_key_env,
            reasoning=DEFAULT_REASONS_WITH_TOOLS,
        ),
        _anthropic(
            "claude-sonnet-4-6",
            ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000, 6_000_000),
            "2025-08",
            anthropic_key_env,
        ),
        _anthropic(
            "claude-haiku-4-5",
            ModelPrice(1_000_000, 5_000_000, 100_000, 1_250_000, 2_000_000),
            "2025-07",
            anthropic_key_env,
        ),
        _openai(
            "gpt-5.6-sol",
            ModelPrice(4_000_000, 20_000_000, 400_000, 0, 0, 5_000_000),
            "2026-02",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-5.6-terra",
            ModelPrice(2_000_000, 12_000_000, 200_000, 0, 0, 2_500_000),
            "2026-02",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-5.6-luna",
            ModelPrice(200_000, 1_200_000, 20_000, 0, 0, 250_000),
            "2026-02",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-5.5",
            ModelPrice(5_000_000, 30_000_000, 500_000, 5_000_000, 5_000_000),
            "2025-12",
            openai_key_env,
        ),
        _openai(
            "gpt-5.4",
            ModelPrice(2_500_000, 15_000_000, 250_000, 2_500_000, 2_500_000),
            "2025-08",
            openai_key_env,
        ),
        _openai(
            "gpt-5.4-mini",
            ModelPrice(750_000, 4_500_000, 75_000, 750_000, 750_000),
            "2025-08",
            openai_key_env,
        ),
        _openai(
            "gpt-5.4-nano",
            ModelPrice(200_000, 1_250_000, 20_000, 200_000, 200_000),
            "2025-08",
            openai_key_env,
        ),
    )


CORE_MODEL_SPECS = core_model_specs(ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV)
CORE_PRICES = {spec.id: spec.price for spec in CORE_MODEL_SPECS}
CORE_PRICING = pricing_from(CORE_PRICES)
PRICE_DIGEST = CORE_PRICING.digest
