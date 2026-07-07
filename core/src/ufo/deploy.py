"""`ufoctl deploy`: freeze a deploy into a control-plane request, mirroring `ufoctl bundle`.

Like `bundle`, `deploy` GENERATES a runnable recipe and executes nothing itself: it emits the deploy
request the `--backend k8s` control plane (the closed-source `ufo-control`, RFC 0004) consumes,
beside the bundle artifact `bundle` already produces. The verb takes no per-run flags — it is
config-driven and derived: the identity lives in `[deploy]` (set once), the owner and a default
tenant name come from the workspace `ufoctl init` created, and the Postgres model is backend-
determined, never a user knob. The `--remote` post is the one execution step, at CLI startup off the
serve loop, and it imports no orchestrator: it is an httpx client to the control-plane API. Core
never imports or assumes `ufo-control` (the dependency is one-way); this module defines the shape.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ufo.bundle import Bundle, BundleResult
from ufo.config import Config, DeployConfig
from ufo.ext.store import Catalog

DEPLOY_REQUEST_NAME = "deploy-request.json"
BUNDLE_SUBDIR = "bundle"
MINTED_SECRETS = ("UFO_CREDENTIAL_KEY", "UFO_ARTIFACT_TOKEN_SECRET")
DIGEST_PREFIX = "sha256:"
DIGEST_HEX_LEN = 64
DEFAULT_BUNDLE_REPOSITORY = "ghcr.io/metalcraftai/ufo"
DEFAULT_SANDBOX_REPOSITORY = "ghcr.io/metalcraftai/ufo-sandbox"


class DeployImage(BaseModel):
    """A digest-pinned OCI image the control plane pulls — `repository@sha256:…`. Never a mutable
    tag: the cluster runs exactly the bytes `ufoctl bundle` / the sandbox build produced. Digests
    come from a build+push, so they are supplied in `[deploy]`, not derived by convention."""

    model_config = ConfigDict(extra="forbid")
    repository: str
    digest: str

    @field_validator("digest")
    @classmethod
    def _digest_is_pinned(cls, value: str) -> str:
        if not value.startswith(DIGEST_PREFIX) or len(value) != len(DIGEST_PREFIX) + DIGEST_HEX_LEN:
            raise ValueError("digest must be a full sha256:<64 hex> content digest")
        return value

    @classmethod
    def parse(cls, ref: str) -> "DeployImage":
        repository, sep, digest = ref.partition("@")
        if not sep:
            raise ValueError(f"image {ref!r} must be digest-pinned as repository@sha256:<64 hex>")
        return cls(repository=repository, digest=digest)


class TenantIdentity(BaseModel):
    """The tenant slug (`[deploy].name` or the workspace agent name), the public hostname (`host` or
    `<name>.<base_domain>`), and the owner email read from the workspace `init` onboarded."""

    model_config = ConfigDict(extra="forbid")
    name: str
    host: str
    owner_email: str

    @field_validator("name")
    @classmethod
    def _dns_label(cls, value: str) -> str:
        stripped = value.replace("-", "")
        if not value or len(value) > 40 or not stripped.isalnum() or not value[0].isalpha():
            raise ValueError("tenant name must be a short DNS-1123 label starting with a letter")
        return value.lower()


class SecretInventory(BaseModel):
    """Secret env-var NAMES, never values. `minted` are generated per tenant by the control plane
    (the Fernet store key, the artifact HMAC); `platform` are the names the tenant's `serve` reads
    from the cluster's platform Secret (the model API keys). BYOK provider secrets are absent — they
    land encrypted in `credential` rows through chat onboarding after the workspace is up."""

    model_config = ConfigDict(extra="forbid")
    minted: tuple[str, ...] = MINTED_SECRETS
    platform: tuple[str, ...] = ()


class DeployRequest(BaseModel):
    """The contract the control plane consumes. `config_toml` is this deploy's `ufo.toml`
    verbatim; the control plane overlays the infra knobs (database, blob, hub, connect) it
    provisions. `pack` is the activation set label, denormalized from `[pack] name`."""

    model_config = ConfigDict(extra="forbid")
    tenant: TenantIdentity
    bundle_image: DeployImage
    sandbox_image: DeployImage
    config_toml: str
    pack: str
    postgres: Literal["database", "rls"] = "database"
    secrets: SecretInventory = SecretInventory()


class DeployStatus(BaseModel):
    """The control plane's poll response — the tenant's reconciled phase and workspace URL.
    `workspace_id` is the tenant's minted workspace uuid, surfaced once the tenant is Ready so a
    caller (the onboarding backend) can mint a member bearer against it."""

    model_config = ConfigDict(extra="forbid")
    tenant: str
    phase: Literal["Pending", "Provisioning", "Ready", "Failed"]
    url: str | None = None
    message: str = ""
    workspace_id: str | None = None


@dataclass(frozen=True)
class WorkspaceIdentity:
    """The bits of the deploy request read from the workspace `ufoctl init` created — never asked
    for again. `owner_email` is the onboarded owner; `default_name` is the workspace's agent name,
    the fallback tenant slug when `[deploy].name` is unset."""

    owner_email: str
    default_name: str


class DeployResolutionError(Exception):
    """A deploy request could not be assembled — a required, underivable `[deploy]` field is absent.
    The message names exactly what to add."""


def resolve_request(
    deploy: DeployConfig, config: Config, config_path: Path, workspace: WorkspaceIdentity
) -> DeployRequest:
    """Assemble the deploy request from `[deploy]` + the workspace, deriving what it can and failing
    loud (naming every missing field at once) on what it cannot."""
    missing: list[str] = []
    if deploy.host is None and deploy.base_domain is None and deploy.backend != "compose":
        missing.append(
            "[deploy].host or [deploy].base_domain (a public hostname for the k8s backend)"
        )
    if deploy.bundle_image is None:
        missing.append(
            f"[deploy].bundle_image (digest-pinned, e.g. {DEFAULT_BUNDLE_REPOSITORY}@sha256:…)"
        )
    if deploy.sandbox_image is None:
        missing.append(
            f"[deploy].sandbox_image (digest-pinned, e.g. {DEFAULT_SANDBOX_REPOSITORY}@sha256:…)"
        )
    if config.pack.name is None:
        missing.append("[pack].name (the activation set the tenant runs)")
    if missing:
        raise DeployResolutionError("deploy needs " + "; ".join(missing))
    assert deploy.bundle_image is not None and deploy.sandbox_image is not None
    assert config.pack.name is not None
    name = deploy.name or _slug(workspace.default_name)
    host = deploy.host or (f"{name}.{deploy.base_domain}" if deploy.base_domain else "localhost")
    try:
        return DeployRequest(
            tenant=TenantIdentity(name=name, host=host, owner_email=workspace.owner_email),
            bundle_image=DeployImage.parse(deploy.bundle_image),
            sandbox_image=DeployImage.parse(deploy.sandbox_image),
            config_toml=config_path.read_text(),
            pack=config.pack.name,
            # Backend-determined, never a user knob: k8s provisions a database per tenant, compose a
            # local one — both "database". RLS is a k8s tier chosen server-side, not exposed here.
            postgres="database",
            secrets=SecretInventory(platform=_platform_secrets(config)),
        )
    except ValidationError as error:
        raise DeployResolutionError(
            f"could not derive a valid tenant name {name!r} — set [deploy].name to a DNS-1123 label"
        ) from error


def _slug(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower())
    cleaned = cleaned.strip("-")
    return cleaned if cleaned and cleaned[0].isalpha() else "workspace"


def _platform_secrets(config: Config) -> tuple[str, ...]:
    names = (config.models.anthropic_api_key_env, config.models.openai_api_key_env)
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class DeployResult:
    out: Path
    bundle: BundleResult
    request_path: Path | None
    request: DeployRequest | None


@dataclass(frozen=True)
class Deploy:
    """Produce the deploy recipe into `out`: the bundle artifact (via `Bundle`) always, plus the k8s
    deploy request when one resolved. Generate-only — mirrors `Bundle` and executes nothing; the
    caller posts the request."""

    config_path: Path
    catalog: Catalog | None
    out: Path
    request: DeployRequest | None

    def build(self) -> DeployResult:
        bundle = Bundle(
            config_path=self.config_path, catalog=self.catalog, out=self.out / BUNDLE_SUBDIR
        ).build()
        request_path: Path | None = None
        if self.request is not None:
            request_path = self.out / DEPLOY_REQUEST_NAME
            request_path.write_text(self.request.model_dump_json(indent=2) + "\n")
        return DeployResult(
            out=self.out, bundle=bundle, request_path=request_path, request=self.request
        )
