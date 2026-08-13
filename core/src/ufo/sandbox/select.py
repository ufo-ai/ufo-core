"""The one sandbox carrier a deploy selects."""

from urllib.parse import urlparse

from ufo.config import Config
from ufo.ext.loader import NotRegisteredError
from ufo.ext.manifest import CarrierSpec, Manifest
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import Carrier


def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]:
    """The one sandbox backend this process runs, chosen by `[sandbox] backend`: core's default
    `local` carrier plus every carrier an extension contributes via its `carriers` Manifest point
    (`docker`, `e2b`, a remote runner). An extension name that collides with the built-in or another
    extension fails loud, and a backend name no carrier registers fails loud — so the selected name
    resolves to exactly one factory, built once here and held on the runtime. Answers the selected
    `CarrierSpec` alongside the carrier: a caller reaching a sandbox from the host has to know
    whether the backend runs off-cluster, and a portal offers the agent's sandbox-size setting
    exactly where the backend declares sizes. A remote backend with no `[sandbox] proxy_public_url`
    fails loud too: its sandbox could reach neither the process-local proxy nor a metered egress
    route, so it would run open — never a silent default."""
    specs: dict[str, CarrierSpec] = {"local": CarrierSpec(name="local", factory=LocalCarrier)}
    for manifest in manifests:
        for spec in manifest.carriers:
            if spec.name in specs:
                raise RuntimeError(f"two carriers register backend {spec.name!r}")
            specs[spec.name] = spec
    selected = specs.get(config.sandbox.backend)
    if selected is None:
        raise NotRegisteredError(
            f"sandbox backend {config.sandbox.backend!r} is not a registered carrier "
            f"(have {sorted(specs)})"
        )
    if selected.off_cluster:
        public_url = config.sandbox.proxy_public_url
        if not public_url:
            raise RuntimeError(
                f"sandbox backend {config.sandbox.backend!r} is remote and cannot reach the "
                "process-local egress proxy; set [sandbox] proxy_public_url to the externally "
                "reachable HTTPS proxy URL so in-sandbox egress is credential-injected, "
                "default-denied, and metered"
            )
        parsed = urlparse(public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError(
                f"sandbox backend {config.sandbox.backend!r} is remote; "
                "[sandbox] proxy_public_url must be an HTTPS URL so its run token is encrypted "
                "in transit"
            )
    return selected.factory(), selected
