"""Resolve a verified user's org domain to a tenant — join an existing one or provision a fresh one
— entirely through the control plane's HTTP API.

The gateway holds no cluster authority: it counts tenants for the domain, POSTs the identical
`DeployRequest` contract `ufoctl deploy` speaks (assembled as a plain JSON dict, since extensions
import only `ufo.sdk`), polls the tenant to Ready, and adds a member — each a call the closed
control plane authorizes. Multiple tenants for one domain is unrepresentable by invariant, so it
escalates rather than guessing."""

import hashlib
from dataclasses import dataclass

import httpx

ASSISTANT_HOSTED_PACK = "assistant_hosted"
TENANT_NAME_SHA_LEN = 8
CONFIG_TOML = '[pack]\nname = "assistant_hosted"\n\n[memory]\nindex_backend = "turbopuffer"\n'


class TooManyTenantsForDomain(RuntimeError):
    """A domain resolved to more than one tenant — a human must reconcile before join is safe."""


@dataclass(frozen=True)
class TenantView:
    name: str
    phase: str
    workspace_id: str | None


@dataclass(frozen=True)
class DeployTarget:
    base_domain: str
    bundle_image: str


def slugify_domain(domain: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in domain.lower())
    return "-".join(part for part in cleaned.split("-") if part)


def mint_tenant_name(domain: str) -> str:
    suffix = hashlib.sha256(domain.encode()).hexdigest()[:TENANT_NAME_SHA_LEN]
    return f"{slugify_domain(domain)}-{suffix}"


def image_ref(ref: str) -> dict[str, str]:
    repository, separator, digest = ref.partition("@")
    if not separator:
        raise ValueError(f"image {ref!r} must be digest-pinned as repository@sha256:<64 hex>")
    return {"repository": repository, "digest": digest}


def deploy_request(name: str, owner_email: str, target: DeployTarget) -> dict[str, object]:
    """The `DeployRequest` wire shape (snake_case JSON) the control plane consumes — the identical
    contract `ufoctl deploy` produces. `postgres`, `secrets`, and `sandbox_image` are left to their
    defaults: the hosted tier runs the e2b carrier, which needs no sandbox image, and the tier is
    chosen server-side."""
    return {
        "tenant": {
            "name": name,
            "host": f"{name}.{target.base_domain}",
            "owner_email": owner_email,
        },
        "bundle_image": image_ref(target.bundle_image),
        "config_toml": CONFIG_TOML,
        "pack": ASSISTANT_HOSTED_PACK,
    }


@dataclass(frozen=True)
class JoinOrProvision:
    http: httpx.AsyncClient
    target: DeployTarget

    async def tenants_for_domain(self, domain: str) -> tuple[TenantView, ...]:
        response = await self.http.get("/v1/tenants", params={"orgDomain": domain})
        response.raise_for_status()
        return tuple(_view(item) for item in response.json())

    async def provision(self, domain: str, owner_email: str) -> str:
        name = mint_tenant_name(domain)
        request = deploy_request(name, owner_email, self.target)
        response = await self.http.post("/v1/deploy", json=request)
        response.raise_for_status()
        return name

    async def tenant_status(self, name: str) -> TenantView:
        response = await self.http.get(f"/v1/tenants/{name}")
        response.raise_for_status()
        return _view(response.json())

    async def join(self, name: str, email: str) -> str:
        response = await self.http.post(f"/v1/tenants/{name}/members", json={"email": email})
        response.raise_for_status()
        return str(response.json()["workspace_id"])


def _view(item: dict[str, object]) -> TenantView:
    workspace_id = item.get("workspace_id")
    return TenantView(
        name=str(item["tenant"]),
        phase=str(item["phase"]),
        workspace_id=None if workspace_id is None else str(workspace_id),
    )
