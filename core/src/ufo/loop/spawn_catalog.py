"""The spawn-catalog skill, assembled per turn from the live subagent registry and the workspace's
agent rows — so the targets a spawn can name, and the payload each takes, cannot drift from what
`spawn` actually dispatches against. Profiles are deploy-fixed; agents are workspace state, which
is why the catalog is built per turn rather than at boot. It carries its own index entry, so an
agent reaching for how to delegate loads it by name and has the targets before its first spawn."""

from collections.abc import Mapping
from uuid import UUID

import sqlalchemy as sa

from ufo.contracts import TaskInput
from ufo.db import workspace_tx
from ufo.ext.manifest import SubagentProfile
from ufo.loop.subagents import SubagentRegistry
from ufo.schema import tables
from ufo.seats import member_is_admin
from ufo.skills.runtime import RuntimeSkill
from ufo.workspace import ws_current

SPAWN_CATALOG_SKILL_NAME = "spawn-catalog"
SPAWN_CATALOG_DESCRIPTION = (
    "The spawn targets this turn can dispatch — subagent profiles and workspace agents — and the "
    "payload each one takes, generated from the live registry and the workspace's agents. Load it "
    "to pick a target and build its payload."
)


def _profile_payload(profile: SubagentProfile) -> str:
    fields = profile.input_model.model_fields
    if not fields:
        return "(no fields)"
    return ", ".join(
        f"`{name}`" if field.is_required() else f"`{name}` (optional)"
        for name, field in sorted(fields.items())
    )


def _schema_payload(schema: Mapping[str, object] | None) -> str:
    if schema is None:
        return ", ".join(f"`{name}`" for name in sorted(TaskInput.model_fields))
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return "(no fields)"
    required = schema.get("required")
    names = frozenset(required) if isinstance(required, list) else frozenset()
    return ", ".join(
        f"`{name}`" if name in names else f"`{name}` (optional)" for name in sorted(properties)
    )


async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill:
    """One `RuntimeSkill` listing every profile and every spawnable workspace agent with the
    payload keys each requires, built beside the dispatch so the catalog and the spawn read the
    same records. Agents are the set the spawn gate admits: the turn's member's own rows, and for
    a workspace admin every row — an ownerless row (main, provisioned) is the admins'. An agent
    whose name a profile shadows is listed under its qualified form, which is the only form a
    spawn of it accepts."""
    profile_names = frozenset(profile.name for profile in registry.profiles)
    async with workspace_tx() as connection:
        admin = member_id is not None and await member_is_admin(
            connection, ws_current().workspace_id, member_id
        )
        agents = (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.input_schema)
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    *(() if admin else (tables.agent.c.owner_member_id == member_id,)),
                )
                .order_by(tables.agent.c.name)
            )
        ).all()
    rows = "\n".join(
        (
            *(
                f"| `{profile.name}` | profile | {_profile_payload(profile)} |"
                for profile in sorted(registry.profiles, key=lambda profile: profile.name)
            ),
            *(
                f"| `{'agent:' if agent.name in profile_names else ''}{agent.name}` | agent "
                f"| {_schema_payload(agent.input_schema)} |"
                for agent in agents
            ),
        )
    )
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
