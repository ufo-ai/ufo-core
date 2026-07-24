"""The model registry: one `ModelSpec` per model, keyed by exact id — the single point every seam
funnels through for routing, pricing, and the model's facts. `model_registry` folds core's specs and
every manifest-contributed spec into one table; a duplicate id fails loud at build and an unknown id
fails loud at `spec`, rather than across a mid-turn 400, a render crash, and a silent zero bill. A
provider resolves its own api key when the turn selects it, so a serve missing one key runs fine
until an agent pinned to that backend actually runs. See RFC 0018."""

from dataclasses import dataclass

from ufo.config import Config
from ufo.credentials import CredentialSlotUnset
from ufo.ext.manifest import Manifest
from ufo.models.catalog import core_model_specs
from ufo.models.interface import AUTO_MODEL, PROVIDER_ANTHROPIC, PROVIDER_OPENAI, ModelClient
from ufo.models.pricing import Pricing, pricing_from
from ufo.models.spec import ModelSpec
from ufo.workspace import ws_current


@dataclass(frozen=True)
class ModelRegistry:
    """The active models as one id-keyed table — core's direct specs first, then every
    manifest-contributed spec — with the merged price table their entries build. `client_for` builds
    the client of the spec registered under an id; `pricing` prices any model against the merged
    table; `model_key_env` is onboarding's eager key check. An id no spec describes fails loud."""

    specs: dict[str, ModelSpec]
    pricing: Pricing
    auto_model: str

    def resolve(self, model: str) -> str:
        """Map the model-agnostic `auto` sentinel to the deploy's configured concrete model; a
        pinned id passes through."""
        return self.auto_model if model == AUTO_MODEL else model

    def spec(self, model: str) -> ModelSpec:
        """The spec registered under `model`, or a loud failure — the one seam every fact reads
        through, so an unknown id fails here once rather than at each consuming layer."""
        try:
            return self.specs[model]
        except KeyError as miss:
            raise ValueError(f"no model registered for id {model!r}") from miss

    async def client_for(self, model: str) -> ModelClient:
        """The client serving `model`, built for the ambient workspace from the key resolved for its
        spec — the workspace's BYOK secret if set, else the platform default from env. Built per
        call so a workspace's own key is honoured and a rotated platform key takes effect without a
        restart. Fetching the key asserts a bound workspace (`ws_current`); a key set nowhere fails
        loud."""
        spec = self.spec(model)
        if not spec.key_slot and not spec.key_env:
            return spec.client(spec, "")
        try:
            key = await ws_current().credential(spec.key_slot, spec.key_env or None)
        except CredentialSlotUnset as unset:
            needed = spec.key_env or spec.key_slot.upper()
            raise RuntimeError(
                f"model {model!r} needs a key: set env {needed} or the workspace's "
                f"{spec.key_slot!r} BYOK slot"
            ) from unset
        return spec.client(spec, key)

    def key_slot_for(self, model: str) -> str | None:
        """The BYOK slot whose stored value would key this model's calls — total instead of loud: an
        unclaimed model (a historical ledger row from a removed spec) or a keyless spec answers
        None, so a billing export labels it platform-served rather than wedging on it."""
        spec = self.specs.get(model)
        if spec is None or not spec.key_slot:
            return None
        return spec.key_slot

    def model_key_env(self, model: str, config: Config) -> str | None:
        """The env var onboarding requires set before this model's first turn: a core provider's
        configured key, or None for a contributed spec that resolves its own key lazily at turn
        time — an env core cannot name to check eagerly."""
        provider = self.spec(model).provider
        if provider == PROVIDER_ANTHROPIC:
            return config.models.anthropic_api_key_env
        if provider == PROVIDER_OPENAI:
            return config.models.openai_api_key_env
        return None


def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry:
    """Core's direct specs followed by every extension-contributed spec, indexed by id, and the
    price table their entries build. A duplicate id — two specs claiming one slug — fails loud, so a
    contributed model never silently shadows a core one."""
    core = core_model_specs(config.models.anthropic_api_key_env, config.models.openai_api_key_env)
    specs: dict[str, ModelSpec] = {}
    for spec in (*core, *(spec for manifest in manifests for spec in manifest.models)):
        if spec.id in specs:
            raise ValueError(f"two model specs registered for id {spec.id!r}")
        specs[spec.id] = spec
    return ModelRegistry(
        specs=specs,
        pricing=pricing_from({model: spec.price for model, spec in specs.items()}),
        auto_model=config.models.auto_model,
    )
