"""Resolve a verified user's org domain to a tenant — join an existing one or provision a fresh one.

The gateway now runs inside the control-plane package, so it drives the cluster directly rather than
over the deploy API: it counts tenants for the domain by their org-domain label, applies a `Tenant`
object built from the same `ufo.deploy.DeployRequest` contract `ufoctl deploy` produces, polls the
tenant to Ready, and adds a member as the Postgres owner. The operator mints and persists the
workspace uuid on the tenant's status during reconcile; the gateway reads it back from there —
`status.workspaceId` — for both the poll and the join. Multiple tenants for one domain is
unrepresentable by invariant, so it escalates rather than guessing."""

import hashlib
from dataclasses import dataclass

from ufo.deploy import DeployImage, DeployRequest, DeployStatus, TenantIdentity

from ufo_control.kube import KubeClient, status_from_tenant
from ufo_control.members import MemberRequest, add_member

ASSISTANT_HOSTED_PACK = "assistant_hosted"
TENANT_NAME_SHA_LEN = 8
CONFIG_TOML = (
    '[pack]\nname = "assistant_hosted"\n\n'
    '[memory]\nindex_backend = "turbopuffer"\n\n'
    '[research]\nsearch_provider = "exa"\n\n'
    '[sandbox]\nbackend = "e2b"\n\n'
    '[browser]\ncdp_provider = "sandbox_chrome"\n'
)


class TooManyTenantsForDomain(RuntimeError):
    """A domain resolved to more than one tenant — a human must reconcile before join is safe."""


class TenantNotReady(RuntimeError):
    """The domain's tenant exists but is not serving — joining would sign the member into a deploy
    that cannot answer them."""


@dataclass(frozen=True)
class JoinedTenant:
    """Where an existing dedicated deploy took the member: the tenant slug, the workspace uuid its
    operator minted, and the URL the member's `ufo` surface talks to."""

    name: str
    workspace_id: str
    url: str


@dataclass(frozen=True)
class TenantJoin:
    """Resolve a verified domain to its one existing tenant and join the member — the piece of
    resolution both tiers share, so an org with a dedicated deploy is never forked onto a second
    workspace. Reads Tenant CRs, never applies one: provisioning stays the enterprise tier's."""

    kube: KubeClient
    base_domain: str

    async def join_existing(self, domain: str, email: str) -> JoinedTenant | None:
        """The domain's tenant joined, or None when the cluster proves the domain has none — a
        listing failure raises rather than answering None, because 'could not look' read as 'no
        tenant' silently forks the org. An ambiguous domain or a tenant not yet (or no longer)
        serving refuses loudly instead of signing the member into a deploy that cannot answer."""
        statuses = [
            status_from_tenant(obj) for obj in await self.kube.list_tenants(org_domain=domain)
        ]
        if len(statuses) > 1:
            raise TooManyTenantsForDomain(
                f"domain {domain} maps to {len(statuses)} workspaces — contact support"
            )
        if not statuses:
            return None
        status = statuses[0]
        if status.phase != "Ready":
            raise TenantNotReady(
                f"{domain}'s workspace is {status.phase.lower()} — try again shortly"
            )
        result = await add_member(self.kube, status.tenant, MemberRequest(email=email))
        return JoinedTenant(
            name=status.tenant,
            workspace_id=result.workspace_id,
            url=status.url or f"https://{status.tenant}.{self.base_domain}",
        )


@dataclass(frozen=True)
class DeployTarget:
    base_domain: str
    bundle_image: str


def slugify_domain(domain: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in domain.lower())
    return "-".join(part for part in cleaned.split("-") if part)


def mint_tenant_name(domain: str) -> str:
    """A stable per-domain tenant slug: the slugified domain plus a hash suffix. Deterministic so a
    concurrent second onboard for a fresh domain computes the same name and the apply is idempotent,
    never a duplicate tenant."""
    suffix = hashlib.sha256(domain.encode()).hexdigest()[:TENANT_NAME_SHA_LEN]
    return f"{slugify_domain(domain)}-{suffix}"


def deploy_request(name: str, owner_email: str, target: DeployTarget) -> DeployRequest:
    """The `DeployRequest` the control plane consumes — the identical contract `ufoctl deploy`
    produces. `sandbox_image` is absent (the hosted tier runs the e2b carrier, which needs no image)
    and `postgres` keeps its default: the platform overrides the tier server-side (the hosted rls
    tier), so the request's value is never read."""
    return DeployRequest(
        tenant=TenantIdentity(
            name=name, host=f"{name}.{target.base_domain}", owner_email=owner_email
        ),
        bundle_image=DeployImage.parse(target.bundle_image),
        config_toml=CONFIG_TOML,
        pack=ASSISTANT_HOSTED_PACK,
    )


@dataclass(frozen=True)
class JoinOrProvision:
    kube: KubeClient
    target: DeployTarget

    @property
    def joins(self) -> TenantJoin:
        return TenantJoin(kube=self.kube, base_domain=self.target.base_domain)

    def workspace_url(self, name: str) -> str:
        """The tenant's public URL surfaced on sign-in — its own subdomain under the deploy base."""
        return f"https://{name}.{self.target.base_domain}"

    async def provision(self, domain: str, owner_email: str) -> str:
        name = mint_tenant_name(domain)
        await self.kube.apply_tenant(deploy_request(name, owner_email, self.target))
        return name

    async def tenant_status(self, name: str) -> DeployStatus:
        obj = await self.kube.get_tenant(name)
        if obj is None:
            return DeployStatus(tenant=name, phase="Pending", message="not yet reconciled")
        return status_from_tenant(obj)
