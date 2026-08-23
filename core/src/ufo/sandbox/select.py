"""The sandbox carriers a deploy keeps live, and which of them new sandboxes open on."""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from ufo.config import Config
from ufo.ext.loader import NotRegisteredError
from ufo.ext.manifest import CarrierSpec, Manifest
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import Carrier


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
    point (`docker`, `e2b`, `daytona`). An extension name that collides with the built-in or
    another extension fails loud, a name no carrier registers fails loud, and a resume backend
    repeating the default fails loud — so every configured name resolves to exactly one factory,
    built once here and held for the process's life. A remote backend with no `[sandbox]
    proxy_public_url` fails loud wherever it appears: its sandbox could reach neither the
    process-local proxy nor a metered egress route, so it would run open — never a silent
    default."""
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


def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]:
    """The default backend alone — the carrier new sandboxes open on."""
    selected = select_carriers(config, manifests)
    return selected.carrier, selected.spec


def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]:
    selected = specs.get(name)
    if selected is None:
        raise NotRegisteredError(
            f"sandbox backend {name!r} is not a registered carrier (have {sorted(specs)})"
        )
    if selected.off_cluster:
        public_url = config.sandbox.proxy_public_url
        if not public_url:
            raise RuntimeError(
                f"sandbox backend {name!r} is remote and cannot reach the process-local egress "
                "proxy; set [sandbox] proxy_public_url to the externally reachable HTTPS proxy "
                "URL so in-sandbox egress is credential-injected, default-denied, and metered"
            )
        parsed = urlparse(public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError(
                f"sandbox backend {name!r} is remote; [sandbox] proxy_public_url must be an "
                "HTTPS URL so its run token is encrypted in transit"
            )
    return selected.factory(), selected
