"""A `ServingModel` over a test's own model client, carrying the real core spec of the named model
so the provider, reasoning and context window the engine and compaction read are the ones
production reads."""

from ufo.harness.models.catalog import CORE_MODEL_SPECS
from ufo.harness.models.interface import ModelClient
from ufo.harness.models.registry import ServingModel

CORE_SPECS = {spec.id: spec for spec in CORE_MODEL_SPECS}


def serving_model(client: ModelClient, model: str = "claude-opus-4-8") -> ServingModel:
    return ServingModel(model=model, spec=CORE_SPECS[model], client=client)
