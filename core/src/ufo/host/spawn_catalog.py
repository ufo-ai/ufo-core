"""What `spawn` can dispatch this turn, read once from the live subagent registry and the
workspace's agent rows — so the targets a spawn can name, and the payload each takes, cannot drift
from what `spawn` actually dispatches against. Profiles are deploy-fixed; agents are workspace
state, which is why this is read per turn rather than at boot.

The same read serves two surfaces, because a caller that has to load a document before it can name
a required argument does not always load it: `spawn`'s own `payload` description carries the keys,
where no instruction can forbid reading them, and the catalog skill carries the table for a caller
picking a target it has not used before."""

from dataclasses import dataclass

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.skills.runtime import RuntimeSkill
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.turns.audience import Audience, audience_member
from ufo.runtime.turns.contracts import input_contract, payload_keys
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

SPAWN_CATALOG_SKILL_NAME = "spawn-catalog"
SPAWN_CATALOG_DESCRIPTION = (
    "The spawn targets this turn can dispatch — subagent profiles and workspace agents — and the "
    "payload each one takes, generated from the live registry and the workspace's agents. Load it "
    "to pick a target and build its payload."
)


@dataclass(frozen=True)
class SpawnTarget:
    """One dispatchable target: the name a spawn takes, which namespace it came from, and the
    payload keys its contract requires."""

    name: str
    kind: str
    keys: str


async def spawn_targets(registry: SubagentRegistry, audience: Audience) -> tuple[SpawnTarget, ...]:
    """Every target this conversation may discover, with the payload keys each takes. A member
    audience sees that member's private agents and workspace agents; every shared or room audience
    sees workspace agents only. An agent whose name a profile shadows is listed under its qualified
    form, which is the only form a spawn of it accepts.

    A profile that runs on the member's own provider account is listed whether or not they
    connected one. Withholding it hides the capability from the member who has not met it yet: the
    model would never name coding, so nothing would ever tell them it exists or why it is off. The
    spawn refusal is what carries that — it names the account the profile needs and the screen that
    connects one — so the member meets the requirement by reaching for the thing and being told,
    rather than by never being offered it."""
    profiles = registry.profiles
    profile_names = frozenset(profile.name for profile in profiles)
    member_id = audience_member(audience)
    agent_scope = (
        tables.agent.c.visibility == "workspace"
        if member_id is None
        else sa.or_(
            tables.agent.c.visibility == "workspace",
            tables.agent.c.owner_member_id == member_id,
        )
    )
    async with workspace_tx() as connection:
        agents = (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.input_schema)
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.archived_at.is_(None),
                    agent_scope,
                )
                .order_by(tables.agent.c.name)
            )
        ).all()
    return (
        *(
            SpawnTarget(profile.name, "profile", payload_keys(profile.input_model))
            for profile in sorted(profiles, key=lambda profile: profile.name)
        ),
        *(
            SpawnTarget(
                f"{'agent:' if agent.name in profile_names else ''}{agent.name}",
                "agent",
                payload_keys(input_contract(agent.input_schema)),
            )
            for agent in agents
        ),
    )


def spawn_payload_description(targets: tuple[SpawnTarget, ...]) -> str:
    """The `payload` field's description for this turn: the keys each target takes, at the one
    place every spawn is written. The generic sentence it replaces named the contract without
    showing it, so a caller learned the keys only from a refusal — measured at nine wrong-payload
    calls in eighteen failures before the catalog skill existed, and unchanged by it for a caller
    whose own instructions tell it not to load a skill."""
    if not targets:
        return "Arguments matching the target's input schema."
    shapes = "; ".join(f"{target.name} takes {target.keys}" for target in targets)
    return f"Arguments matching the target's input schema — {shapes}."


def spawn_catalog_skill(targets: tuple[SpawnTarget, ...]) -> RuntimeSkill:
    """One `RuntimeSkill` listing every target with the payload keys it requires, rendered from
    the read the dispatch itself resolves against, so the table cannot drift from behaviour."""
    rows = "\n".join(f"| `{target.name}` | {target.kind} | {target.keys} |" for target in targets)
    body = (
        "The targets `spawn` can dispatch, generated from the live registry and the workspace's "
        "agents — the same records a spawn resolves against, so this table cannot drift from "
        "behaviour. `target` takes one of these names exactly, and `payload` takes that row's "
        "keys.\n\n"
        f"| target | kind | payload |\n|---|---|---|\n{rows}\n"
    )
    raw = (
        f"---\nname: {SPAWN_CATALOG_SKILL_NAME}\n"
        f"description: {SPAWN_CATALOG_DESCRIPTION}\n---\n\n{body}"
    )
    return RuntimeSkill(
        name=SPAWN_CATALOG_SKILL_NAME,
        description=SPAWN_CATALOG_DESCRIPTION,
        instructions=body,
        raw_skill_md=raw,
    )
