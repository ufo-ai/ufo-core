"""The model registry: one `ModelSpec` per model, keyed by exact id — the single point every seam
funnels through for routing, pricing, and the model's facts. `model_registry` folds core's specs and
every manifest-contributed spec into one table; a duplicate id fails loud at build and an unknown id
fails loud at `spec`, rather than across a mid-turn 400, a render crash, and a silent zero bill. A
provider resolves its own api key when the turn selects it, so a serve missing one key runs fine
until an agent pinned to that backend actually runs. See RFC 0018."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from ufo.config import Config
from ufo.harness.models.catalog import core_model_specs
from ufo.harness.models.interface import (
    AUTO_MODEL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    ModelClient,
    ModelEvent,
    ModelRequest,
)
from ufo.harness.models.pricing import Pricing, pricing_from
from ufo.harness.models.spec import ModelSpec
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialValueInvalid
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.workspace import ws_current


@dataclass(frozen=True)
class _RebuiltOnRejection:
    """A member's account client, rebuilt once if the provider rejects its token mid-turn.

    One client serves a whole turn, and a coding turn runs to a hundred rounds — far longer than an
    access token's remaining life when the turn happens to start near expiry. Rebuilding re-reads
    the slot, which is what refreshes a spent grant, so the round that would have died on a 401
    carries on under the pair that refresh bought. The retry is spent only before the first event:
    a stream that already delivered cannot be replayed without repeating what the turn has seen.

    Once, and it is the unwrapped client that serves the retry — a rebuild is wrapped again by the
    registry, so retrying through the wrapper would make every rejection open another, and a
    credential the rebuild cannot change (an account without entitlement, a grant revoked at the
    provider while the stored one still looks live) would recurse instead of surfacing the fault
    that names the account to connect again."""

    registry: "ModelRegistry"
    model: str
    built: ModelClient

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        delivered = False
        try:
            async for event in self.built.complete(request):
                delivered = True
                yield event
            return
        except CredentialValueInvalid:
            if delivered:
                raise
        rebuilt = await self.registry.client_for(self.model)
        once = rebuilt.built if isinstance(rebuilt, _RebuiltOnRejection) else rebuilt
        async for event in once.complete(request):
            yield event


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
        needed = spec.key_env or spec.key_slot.upper()
        try:
            key = await ws_current().model_credential(spec.key_slot, spec.key_env or None, model)
        except CredentialSlotUnset as unset:
            raise RuntimeError(
                f"model {model!r} needs a key: set env UFO_{needed} (or {needed}) or the "
                f"workspace's {spec.key_slot!r} BYOK slot"
            ) from unset
        try:
            key.encode("ascii")
        except UnicodeEncodeError as error:
            raise CredentialValueInvalid(
                f"model {model!r} key contains non-ASCII characters: env UFO_{needed} (or "
                f"{needed}) or the workspace's {spec.key_slot!r} BYOK slot holds a value the "
                "provider wire cannot carry."
            ) from error
        built = spec.client(spec, key)
        if spec.key_slot and ws_current().member_routed_call(spec.key_slot, model):
            return _RebuiltOnRejection(registry=self, model=model, built=built)
        return built

    def provider_for(self, model: str) -> str:
        """The provider that serves `model` — the `provider` metric dimension its calls are metered
        under, so an off-turn call splits by backend the way a turn's round does. Loud on an id no
        spec describes, like every other read through `spec`."""
        return self.spec(model).provider

    def key_slot_for(self, model: str) -> str | None:
        """The BYOK slot whose stored value would key this model's calls — total instead of loud: an
        unclaimed model (a historical ledger row from a removed spec) or a keyless spec answers
        None, so a billing export labels it platform-served rather than wedging on it.

        `auto` resolves first, because an agent stores what it was authored with and a workspace
        created the normal way stores `auto`. Asked about that sentinel a bare lookup answers None,
        which reads as platform-served for an agent whose calls the workspace's own key pays."""
        spec = self.specs.get(self.resolve(model))
        if spec is None or not spec.key_slot:
            return None
        return spec.key_slot

    def model_key_env(self, model: str, config: Config) -> str | None:
        """The env var onboarding requires set before this model's first turn: a core provider's
        configured key, or None for a contributed spec that resolves its own key lazily at turn
        time — an env core cannot name to check eagerly. Resolves the `auto` sentinel first, because
        the key the first turn needs is the key of the model that turn will actually run."""
        provider = self.spec(self.resolve(model)).provider
        if provider == PROVIDER_ANTHROPIC:
            return config.models.anthropic_api_key_env
        if provider == PROVIDER_OPENAI:
            return config.models.openai_api_key_env
        return None


def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry:
    """Core's direct specs followed by every extension-contributed spec, indexed by id, and the
    price table their entries build. A duplicate id — two specs claiming one slug — fails loud, so a
    contributed model never silently shadows a core one, and so does a configured model naming no
    registered spec: an agent that defers its model resolves through `auto_model` every turn, every
    ambient reply decision resolves through `ambient_reply_model`, and every background job's own
    model call resolves through `background_jobs_model`, so a typo in any of the three is one boot
    failure rather than a mid-turn failure per workspace."""
    core = core_model_specs(config.models.anthropic_api_key_env, config.models.openai_api_key_env)
    specs: dict[str, ModelSpec] = {}
    for spec in (*core, *(spec for manifest in manifests for spec in manifest.models)):
        if spec.id in specs:
            raise ValueError(f"two model specs registered for id {spec.id!r}")
        specs[spec.id] = spec
    if config.models.auto_model not in specs:
        raise ValueError(
            f"models.auto_model {config.models.auto_model!r} is not a registered model id — "
            "every agent that defers its model resolves through it"
        )
    if config.models.ambient_reply_model not in specs:
        raise ValueError(
            f"models.ambient_reply_model {config.models.ambient_reply_model!r} is not a registered "
            "model id — every ambient reply decision resolves through it"
        )
    if config.models.background_jobs_model not in specs:
        raise ValueError(
            f"models.background_jobs_model {config.models.background_jobs_model!r} is not a "
            "registered model id — every background job's model call resolves through it"
        )
    return ModelRegistry(
        specs=specs,
        pricing=pricing_from({model: spec.price for model, spec in specs.items()}),
        auto_model=config.models.auto_model,
    )
