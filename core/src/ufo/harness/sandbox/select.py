"""The sandbox carriers a deploy keeps live, and which of them new sandboxes open on."""

from collections.abc import Mapping
from dataclasses import dataclass

from ufo.config import Config
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import Carrier
from ufo.runtime.ext.manifest import CarrierSpec, Manifest, NotRegisteredError


@dataclass(frozen=True)
class DeployCarriers:
    """Every carrier this deploy runs: the default backend new sandboxes open on, plus one entry
    per `[sandbox] resume_backends` name — a backend kept live only for the stored handles bearing
    its scheme, so a deploy moves to a new provider without stranding the workspaces the old one
    still holds."""

    carrier: Carrier
    spec: CarrierSpec
    resume: Mapping[str, tuple[Carrier, CarrierSpec]]


def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers:
    """Build the deploy's carriers from `[sandbox] backend` and `[sandbox] resume_backends`: core's
    default `local` carrier plus every carrier an extension contributes via its `carriers` Manifest
    point (`docker`, `e2b`). An extension name that collides with the built-in or
    another extension fails loud, a name no carrier registers fails loud, and a resume backend
    repeating the default fails loud — so every configured name resolves to exactly one factory,
    built once here and held for the process's life. A backend whose sandbox runs off the cluster
    fails loud wherever it appears unless `[sandbox] proxy_url` names the proxy service: its sandbox
    reaches the network only through that service, so without it the sandbox would run open — never
    a silent default."""
    specs: dict[str, CarrierSpec] = {"local": CarrierSpec(name="local", factory=LocalCarrier)}
    for manifest in manifests:
        for spec in manifest.carriers:
            if spec.name in specs:
                raise RuntimeError(f"two carriers register backend {spec.name!r}")
            specs[spec.name] = spec
    resume_names = config.sandbox.resume_backends
    for name in resume_names:
        if name == config.sandbox.backend:
            raise RuntimeError(f"resume backend {name!r} is already the default backend")
    if len(set(resume_names)) != len(resume_names):
        raise RuntimeError(f"[sandbox] resume_backends repeats a backend: {resume_names}")
    carrier, spec = _built(specs, config, config.sandbox.backend)
    resume = {name: _built(specs, config, name) for name in resume_names}
    return DeployCarriers(carrier=carrier, spec=spec, resume=resume)


def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]:
    selected = specs.get(name)
    if selected is None:
        raise NotRegisteredError(
            f"sandbox backend {name!r} is not a registered carrier (have {sorted(specs)})"
        )
    if selected.off_cluster and config.sandbox.proxy_url is None:
        raise RuntimeError(
            f"sandbox backend {name!r} runs off the cluster and egresses only through the proxy "
            "service; set [sandbox] proxy_url to its https URL so in-sandbox egress is "
            "credential-injected, default-denied, and metered"
        )
    return selected.factory(), selected
