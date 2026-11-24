"""Core's model catalog: one `ModelSpec` per model core ships directly over Anthropic and OpenAI,
and the pricing the ledger stamps against. The single home for core model facts — price, knowledge
cutoff, context window, routing, and reasoning capability. An extension contributes its own specs
through the manifest; the registry folds core's and theirs into one id-keyed table."""

from ufo.harness.models.anthropic import AnthropicClient, anthropic_sdk_client, is_oauth_credential
from ufo.harness.models.interface import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from ufo.harness.models.openai import (
    OpenAIClient,
    chatgpt_account_id,
    codex_sdk_client,
    openai_sdk_client,
)
from ufo.harness.models.pricing import ModelPrice, pricing_from
from ufo.harness.models.spec import ApiSurface, ModelSpec, ReasoningSupport

ANTHROPIC_KEY_SLOT = "anthropic_api_key"
OPENAI_KEY_SLOT = "openai_api_key"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
OPENAI_KEY_ENV = "OPENAI_API_KEY"

ANTHROPIC_CONTEXT_WINDOW = 200_000
ANTHROPIC_LONG_CONTEXT_WINDOW = 1_000_000
HAIKU_5_5_LOWER_TIER_WINDOW = 100_000
OPENAI_CONTEXT_WINDOW = 272_000

REASONS_WITH_TOOLS = ReasoningSupport(supported=True, tools_with_reasoning=True)
DEFAULT_REASONS_WITH_TOOLS = ReasoningSupport(
    supported=True, tools_with_reasoning=True, default_on=True
)
REQUIRED_REASONS_WITH_TOOLS = ReasoningSupport(
    supported=True, tools_with_reasoning=True, default_on=True, can_disable=False
)


def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient:
    return AnthropicClient(
        client=anthropic_sdk_client(key), spec=spec, oauth=is_oauth_credential(key)
    )


def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient:
    account = chatgpt_account_id(key)
    if account is None:
        return OpenAIClient(client=openai_sdk_client(key), spec=spec)
    return OpenAIClient(client=codex_sdk_client(key, account), spec=spec, codex=True)


def _anthropic(
    id: str,
    price: ModelPrice,
    cutoff: str,
    key_env: str,
    *,
    context_window: int = ANTHROPIC_CONTEXT_WINDOW,
    reasoning: ReasoningSupport = REASONS_WITH_TOOLS,
    forced_tool_choice: bool = True,
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
        forced_tool_choice=forced_tool_choice,
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
    """Core's direct backends, built with the key-env names the deploy configured. Anthropic cache
    writes cost 1.25x base input at 5m and 2x at 1h. Cache reads cost 0.1x input except on Opus 5.5
    and Fable 5.2, where they cost 0.05x and 0.025x. The current Opus, Sonnet, and Fable models
    require reasoning and reject forced tool choice. Haiku 5.5 defaults to reasoning but permits
    both disabling it and forcing one tool.

    OpenAI's current Astra, Sol, Terra, and Luna models run on the Responses surface. Cached input
    costs 0.05x base input for Astra, Sol, and Terra, and 0.1x for Luna. A 30-minute cache write
    costs 1.25x base input. Each accepts 1,050,000
    tokens, but a prompt at or above 272,000 is billed at 2x input and 1.5x output. One rate per
    token class cannot express that tier, so these rows carry the 272,000-token window where their
    registered rate is true. Haiku 5.5 similarly carries the 100,000-token window below its 5x
    long-context tier rather than the full 1M tokens it accepts."""
    return (
        _anthropic(
            "claude-fable-5-2",
            ModelPrice(8_000_000, 40_000_000, 200_000, 10_000_000, 16_000_000),
            "2026-06",
            anthropic_key_env,
            context_window=ANTHROPIC_LONG_CONTEXT_WINDOW,
            reasoning=REQUIRED_REASONS_WITH_TOOLS,
            forced_tool_choice=False,
        ),
        _anthropic(
            "claude-opus-5-5",
            ModelPrice(4_000_000, 20_000_000, 200_000, 5_000_000, 8_000_000),
            "2026-06",
            anthropic_key_env,
            context_window=ANTHROPIC_LONG_CONTEXT_WINDOW,
            reasoning=REQUIRED_REASONS_WITH_TOOLS,
            forced_tool_choice=False,
        ),
        _anthropic(
            "claude-sonnet-5-5",
            ModelPrice(2_000_000, 10_000_000, 200_000, 2_500_000, 4_000_000),
            "2026-06",
            anthropic_key_env,
            context_window=ANTHROPIC_LONG_CONTEXT_WINDOW,
            reasoning=REQUIRED_REASONS_WITH_TOOLS,
            forced_tool_choice=False,
        ),
        _anthropic(
            "claude-haiku-5-5",
            ModelPrice(100_000, 500_000, 10_000, 125_000, 200_000),
            "2026-06",
            anthropic_key_env,
            context_window=HAIKU_5_5_LOWER_TIER_WINDOW,
            reasoning=DEFAULT_REASONS_WITH_TOOLS,
        ),
        _openai(
            "gpt-6.2-astra",
            ModelPrice(8_000_000, 40_000_000, 400_000, 0, 0, 10_000_000),
            "2026-05",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-6.2-sol",
            ModelPrice(2_000_000, 10_000_000, 100_000, 0, 0, 2_500_000),
            "2026-06",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-6.1-terra",
            ModelPrice(1_000_000, 5_000_000, 50_000, 0, 0, 1_250_000),
            "2026-06",
            openai_key_env,
            api_surface="responses",
        ),
        _openai(
            "gpt-6-luna",
            ModelPrice(100_000, 500_000, 10_000, 0, 0, 125_000),
            "2026-05",
            openai_key_env,
            api_surface="responses",
        ),
    )


CORE_MODEL_SPECS = core_model_specs(ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV)
CORE_PRICES = {spec.id: spec.price for spec in CORE_MODEL_SPECS}
CORE_PRICING = pricing_from(CORE_PRICES)
PRICE_DIGEST = CORE_PRICING.digest
