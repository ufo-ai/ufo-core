"""The model backend registry: which client serves a model, and the price table that prices it.

Core ships direct Anthropic + OpenAI clients; a model-provider extension contributes more through
its Manifest `models` point. `model_registry` folds core's two providers and every manifest's into
one ordered table — core first — that the turn loop selects a client from and the accounting layer
prices against. A provider resolves its own API key when the turn selects it, so a serve missing one
key runs fine until an agent pinned to that backend actually runs; a model no provider claims fails
loud rather than guessing a backend."""

from dataclasses import dataclass

from ufo.accounting import Pricing, pricing_with
from ufo.config import Config
from ufo.credentials import CredentialSlotUnset
from ufo.ext.manifest import Manifest, ModelProviderSpec
from ufo.models.anthropic import AnthropicClient, anthropic_sdk_client
from ufo.models.interface import (
    ANTHROPIC_MODEL_PREFIXES,
    AUTO_MODEL,
    OPENAI_MODEL_PREFIXES,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    ModelClient,
)
from ufo.models.openai import OpenAIClient, openai_sdk_client
from ufo.workspace import ws_current

ANTHROPIC_KEY_SLOT = "anthropic_api_key"
OPENAI_KEY_SLOT = "openai_api_key"


@dataclass(frozen=True)
class ModelRegistry:
    """The active model backends as one ordered table — core's direct clients first, then every
    manifest-contributed provider — with the merged price table their entries build. `client_for`
    returns the client of the first provider whose matcher claims the model id; `pricing` prices any
    model against the merged table; `model_key_env` is onboarding's eager key check. A model no
    provider claims fails loud."""

    providers: tuple[ModelProviderSpec, ...]
    pricing: Pricing
    auto_model: str

    def resolve(self, model: str) -> str:
        """Map the model-agnostic `auto` sentinel to the deploy's configured concrete model; a
        pinned id passes through. An agent projection keeps its declared value — resolution is a
        runtime concern the turn applies before selecting a client and pricing the run."""
        return self.auto_model if model == AUTO_MODEL else model

    async def client_for(self, model: str) -> ModelClient:
        """The client serving `model`, built for the ambient workspace from the key resolved for its
        provider — the workspace's BYOK secret if set, else the platform default from env. Built per
        call so a workspace's own key is honoured and a rotated platform key takes effect without a
        restart. Fetching the key asserts a bound workspace (`ws_current`), so a call is always
        attributable to the workspace that made it; a key set nowhere fails loud."""
        spec = self._provider_for(model)
        if not spec.key_slot and not spec.key_env:
            return spec.client(model, "")
        try:
            key = await ws_current().credential(spec.key_slot, spec.key_env or None)
        except CredentialSlotUnset as unset:
            needed = spec.key_env or spec.key_slot.upper()
            raise RuntimeError(
                f"model provider {spec.name!r} needs a key: set env {needed} or the workspace's "
                f"{spec.key_slot!r} BYOK slot"
            ) from unset
        return spec.client(model, key)

    def model_key_env(self, model: str, config: Config) -> str | None:
        """The env var onboarding requires set before this model's first turn: a core provider's
        configured key, or None for a contributed provider that resolves its own key lazily at turn
        time — an env core cannot name to check eagerly."""
        name = self._provider_for(model).name
        if name == PROVIDER_ANTHROPIC:
            return config.models.anthropic_api_key_env
        if name == PROVIDER_OPENAI:
            return config.models.openai_api_key_env
        return None

    def _provider_for(self, model: str) -> ModelProviderSpec:
        provider = next((spec for spec in self.providers if spec.matches(model)), None)
        if provider is None:
            raise ValueError(f"no model provider serves model {model!r}")
        return provider


def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry:
    """Core's two direct backends followed by every extension-contributed provider, and the price
    table merging their entries over core's rates. Core is first, so a bare Anthropic/OpenAI id
    always resolves to its direct client; a contributed provider serves only what core does not."""
    core = (
        ModelProviderSpec(
            name=PROVIDER_ANTHROPIC,
            matches=lambda model: model.startswith(ANTHROPIC_MODEL_PREFIXES),
            client=lambda model, key: AnthropicClient(client=anthropic_sdk_client(key)),
            key_slot=ANTHROPIC_KEY_SLOT,
            key_env=config.models.anthropic_api_key_env,
        ),
        ModelProviderSpec(
            name=PROVIDER_OPENAI,
            matches=lambda model: model.startswith(OPENAI_MODEL_PREFIXES),
            client=lambda model, key: OpenAIClient(client=openai_sdk_client(key)),
            key_slot=OPENAI_KEY_SLOT,
            key_env=config.models.openai_api_key_env,
        ),
    )
    providers = (*core, *(spec for manifest in manifests for spec in manifest.models))
    contributed = {model: price for spec in providers for model, price in spec.prices}
    return ModelRegistry(
        providers=providers,
        pricing=pricing_with(contributed),
        auto_model=config.models.auto_model,
    )
