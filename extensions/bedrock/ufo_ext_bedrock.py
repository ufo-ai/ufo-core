"""Amazon Bedrock Mantle model provider over Anthropic and OpenAI-compatible APIs.

Each model is one `ModelSpec`: the Anthropic ids route to core's `AnthropicClient` over the Bedrock
Mantle Anthropic endpoint; the OpenAI ids route to core's `OpenAIClient`, which renders the Chat
Completions or Responses request from the spec's `api_surface`. This extension carries no request
translation of its own — only the endpoint and AWS-keyed client each spec builds."""

import os
from typing import cast

import anthropic

from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo.sdk.models import (
    AnthropicClient,
    ApiSurface,
    ModelPrice,
    ModelSpec,
    OpenAIClient,
    ReasoningSupport,
    openai_sdk_client,
)

NAME = "bedrock"
VERSION = "0.1.0"
PROVIDER_NAME = "bedrock"
BEDROCK_API_KEY_ENV = "AWS_BEARER_TOKEN_BEDROCK"
BEDROCK_KEY_SLOT = "bedrock_api_key"
AWS_REGION_ENV = "AWS_REGION"
AWS_DEFAULT_REGION_ENV = "AWS_DEFAULT_REGION"
PROVIDER_TIMEOUT_SECONDS = 60.0

ANTHROPIC_CONTEXT_WINDOW = 200_000
OPENAI_CONTEXT_WINDOW = 272_000
GPT_OSS_CONTEXT_WINDOW = 128_000
REASONS = ReasoningSupport(supported=True, tools_with_reasoning=True)


def bedrock_region() -> str:
    region = os.environ.get(AWS_REGION_ENV) or os.environ.get(AWS_DEFAULT_REGION_ENV)
    if not region:
        raise RuntimeError(
            f"Bedrock needs a region: set {AWS_REGION_ENV} or {AWS_DEFAULT_REGION_ENV}"
        )
    return region


def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient:
    return AnthropicClient(
        client=cast(
            anthropic.AsyncAnthropic,
            anthropic.AsyncAnthropicBedrockMantle(
                api_key=key,
                aws_region=bedrock_region(),
                max_retries=0,
                timeout=PROVIDER_TIMEOUT_SECONDS,
            ),
        ),
        spec=spec,
    )


def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient:
    region = bedrock_region()
    base = (
        f"https://bedrock-mantle.{region}.api.aws/openai/v1"
        if spec.api_surface == "responses"
        else f"https://bedrock-mantle.{region}.api.aws/v1"
    )
    return OpenAIClient(client=openai_sdk_client(key, base), spec=spec)


def _anthropic(id: str, price: ModelPrice, cutoff: str) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_NAME,
        client=_anthropic_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=ANTHROPIC_CONTEXT_WINDOW,
        reasoning=REASONS,
        api_surface="chat",
        key_slot=BEDROCK_KEY_SLOT,
        key_env=BEDROCK_API_KEY_ENV,
    )


def _openai(
    id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=PROVIDER_NAME,
        client=_openai_client,
        price=price,
        knowledge_cutoff=cutoff,
        context_window=window,
        reasoning=REASONS,
        api_surface=api_surface,
        key_slot=BEDROCK_KEY_SLOT,
        key_env=BEDROCK_API_KEY_ENV,
    )


BEDROCK_MODEL_SPECS = (
    _anthropic(
        "anthropic.claude-fable-5",
        ModelPrice(10_000_000, 50_000_000, 1_000_000, 12_500_000),
        "2026-01",
    ),
    _anthropic(
        "anthropic.claude-opus-4-8",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
        "2026-01",
    ),
    _anthropic(
        "anthropic.claude-opus-4-7",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
        "2026-01",
    ),
    _anthropic(
        "anthropic.claude-opus-4-6-v1",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
        "2025-08",
    ),
    _anthropic(
        "anthropic.claude-sonnet-5",
        ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
        "2026-01",
    ),
    _anthropic(
        "anthropic.claude-sonnet-4-6",
        ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
        "2025-08",
    ),
    _openai(
        "openai.gpt-oss-20b",
        ModelPrice(70_000, 300_000, 0, 0),
        "2024-06",
        GPT_OSS_CONTEXT_WINDOW,
        "chat",
    ),
    _openai(
        "openai.gpt-oss-120b",
        ModelPrice(150_000, 600_000, 0, 0),
        "2024-06",
        GPT_OSS_CONTEXT_WINDOW,
        "chat",
    ),
    _openai(
        "openai.gpt-5.4",
        ModelPrice(2_500_000, 15_000_000, 250_000, 2_500_000),
        "2025-08",
        OPENAI_CONTEXT_WINDOW,
        "responses",
    ),
    _openai(
        "openai.gpt-5.5",
        ModelPrice(5_000_000, 30_000_000, 500_000, 5_000_000),
        "2025-12",
        OPENAI_CONTEXT_WINDOW,
        "responses",
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=BEDROCK_KEY_SLOT,
                description="Amazon Bedrock API key used by Mantle model APIs.",
            ),
        ),
        models=BEDROCK_MODEL_SPECS,
    )
