import json
from pathlib import Path

import httpx
import pytest

from ufo.cli import _drive_deploy
from ufo.config import Config, load_config
from ufo.deploy import (
    Deploy,
    DeployImage,
    DeployRequest,
    DeployResolutionError,
    WorkspaceIdentity,
    resolve_request,
)

DIGEST = "sha256:" + "a" * 64
BUNDLE = f"ghcr.io/acme/ufo@{DIGEST}"
SANDBOX = f"ghcr.io/acme/sandbox@{DIGEST}"
WORKSPACE = WorkspaceIdentity(owner_email="you@acme.com", default_name="assistant")

BASE_CONFIG = """\
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"

[pack]
name = "assistant"
"""


def _config(tmp_path: Path, deploy: str = "") -> tuple[Config, Path]:
    path = tmp_path / "ufo.toml"
    path.write_text(BASE_CONFIG + deploy)
    return load_config(path), path


def test_resolve_derives_host_name_and_owner(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path,
        f'\n[deploy]\nbackend = "k8s"\nbase_domain = "ufo.app"\n'
        f'bundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n',
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    assert request.tenant.name == "assistant"  # from the workspace agent
    assert request.tenant.host == "assistant.ufo.app"  # <name>.<base_domain>
    assert request.tenant.owner_email == "you@acme.com"  # from the workspace owner
    assert request.postgres == "database"  # backend-determined, not asked
    assert "ANTHROPIC_API_KEY" in request.secrets.platform
    assert request.config_toml == path.read_text()


def test_resolve_uses_explicit_host_and_name(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path,
        f'\n[deploy]\nbackend = "k8s"\nname = "acme"\nhost = "chat.acme.com"\n'
        f'bundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n',
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    assert request.tenant.name == "acme"
    assert request.tenant.host == "chat.acme.com"


def test_compose_backend_defaults_host_to_localhost(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path, f'\n[deploy]\nbundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n'
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    assert request.tenant.host == "localhost"  # compose is the default backend


def test_resolve_fails_loud_naming_every_missing_field(tmp_path: Path) -> None:
    config, path = _config(tmp_path, '\n[deploy]\nbackend = "k8s"\n')
    with pytest.raises(DeployResolutionError) as error:
        resolve_request(config.deploy, config, path, WORKSPACE)
    message = str(error.value)
    assert "[deploy].host or [deploy].base_domain" in message
    assert "[deploy].bundle_image" in message
    assert "[deploy].sandbox_image" in message


def test_image_parse_requires_a_digest() -> None:
    assert DeployImage.parse(BUNDLE).digest == DIGEST
    with pytest.raises(ValueError):
        DeployImage.parse("ghcr.io/acme/ufo:latest")


def test_request_round_trips_through_json(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path,
        f'\n[deploy]\nbackend = "k8s"\nhost = "chat.acme.com"\n'
        f'bundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n',
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    assert DeployRequest.model_validate(json.loads(request.model_dump_json())) == request


def test_build_writes_bundle_and_request(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path,
        f'\n[deploy]\nbackend = "k8s"\nname = "acme"\nhost = "chat.acme.com"\n'
        f'bundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n',
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    out = tmp_path / "out"
    result = Deploy(config_path=path, catalog=None, out=out, request=request).build()
    assert (out / "bundle" / "Dockerfile").exists()
    assert result.request_path == out / "deploy-request.json"
    assert DeployRequest.model_validate_json(result.request_path.read_text()) == request


def test_build_without_a_request_writes_only_the_bundle(tmp_path: Path) -> None:
    _, path = _config(tmp_path)
    out = tmp_path / "out"
    result = Deploy(config_path=path, catalog=None, out=out, request=None).build()
    assert (out / "bundle" / "Dockerfile").exists()
    assert result.request_path is None
    assert not (out / "deploy-request.json").exists()


async def test_drive_deploy_posts_then_polls_to_ready(tmp_path: Path) -> None:
    config, path = _config(
        tmp_path,
        f'\n[deploy]\nbackend = "k8s"\nhost = "acme.ufo.app"\n'
        f'bundle_image = "{BUNDLE}"\nsandbox_image = "{SANDBOX}"\n',
    )
    request = resolve_request(config.deploy, config, path, WORKSPACE)
    posted: dict[str, object] = {}
    polls = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posted["body"] = json.loads(req.content)
            return httpx.Response(200, json={"tenant": "assistant", "phase": "Pending"})
        polls["count"] += 1
        phase = "Ready" if polls["count"] >= 2 else "Provisioning"
        return httpx.Response(
            200, json={"tenant": "assistant", "phase": phase, "url": "https://acme.ufo.app"}
        )

    import ufo.cli as cli

    cli.DEPLOY_POLL_SECONDS = 0.0
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp") as client:
        status = await _drive_deploy(client, request)

    assert posted["body"]["tenant"]["host"] == "acme.ufo.app"  # type: ignore[index]
    assert status.phase == "Ready"
    assert status.url == "https://acme.ufo.app"
    assert polls["count"] == 2
