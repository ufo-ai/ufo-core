"""The web surface's audience authority: the owner-maintained member list deciding which agents a
member reaches in the portal (#645 — the surface is the audience authority; there is no
surface-independent member↔agent ACL).

Grants live in the extension's own store, one row per `(agent, email)` — the email is the web
surface's identity axis, the claim its bearer proves. The actions here are admin-only and bind to
the `member` object they act on; the agent they grant is the one the call names through the
cross-agent gate, or the executing agent when it names none, so a grant is made in that agent's own
conversation or from the main agent by naming it — which a workspace admin can always do, because
an admin reaches every agent. Everyone else reaches the agents whose `visibility` is `workspace` —
main is born one, so the portal answers a member the way the CLI and an unbound Slack install
already do — plus private agents granted to their email, owned by them, or holding their
member-private extension conversations."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.sdk.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.objects import CONVERSATION_KIND, MEMBER_KIND
from ufo.sdk.seats import SeatEntry, Seats
from ufo.sdk.surfaces import AgentSummary, SurfaceContext, record_transcript_access
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)

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
    agent), and the agents their web audience holds — every workspace-visible agent, an explicit
    grant, or a row they own, since the member who created an agent must be able to open it; a
    member-private extension conversation grants only its agent chat. `member_agents` is that
    audience before the admin role widens direct access, so passive cross-agent listings never use
    administration as discovery."""

    admin: bool
    agents: tuple[AgentSummary, ...]
    member_agents: tuple[AgentSummary, ...]
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
    member = next(
        (entry for entry in snapshot.members if entry.email.strip().lower() == lowered), None
    )
    agents = await surface.list_agents()
    if member is None or not member.seated:
        return WebAudience(admin=False, agents=(), member_agents=(), conversation_agents=())
    granted = await _granted_agent_ids(extension.store, lowered)
    extension_agents = await surface.member_extension_agent_ids(member.id)
    member_id = member.id
    member_agents = tuple(
        a
        for a in agents
        if a.visibility == "workspace"
        or a.id in granted
        or (member_id is not None and a.owner_member_id == member_id)
    )
    conversation_agents = tuple(
        agent for agent in agents if agent.id in extension_agents and agent.id not in granted
    )
    return WebAudience(
        admin=member.admin,
        agents=agents if member.admin else member_agents,
        member_agents=member_agents,
        conversation_agents=() if member.admin else conversation_agents,
    )


class WebAccessInput(BaseModel):
    """Empty: the member is the action's target on the `member` kind, and the agent is the wire's
    `agent` or, unnamed, the turn's own."""

    model_config = ConfigDict(extra="forbid")


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


def _target_agent(ctx: ToolContext) -> tuple[UUID, str]:
    """The agent a web grant binds to and how the reply names it: the resolved agent target when
    the call named one, else the executing agent."""
    if ctx.target is None:
        raise RuntimeError("a web access action dispatched without its target")
    if ctx.target.agent is None:
        return ctx.turn.agent_id, "this agent"
    return ctx.target.agent.id, ctx.target.agent.name


async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry:
    if ctx.speaker_member_id is None:
        return _refusal("Only a speaking member can change web access.")
    if not await ctx.speaker_is_admin():
        return _refusal(
            "Ask a workspace admin — only they can change who reaches this agent on the web."
        )
    if ctx.target is None or ctx.target.name is None:
        raise RuntimeError("a web access action dispatched without its member target")
    member_id = UUID(ctx.target.name)
    async with extension.transaction() as connection:
        snapshot = await Seats(extension.store.workspace_id).snapshot(connection)
    member = next((entry for entry in snapshot.members if entry.id == member_id), None)
    if member is None:
        return _refusal(f"No workspace member has the id {member_id} — invite them first.")
    return member


async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("grant_web_access dispatched without its ExtensionContext")
    gated = await _gate(ctx, ctx.ext)
    if isinstance(gated, ToolResult):
        return gated
    agent_id, agent_label = _target_agent(ctx)
    if ctx.target is not None and ctx.target.agent is None and await ctx.agent_is_main():
        return ToolResult(
            content=(
                TextContent(text="The main agent already answers every member in the portal."),
            )
        )
    email = gated.email.strip().lower()
    await ctx.ext.store.put(_grant_key(agent_id, email), {"granted_by": str(ctx.speaker_member_id)})
    return ToolResult(
        content=(TextContent(text=f"{email} can now reach {agent_label} in the web portal."),)
    )


async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("revoke_web_access dispatched without its ExtensionContext")
    gated = await _gate(ctx, ctx.ext)
    if isinstance(gated, ToolResult):
        return gated
    agent_id, agent_label = _target_agent(ctx)
    email = gated.email.strip().lower()
    await ctx.ext.store.delete(_grant_key(agent_id, email))
    if ctx.target is not None and ctx.target.agent is None and await ctx.agent_is_main():
        return ToolResult(
            content=(
                TextContent(
                    text=f"The main agent answers every member — {email} still reaches it in the "
                    "portal."
                ),
            )
        )
    return ToolResult(
        content=(TextContent(text=f"{email} no longer reaches {agent_label} in the web portal."),)
    )


class PrivateTranscriptInput(BaseModel):
    """Empty: the conversation is the action's target on the `conversation` kind, and the agent
    is the wire's `agent` or, unnamed, the turn's own — a conversation of any other agent is
    refused by the same wall the portal's routes answer on."""

    model_config = ConfigDict(extra="forbid")


async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult:
    """Record the acknowledgement that opens another member's private transcript. This is a
    granting act — the row it writes is what the portal's content gate answers on — so it rides a
    turn like every other grant, and that turn is its audit record. A channel another organization
    sits in never carries it: what the record discloses would land in that channel."""
    if ctx.speaker_member_id is None:
        return _refusal("Only a speaking member can open a private transcript.")
    if not await ctx.speaker_is_admin():
        return _refusal("Ask a workspace admin — only they can read another member's transcript.")
    if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
        return _refusal(
            "A private transcript is opened from an internal conversation, never from a channel "
            "shared with another organization."
        )
    if ctx.target is None or ctx.target.name is None:
        raise RuntimeError("read_private_transcript dispatched without its conversation target")
    agent_id, _label = _target_agent(ctx)
    recorded = await record_transcript_access(
        ctx.turn.workspace_id,
        UUID(ctx.target.name),
        agent_id,
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
            "Give this workspace member access to an agent in the web portal — workspace admins "
            "only. The agent is the one named, or the current one when none is. The "
            "main agent needs no grant: it answers every member."
        ),
        input_model=WebAccessInput,
        handler=_grant,
        side_effecting=True,
        bound=ObjectBinding(kind=MEMBER_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Grant web access"),
    ),
    ToolDef(
        name="revoke_web_access",
        description=(
            "Remove this workspace member's access to an agent in the web portal — workspace "
            "admins only. The agent is the one named, or the current one when none is. Admins "
            "keep reaching every agent, and the main agent answers every member regardless of "
            "grants."
        ),
        input_model=WebAccessInput,
        handler=_revoke,
        side_effecting=True,
        bound=ObjectBinding(kind=MEMBER_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Revoke web access"),
    ),
    ToolDef(
        name="read_private_transcript",
        description=(
            "Acknowledge that this private conversation of another member may hold private "
            "information and open it for reading in the web portal — workspace admins only. "
            "Records who read it, whose it was, and when. The transcript itself is read in the "
            "portal, not here."
        ),
        input_model=PrivateTranscriptInput,
        handler=_read_private_transcript,
        side_effecting=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(
            label="Open transcript",
            confirm="This records that you opened another member's private conversation.",
        ),
    ),
)
