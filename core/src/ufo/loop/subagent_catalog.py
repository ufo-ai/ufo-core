"""The subagent-catalog skill, generated at boot from the live subagent registry — so the profiles a
spawn can name, and the payload each takes, cannot drift from what `spawn_subagent` actually
dispatches against. Rendered from the same `SubagentProfile`s the registry resolves, never
hand-written; `delegation` pulls it through `depends`, so an agent reaching for how to delegate
mounts the catalog with it."""

from ufo.ext.manifest import SubagentProfile
from ufo.loop.subagents import SubagentRegistry
from ufo.skills.runtime import RuntimeSkill

SUBAGENT_CATALOG_SKILL_NAME = "subagent-catalog"
SUBAGENT_CATALOG_DESCRIPTION = (
    "The subagent profiles this deploy can spawn and the payload each one takes — generated from "
    "the live subagent registry. Load it to pick a profile and build its payload."
)


def _payload(profile: SubagentProfile) -> str:
    fields = profile.input_model.model_fields
    if not fields:
        return "(no fields)"
    return ", ".join(
        f"`{name}`" if field.is_required() else f"`{name}` (optional)"
        for name, field in sorted(fields.items())
    )


def subagent_catalog_skill(registry: SubagentRegistry) -> RuntimeSkill:
    """One `RuntimeSkill` listing every registered profile and the payload keys it requires, built
    beside the registry at boot so the catalog and the dispatch read the same records."""
    rows = "\n".join(
        f"| `{profile.name}` | {_payload(profile)} |"
        for profile in sorted(registry.profiles, key=lambda profile: profile.name)
    )
    body = (
        "The profiles `spawn_subagent` can dispatch, generated from the live registry — the same "
        "records a spawn resolves against, so this table cannot drift from behaviour. `profile` "
        "takes one of these names exactly, and `payload` takes that row's keys.\n\n"
        f"| profile | payload |\n|---|---|\n{rows}\n"
    )
    raw = (
        f"---\nname: {SUBAGENT_CATALOG_SKILL_NAME}\n"
        f"description: {SUBAGENT_CATALOG_DESCRIPTION}\n---\n\n{body}"
    )
    return RuntimeSkill(
        name=SUBAGENT_CATALOG_SKILL_NAME,
        description=SUBAGENT_CATALOG_DESCRIPTION,
        instructions=body,
        raw_skill_md=raw,
    )
