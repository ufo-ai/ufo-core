"""The model-catalog skill, generated at boot from the live model registry — so the models a member
can pin, and their facts, cannot drift from what the runtime actually routes, prices, and prompts
against (both-ends applied to documentation). Rendered from the same `ModelSpec`s every seam reads,
never hand-written. See RFC 0018."""

from ufo.billing.accounting import MICRO_USD_PER_USD
from ufo.models.registry import ModelRegistry
from ufo.skills.runtime import RuntimeSkill

MODEL_CATALOG_SKILL_NAME = "model-catalog"
MODEL_CATALOG_DESCRIPTION = (
    "The models this deploy can run and their facts — price, knowledge cutoff, context window, "
    "reasoning, api surface — generated from the live model registry. Load it to choose or compare "
    "models."
)


def _per_mtok(micro_usd_per_mtok: int) -> str:
    return f"${micro_usd_per_mtok / MICRO_USD_PER_USD:.2f}"


def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill:
    """One `RuntimeSkill` whose body is a table of every registered model, rendered from the
    registry's specs. Built once at boot beside the model registry, so the catalog and the runtime
    read the same records."""
    header = (
        "| id | provider | knowledge cutoff | context window | price in/out ($/Mtok) "
        "| reasoning | api surface |\n|---|---|---|---|---|---|---|"
    )
    rows = "\n".join(
        f"| `{spec.id}` | {spec.provider} | {spec.knowledge_cutoff} | {spec.context_window:,} "
        f"| {_per_mtok(spec.price.input)} / {_per_mtok(spec.price.output)} "
        f"| {'yes' if spec.reasoning.supported else 'no'} | {spec.api_surface} |"
        for spec in sorted(registry.specs.values(), key=lambda spec: spec.id)
    )
    body = (
        "The models this deploy can run, generated from the live registry — the same `ModelSpec` "
        "records the runtime routes, prices, and prompts against, so this table cannot drift from "
        f"behaviour.\n\n{header}\n{rows}\n"
    )
    raw = (
        f"---\nname: {MODEL_CATALOG_SKILL_NAME}\n"
        f"description: {MODEL_CATALOG_DESCRIPTION}\n---\n\n{body}"
    )
    return RuntimeSkill(
        name=MODEL_CATALOG_SKILL_NAME,
        description=MODEL_CATALOG_DESCRIPTION,
        instructions=body,
        raw_skill_md=raw,
    )
