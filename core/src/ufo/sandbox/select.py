"""The one sandbox carrier a deploy selects."""

from collections.abc import Callable
from urllib.parse import urlparse

from ufo.config import Config
from ufo.ext.loader import NotRegisteredError
from ufo.ext.manifest import Manifest
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import Carrier


def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, bool]:
    """The one sandbox backend this process runs, chosen by `[sandbox] backend`: core's default
    `local` carrier plus every carrier an extension contributes via its `carriers` Manifest point
    (`docker`, `e2b`, a remote runner). An extension name that collides with the built-in or another
    extension fails loud, and a backend name no carrier registers fails loud — so the selected name
    resolves to exactly one factory, built once here and held on the runtime. Answers whether that
    backend runs off-cluster alongside it, since a caller reaching a sandbox from the host has to
    know where the sandbox is. A remote backend with no `[sandbox] proxy_public_url` fails loud too:
    its sandbox could reach neither the process-local proxy nor a metered egress route, so it would
    run open — never a silent default."""
    factories: dict[str, Callable[[], Carrier]] = {"local": LocalCarrier}
    off_cluster: set[str] = set()
    for manifest in manifests:
        for spec in manifest.carriers:
            if spec.name in factories:
                raise RuntimeError(f"two carriers register backend {spec.name!r}")
            factories[spec.name] = spec.factory
            if spec.off_cluster:
                off_cluster.add(spec.name)
    factory = factories.get(config.sandbox.backend)
    if factory is None:
        raise NotRegisteredError(
            f"sandbox backend {config.sandbox.backend!r} is not a registered carrier "
            f"(have {sorted(factories)})"
        )
    if config.sandbox.backend in off_cluster:
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
    return factory(), config.sandbox.backend in off_cluster
