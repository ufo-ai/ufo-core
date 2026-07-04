"""The Docker carrier extension's selection seam.

The container-integration proof lives in test_file_tools.py (Docker-gated). This asserts only the
seam: with `[sandbox] backend = "docker"` and the docker extension's Manifest present, `serve`
resolves exactly this extension's carrier by name — the deploy swaps to it without core naming
Docker. Constructing the carrier needs no daemon, so this is not Docker-gated."""

from pathlib import Path

import selfhost_ext_docker as docker_ext
from selfhost_ext_docker import DockerCarrier

from selfhost.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from selfhost.serve import _select_carrier


def test_config_backend_docker_resolves_the_extension_contributed_carrier() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="docker"),
    )
    carrier = _select_carrier(config, (docker_ext.manifest(),))
    assert isinstance(carrier, DockerCarrier)
