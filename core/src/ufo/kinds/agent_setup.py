"""What a shipped agent still needs from a member, and the skill that asks for it.

It lives apart from `ufo.kinds.agents` because three subsystems read it — the turn loop, the
portal's agent projection, and the Manifest declaration — and `ufo.kinds.agents` reaches the
extension context, which the portal surface is itself part of.
"""

from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.skills.runtime import RuntimeSkill
from ufo.workspace import ws_current


class AgentSetup(BaseModel):
    """What a shipped agent still needs from a member, and what to do about it. An agent arrives
    with no edges at all: `connectors` names the providers it calls, and `instructions` is what to
    do to obtain them. Stored on the row at creation like the prompt and the model, so the read
    needs no manifest and answers the same after the extension is gone.

    A grant is consent over an account a person owns, so an extension declares this and never
    creates it. A connector names a kind of authority, never an instance: any connection of that
    provider answers it, so which account is the member's choice.

    Source feeds are deliberately absent. A need must be settleable by the agent it is declared
    for, and `object_apply source` writes no grant when the workspace already holds that binding —
    it returns on the existing one — while no verb attaches an existing source to a second agent
    the way `GrantStore.attach` does for a connection. Declaring a source need would state a
    requirement a member cannot always satisfy, so it waits for that primitive."""

    connectors: tuple[str, ...] = ()
    instructions: str = ""


async def pending_setup() -> tuple[tuple[UUID, str, AgentSetup], ...]:
    """Every shipped agent in this workspace a member has not finished wiring, with the grants it
    is still missing. The agent that needs a grant is the one that must ask for it: every grant
    binds to the agent whose conversation it is made in, so this is read to tell that agent what is
    outstanding.

    A connector is met by a grant to any connection of that provider — `connect_account` in the
    agent's own conversation writes one, and `GrantStore.attach` binds a connection that already
    exists, so every declared need has a member-reachable way to settle. An agent no extension
    shipped declares nothing and is never listed."""
    async with workspace_tx() as connection:
        shipped = (
            await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.name, tables.agent.c.setup).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.setup.is_not(None),
                )
            )
        ).all()
        if not shipped:
            return ()
        held = {row.id: set[str]() for row in shipped}
        for granted in await connection.execute(
            sa.select(tables.connector_grant.c.agent_id, tables.connection.c.provider)
            .join(
                tables.connection,
                tables.connector_grant.c.connection_id == tables.connection.c.id,
            )
            .where(tables.connector_grant.c.agent_id.in_(held))
        ):
            held[granted.agent_id].add(granted.provider)
    pending = []
    for row in sorted(shipped, key=lambda row: row.name):
        declared = AgentSetup.model_validate(row.setup)
        providers = held[row.id]
        missing = AgentSetup(
            connectors=tuple(name for name in declared.connectors if name not in providers),
            instructions=declared.instructions,
        )
        if missing.connectors:
            pending.append((row.id, row.name, missing))
    return tuple(pending)


def _wants(missing: AgentSetup) -> str:
    return ", ".join(f"a {provider} account" for provider in missing.connectors)


SETUP_SKILL_NAME = "agent-setup"
SETUP_SKILL_DESCRIPTION = (
    "Finish setting up an agent this workspace installed: grant it the accounts it works from. "
    "Load when a member asks to set one up, or asks why one is not working. Not for creating a "
    "new application of their own."
)
SETUP_HEADER = (
    "You are installed but not set up. Ask the member for what is missing, then call "
    "`connect_account` for each account. A grant made in your own conversation binds to you."
)
ROSTER_HEADER = (
    "These agents are installed and not set up. A member must grant each account, and you are the "
    "only agent that may grant one to another agent: call `connect_account` with `agent` set to "
    "that agent's name, in this conversation, with the member speaking."
)


async def setup_skill(agent_id: UUID, is_main: bool, has_speaker: bool) -> RuntimeSkill | None:
    """The loadable skill telling an agent what it still needs, or None when it needs nothing
    or the turn cannot act on it.

    An agent with grants outstanding is told about itself. The main agent is told the roster,
    because it is the one agent that may grant an account to another (`connect_account` takes an
    `agent`), so a member on a surface bound only to it — Slack, the CLI — finishes the setup by
    asking, without opening the portal.

    It is a skill rather than a prompt section because it is a task, not a capability: the index
    carries one line, and the instructions reach the model only on the turn a member actually asks.

    It reaches only a turn a member is speaking on, because every act it names is speaker-gated:
    `connect_account` refuses without one. A turn nobody speaks on — a spawn, a schedule, a source
    arrival — would be handed instructions it cannot follow and a member it cannot ask, and would
    keep reporting the same grant on every later turn.

    Derived from the grants on every turn, so it erases itself as they land rather than needing a
    flag that a later revoke would leave stale."""
    if not has_speaker:
        return None
    pending = await pending_setup()
    mine = next((entry for entry in pending if entry[0] == agent_id), None)
    if mine is None:
        others = [entry for entry in pending if entry[0] != agent_id]
        if not is_main or not others:
            return None
        lines = [
            f"- {name} — still needs {_wants(missing)}. {missing.instructions}".rstrip()
            for _agent, name, missing in others
        ]
        return RuntimeSkill(
            name=SETUP_SKILL_NAME,
            description=SETUP_SKILL_DESCRIPTION,
            instructions="\n".join((ROSTER_HEADER, "", *lines)),
        )
    _, _, missing = mine
    body = f"{SETUP_HEADER}\n\nYou still need {_wants(missing)}. {missing.instructions}"
    return RuntimeSkill(
        name=SETUP_SKILL_NAME, description=SETUP_SKILL_DESCRIPTION, instructions=body.rstrip()
    )
