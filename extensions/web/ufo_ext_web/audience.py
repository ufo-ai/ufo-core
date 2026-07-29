"""The web surface's audience authority: the owner-maintained member list deciding which agents a
member reaches in the portal (#645 — the surface is the audience authority; there is no
surface-independent member↔agent ACL).

Grants live in the extension's own store, one row per `(agent, email)` — the email is the web
surface's identity axis, the claim its bearer proves. `grant_web_access`/`revoke_web_access` are
the chat verbs: admin-only, applying to the executing agent, so granting access to an agent
happens in that agent's own conversation — which a workspace admin can always open, because an
admin reaches every agent. Everyone else reaches exactly the agents granted to their email."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import AgentSummary, SurfaceContext
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

EXTENSION_WEB = "web"
AUDIENCE_PREFIX = "audience/"


def web_extension() -> ExtensionContext:
    """The extension's own scoped handle, built where a surface handler needs the audience store —
    the same bridge the memory explorer uses: a surface receives a `SurfaceContext`, and the
    extension's own rows are reached only through its scoped store and transaction."""
    return ExtensionContext(
        store=ScopedStore(extension=EXTENSION_WEB),
        credentials=CredentialAccess(declared=frozenset()),
    )


def _grant_key(agent_id: UUID, email: str) -> str:
    return f"{AUDIENCE_PREFIX}{agent_id}/{email.strip().lower()}"


async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]:
    """Every web-audience grant, agent → sorted granted emails — the administration view's read
    over the same rows the grant/revoke verbs write."""
    grants: dict[UUID, list[str]] = {}
    for key, _ in await store.list(AUDIENCE_PREFIX):
        agent_str, _, granted_email = key.removeprefix(AUDIENCE_PREFIX).partition("/")
        grants.setdefault(UUID(agent_str), []).append(granted_email)
    return {agent_id: tuple(sorted(emails)) for agent_id, emails in grants.items()}


async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]:
    lowered = email.strip().lower()
    granted: set[UUID] = set()
    for key, _ in await store.list(AUDIENCE_PREFIX):
        agent_str, _, granted_email = key.removeprefix(AUDIENCE_PREFIX).partition("/")
        if granted_email == lowered:
            granted.add(UUID(agent_str))
    return frozenset(granted)


@dataclass(frozen=True)
class WebAudience:
    """One member's view of the portal: whether they administer the workspace (and so see every
    agent) and the agents their web audience holds."""

    admin: bool
    agents: tuple[AgentSummary, ...]

    def allows(self, agent_id: UUID) -> bool:
        return any(agent.id == agent_id for agent in self.agents)


async def web_audience(
    surface: SurfaceContext, extension: ExtensionContext, email: str
) -> WebAudience:
    lowered = email.strip().lower()
    async with extension.transaction() as connection:
        snapshot = await Seats(surface.workspace_id).snapshot(connection)
    admin = any(
        entry.admin and entry.email.strip().lower() == lowered for entry in snapshot.members
    )
    agents = await surface.list_agents()
    if admin:
        return WebAudience(admin=True, agents=agents)
    granted = await _granted_agent_ids(extension.store, lowered)
    return WebAudience(admin=False, agents=tuple(a for a in agents if a.id in granted))


class WebAccessInput(BaseModel):
    email: str = Field(description="The workspace member's email address.")
    user_description: str = Field(
        description="Brief plain-language description for non-technical users, shown in the "
        "activity timeline."
    )


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


async def _gate(
    ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput
) -> ToolResult | None:
    if ctx.speaker_member_id is None:
        return _refusal("Only a speaking member can change web access.")
    if not await ctx.speaker_is_admin():
        return _refusal(
            "Ask a workspace admin — only they can change who reaches this agent on the web."
        )
    if not args.email.strip():
        return _refusal("An email address is required.")
    lowered = args.email.strip().lower()
    async with extension.transaction() as connection:
        snapshot = await Seats(extension.store.workspace_id).snapshot(connection)
    if not any(entry.email.strip().lower() == lowered for entry in snapshot.members):
        return _refusal(
            f"No workspace member has the email {args.email.strip()!r} — invite them first."
        )
    return None


async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("grant_web_access dispatched without its ExtensionContext")
    refused = await _gate(ctx, ctx.ext, args)
    if refused is not None:
        return refused
    await ctx.ext.store.put(
        _grant_key(ctx.turn.agent_id, args.email),
        {"granted_by": str(ctx.speaker_member_id)},
    )
    return ToolResult(
        content=(
            TextContent(
                text=f"{args.email.strip().lower()} can now reach this agent in the web portal."
            ),
        )
    )


async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("revoke_web_access dispatched without its ExtensionContext")
    refused = await _gate(ctx, ctx.ext, args)
    if refused is not None:
        return refused
    await ctx.ext.store.delete(_grant_key(ctx.turn.agent_id, args.email))
    return ToolResult(
        content=(
            TextContent(
                text=f"{args.email.strip().lower()} no longer reaches this agent in the web portal."
            ),
        )
    )


WEB_ACCESS_TOOLS = (
    ToolDef(
        name="grant_web_access",
        description=(
            "Give a workspace member access to this agent in the web portal — workspace admins "
            "only. The member is named by email and must already exist in the workspace."
        ),
        input_model=WebAccessInput,
        handler=_grant,
        side_effecting=True,
    ),
    ToolDef(
        name="revoke_web_access",
        description=(
            "Remove a workspace member's access to this agent in the web portal — workspace "
            "admins only. Admins keep reaching every agent regardless of grants."
        ),
        input_model=WebAccessInput,
        handler=_revoke,
        side_effecting=True,
    ),
)
