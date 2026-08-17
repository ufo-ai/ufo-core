"""The web surface's audience authority: the owner-maintained member list deciding which agents a
member reaches in the portal (#645 — the surface is the audience authority; there is no
surface-independent member↔agent ACL).

Grants live in the extension's own store, one row per `(agent, email)` — the email is the web
surface's identity axis, the claim its bearer proves. The chat verbs here are admin-only and apply
to the executing agent, so granting access to an agent
happens in that agent's own conversation — which a workspace admin can always open, because an
admin reaches every agent. Everyone else reaches the workspace's main agent — the agent every
surface routes an unbound member to, so the portal answers a member the way the CLI and an
unbound Slack install already do — plus non-main agents granted to their email or holding their
member-private extension conversations."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import AgentSummary, SurfaceContext, record_transcript_access
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
    agent), and the agents their web audience holds — the main agent by construction, an explicit
    grant, or a row they own, since the member who created an agent must be able to open it; a
    member-private extension conversation grants only its agent chat."""

    admin: bool
    agents: tuple[AgentSummary, ...]
    conversation_agents: tuple[AgentSummary, ...]

    def allows(self, agent_id: UUID) -> bool:
        return any(agent.id == agent_id for agent in self.agents)

    def allows_chat(self, agent_id: UUID) -> bool:
        return any(agent.id == agent_id for agent in self.chat_agents)

    @property
    def chat_agents(self) -> tuple[AgentSummary, ...]:
        return (*self.agents, *self.conversation_agents)


async def web_audience(
    surface: SurfaceContext, extension: ExtensionContext, email: str
) -> WebAudience:
    lowered = email.strip().lower()
    async with extension.transaction() as connection:
        snapshot = await Seats(surface.workspace_id).snapshot(connection)
    admin = any(
        entry.admin and entry.email.strip().lower() == lowered for entry in snapshot.members
    )
    member = next(
        (entry for entry in snapshot.members if entry.email.strip().lower() == lowered), None
    )
    agents = await surface.list_agents()
    if admin:
        return WebAudience(admin=True, agents=agents, conversation_agents=())
    granted = await _granted_agent_ids(extension.store, lowered)
    extension_agents = (
        frozenset() if member is None else await surface.member_extension_agent_ids(member.id)
    )
    member_id = None if member is None else member.id
    return WebAudience(
        admin=False,
        agents=tuple(
            a
            for a in agents
            if a.main
            or a.id in granted
            or (member_id is not None and a.owner_member_id == member_id)
        ),
        conversation_agents=tuple(
            a for a in agents if a.id in extension_agents and a.id not in granted
        ),
    )


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
    if await ctx.agent_is_main():
        return ToolResult(
            content=(
                TextContent(text="The main agent already answers every member in the portal."),
            )
        )
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
    if await ctx.agent_is_main():
        return ToolResult(
            content=(
                TextContent(
                    text="The main agent answers every member — "
                    f"{args.email.strip().lower()} still reaches it in the portal."
                ),
            )
        )
    return ToolResult(
        content=(
            TextContent(
                text=f"{args.email.strip().lower()} no longer reaches this agent in the web portal."
            ),
        )
    )


class PrivateTranscriptInput(BaseModel):
    """Which conversation an admin is about to read. The agent is the executing one, so a
    conversation of any other agent is refused by the same wall the portal's routes answer on."""

    conversation_id: UUID = Field(description="The conversation whose transcript will be read.")
    user_description: str = Field(
        description="Brief plain-language description for non-technical users, shown in the "
        "activity timeline."
    )


async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult:
    """Record the acknowledgement that opens another member's private transcript. This is a
    granting act — the row it writes is what the portal's content gate answers on — so it rides a
    turn like every other grant, and that turn is its audit record."""
    if ctx.speaker_member_id is None:
        return _refusal("Only a speaking member can open a private transcript.")
    if not await ctx.speaker_is_admin():
        return _refusal("Ask a workspace admin — only they can read another member's transcript.")
    recorded = await record_transcript_access(
        ctx.turn.workspace_id,
        args.conversation_id,
        ctx.turn.agent_id,
        ctx.speaker_member_id,
    )
    if recorded is None:
        return _refusal(
            "Nothing to acknowledge for that id on this agent. An acknowledgement opens another "
            "member's private conversation; your own and a workspace-shared one need none, and a "
            "channel or group DM is readable by nobody here."
        )
    return ToolResult(
        content=(
            TextContent(
                text=f"Recorded: you opened {recorded.subject_email}'s private conversation. "
                "Your email, theirs, and the time are on the record."
            ),
        )
    )


WEB_ACCESS_TOOLS = (
    ToolDef(
        name="grant_web_access",
        description=(
            "Give a workspace member access to this agent in the web portal — workspace admins "
            "only. The member is named by email and must already exist in the workspace. The "
            "main agent needs no grant: it answers every member."
        ),
        input_model=WebAccessInput,
        handler=_grant,
        side_effecting=True,
    ),
    ToolDef(
        name="revoke_web_access",
        description=(
            "Remove a workspace member's access to this agent in the web portal — workspace "
            "admins only. Admins keep reaching every agent, and the main agent answers every "
            "member regardless of grants."
        ),
        input_model=WebAccessInput,
        handler=_revoke,
        side_effecting=True,
    ),
    ToolDef(
        name="read_private_transcript",
        description=(
            "Acknowledge that another member's private conversation may hold private information "
            "and open it for reading in the web portal — workspace admins only. Records who read "
            "it, whose it was, and when. The transcript itself is read in the portal, not here."
        ),
        input_model=PrivateTranscriptInput,
        handler=_read_private_transcript,
        side_effecting=True,
    ),
)
