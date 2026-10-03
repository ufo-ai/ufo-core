"""The model registry: one `ModelSpec` per model, keyed by exact id — the single point every seam
funnels through for routing, pricing, and the model's facts. `model_registry` folds core's specs and
every manifest-contributed spec into one table; a duplicate id fails loud at build and an unknown id
fails loud at `spec`, rather than across a mid-turn 400, a render crash, and a silent zero bill. A
provider resolves its own api key when the turn selects it, so a serve missing one key runs fine
until an agent pinned to that backend actually runs."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from ufo.config import Config
from ufo.harness.models.catalog import core_model_specs
from ufo.harness.models.grant import GrantRefusedRefresh
from ufo.harness.models.interface import (
    AUTO_MODEL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    ModelAccountUnavailable,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ModelStreamStart,
)
from ufo.harness.models.pricing import Pricing, pricing_from
from ufo.harness.models.spec import ModelSpec
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialValueInvalid
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.workspace import (
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    Funding,
    ModelFundingChanged,
    ModelPayer,
    ResolvedModelClient,
    ws_current,
)


@dataclass(frozen=True)
class _UnavailableAccount:
    message: str

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        raise ModelAccountUnavailable(self.message)
        yield ModelStreamStart()


@dataclass(frozen=True)
class _RebuiltOnRejection:
    """Retries on the unwrapped client: the registry wraps every rebuild, so retrying through the
    wrapper would recurse on a credential the rebuild cannot change."""

    registry: "ModelRegistry"
    model: str
    built: ModelClient
    funding: Funding
    payer: str

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
        if rebuilt.funding != self.funding or rebuilt.payer != self.payer:
            raise ModelFundingChanged("model payer changed during provider retry")
        once = rebuilt.client.built if isinstance(rebuilt.client, _RebuiltOnRejection) else rebuilt
        async for event in once.complete(request):
            yield event


@dataclass(frozen=True)
class ModelRoute:
    """One model and the exact funding identity an attempt froze for it."""

    model: str
    funding: Funding
    payer: str


@dataclass
class ModelRoutes:
    """The ordered models remaining after the route currently serving a turn."""

    registry: "ModelRegistry"
    remaining: tuple[ModelRoute, ...]

    async def next(self) -> tuple[ModelSpec, ModelClient, ModelRoute] | None:
        if not self.remaining:
            return None
        route, self.remaining = self.remaining[0], self.remaining[1:]
        resolved = await self.registry.client_for(route.model)
        if resolved.funding != route.funding or resolved.payer != route.payer:
            raise ModelFundingChanged("model payer changed during route failover")
        return self.registry.spec(route.model), resolved.client, route


@dataclass
class ServingModel:
    """The model a turn's rounds run on, held in one place: the id its requests name, the spec whose
    provider, reasoning and context window the turn reads, and the client the rounds call. Every
    fact the turn derives from a model id reads off this holder, so a move leaves nothing keyed to
    the model the turn began on.

    A turn with alternate routes also holds `routes`; `move` swaps the holder onto the next one
    when the provider rate-limits the one in hand. The engine moves only on a round the
    provider refused before its first event — the one shape a rate-limit fault takes, since a
    stream that already delivered fails as an interrupted stream instead — so the moved round
    replays the same canonical messages under the new model id, and a crash-recovery replay of the
    recorded rounds moves at the same round the first run did. A transcript carries one thing the
    second provider cannot read — the first one's reasoning blocks — and each client drops the
    other's."""

    model: str
    spec: ModelSpec
    client: ModelClient
    funding: Funding = PLATFORM_FUNDED
    payer: str = PLATFORM_PAYER
    routes: ModelRoutes | None = None

    async def move(self) -> bool:
        """Move onto the next model route, or say that this turn has none left."""
        if self.routes is None:
            return False
        moved = await self.routes.next()
        if moved is None:
            return False
        self.spec, self.client, route = moved
        self.model, self.funding, self.payer = route.model, route.funding, route.payer
        return True


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

    async def client_for(self, model: str) -> ResolvedModelClient:
        """The client serving `model`, bound to the funding class and exact payer resolved with its
        credential. The workspace's BYOK secret wins when set, else the platform default from env.
        Built per call so a rotated key takes effect without a restart. Fetching it asserts a bound
        workspace (`ws_current`); a key set nowhere fails loud."""
        spec = self.spec(model)
        if not spec.key_slot and not spec.key_env:
            return ResolvedModelClient(spec.client(spec, ""), PLATFORM_FUNDED, PLATFORM_PAYER)
        needed = spec.key_env or spec.key_slot.upper()
        workspace = ws_current()
        routed = workspace.routed_model_call(model)
        try:
            credential = await workspace.model_credential(
                spec.key_slot, spec.key_env or None, model
            )
        except GrantRefusedRefresh as refused:
            if not routed:
                raise
            payer = await workspace.model_payer(spec.key_slot, spec.key_env or None, model)
            return ResolvedModelClient(
                _UnavailableAccount(str(refused)), payer.funding, payer.payer
            )
        except CredentialSlotUnset as unset:
            if routed:
                raise RuntimeError(
                    f"model {model!r} needs the exact account credential bound to this turn"
                ) from unset
            raise RuntimeError(
                f"model {model!r} needs a key: set env UFO_{needed} (or {needed}) or the "
                f"workspace's {spec.key_slot!r} BYOK slot"
            ) from unset
        try:
            credential.value.encode("ascii")
        except UnicodeEncodeError as error:
            raise CredentialValueInvalid(
                f"model {model!r} key contains non-ASCII characters: env UFO_{needed} (or "
                f"{needed}) or the workspace's {spec.key_slot!r} BYOK slot holds a value the "
                "provider wire cannot carry."
            ) from error
        built = spec.client(spec, credential.value)
        if routed:
            built = _RebuiltOnRejection(
                registry=self,
                model=model,
                built=built,
                funding=credential.funding,
                payer=credential.payer,
            )
        return ResolvedModelClient(built, credential.funding, credential.payer)

    async def payer_for(self, model: str) -> ModelPayer:
        """Resolve the funding identity `client_for` will bind without constructing its client."""
        spec = self.spec(model)
        if not spec.key_slot and not spec.key_env:
            return ModelPayer(PLATFORM_FUNDED, PLATFORM_PAYER)
        return await ws_current().model_payer(spec.key_slot, spec.key_env or None, model)

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
    ambient reply decision resolves through `ambient_reply_model`, every background job's own model
    call resolves through `background_jobs_model`, and every subagent route names its models
    directly, so a typo in any of them is one boot failure rather than a mid-turn failure per
    workspace."""
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
    for manifest in manifests:
        for profile in manifest.subagents:
            for model in profile.models:
                resolved = config.models.auto_model if model == AUTO_MODEL else model
                if resolved not in specs:
                    raise ValueError(
                        f"subagent profile {profile.name!r} model {model!r} is not a registered "
                        "model id"
                    )
    return ModelRegistry(
        specs=specs,
        pricing=pricing_from({model: spec.price for model, spec in specs.items()}),
        auto_model=config.models.auto_model,
    )
