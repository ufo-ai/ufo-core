"""The deploy-request contract — the one boundary between OSS core and this closed control plane.

``ufoctl deploy --backend k8s`` (in ufo, generate-only, beside ``bundle.py``) produces a
``DeployRequest`` and POSTs it here; this control plane consumes it. Everything in it comes from
seams core already emits (RFC 0004 "What ``ufoctl deploy`` hands the control plane"): the bundle
image digest, the sandbox image digest, the operator-authored ``ufo.toml``, the tenant
identity, the secret *inventory* (names, never values), and the pack.

The wire form is snake_case JSON — identical on both sides, so the OSS producer and this consumer
agree with no alias layer. The same shape is persisted verbatim as a ``Tenant`` custom resource's
``.spec`` (``kube.tenant_object``), which the operator reconciles.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

SECRET_CREDENTIAL_KEY = "UFO_CREDENTIAL_KEY"
SECRET_ARTIFACT_TOKEN = "UFO_ARTIFACT_TOKEN_SECRET"
MINTED_SECRETS = (SECRET_CREDENTIAL_KEY, SECRET_ARTIFACT_TOKEN)


class ImageRef(BaseModel):
    """A digest-pinned OCI image the cluster pulls — ``repository@sha256:…``. Never a mutable tag:
    the control plane runs exactly the bytes ``ufoctl bundle`` / ``build_template`` produced."""

    model_config = ConfigDict(extra="forbid")
    repository: str
    digest: str

    @field_validator("digest")
    @classmethod
    def _digest_is_pinned(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
            raise ValueError("digest must be a full sha256:<64 hex> content digest")
        return value

    @property
    def ref(self) -> str:
        return f"{self.repository}@{self.digest}"


class TenantIdentity(BaseModel):
    """Operator-supplied: ``--remote <name>`` (the tenant slug → namespace + labels), ``--host``
    (the public hostname TLS terminates at), ``--email`` (the owner ``ufoctl init`` onboards)."""

    model_config = ConfigDict(extra="forbid")
    name: str
    host: str
    owner_email: str

    @field_validator("name")
    @classmethod
    def _dns_label(cls, value: str) -> str:
        if (
            not value
            or len(value) > 40
            or not value.replace("-", "").isalnum()
            or not value[0].isalpha()
        ):
            raise ValueError("tenant name must be a short DNS-1123 label starting with a letter")
        return value.lower()

    @property
    def namespace(self) -> str:
        return f"ufo-{self.name}"


class PostgresModel(StrEnum):
    """How the tenant's Postgres is isolated — both transparent to core (RFC 0003 §5). ``database``:
    a database per tenant (hard isolation, the default). ``rls``: an RLS-scoped role on shared
    Postgres (dense). Core serves ONE workspace and never assumes RLS either way."""

    DATABASE = "database"
    RLS = "rls"


class SecretInventory(BaseModel):
    """Secret *names*, never values (RFC 0004). ``minted`` are generated per tenant by the control
    plane (the Fernet store key, the artifact HMAC). ``platform`` are the env-var names sourced from
    the cluster's platform Secret (model API keys, cloud credentials, carrier keys). BYOK provider
    secrets (Slack, Composio, Exa) are absent — they land encrypted in ``credential`` rows through
    chat onboarding after the workspace is up."""

    model_config = ConfigDict(extra="forbid")
    minted: tuple[str, ...] = MINTED_SECRETS
    platform: tuple[str, ...] = ()


class DeployRequest(BaseModel):
    """The whole contract. ``config_toml`` is the operator-authored ``ufo.toml`` (validated by
    the producer against core's ``Config``); the reconciler overlays the infra knobs (database,
    blob, hub, connect, serve host) it provisions. ``pack`` is the activation set label — it must
    match the config's ``[pack] name`` and is denormalized here so the control plane labels the ns
    without parsing TOML."""

    model_config = ConfigDict(extra="forbid")
    tenant: TenantIdentity
    bundle_image: ImageRef
    sandbox_image: ImageRef
    config_toml: str
    pack: str
    postgres: PostgresModel = PostgresModel.DATABASE
    secrets: SecretInventory = SecretInventory()


class DeployStatus(BaseModel):
    """The poll response and the ``Tenant`` custom resource's ``.status``. ``phase`` walks
    Pending → Provisioning → Ready (or Failed); ``url`` is set once the ingress is admitting."""

    model_config = ConfigDict(extra="forbid")
    tenant: str
    phase: Literal["Pending", "Provisioning", "Ready", "Failed"]
    url: str | None = None
    message: str = ""
