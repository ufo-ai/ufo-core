"""The generated model-catalog skill reads the same registry the runtime routes on — both-ends for
docs: every registered model appears with its facts, and the skill registers in the index."""

from pathlib import Path

import pytest

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.harness.models.catalog_skill import MODEL_CATALOG_SKILL_NAME, model_catalog_skill
from ufo.harness.models.registry import ModelRegistry, model_registry
from ufo.host.ext.loader import skill_registry


def _registry(tmp_path: Path) -> ModelRegistry:
    return model_registry(
        Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
            blob=BlobConfig(backend="filesystem", root=tmp_path),
        ),
        (),
    )


def test_catalog_skill_lists_every_registered_model_with_its_facts(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    skill = model_catalog_skill(registry)
    assert skill.name == MODEL_CATALOG_SKILL_NAME
    assert skill.parent is None
    for spec in registry.specs.values():
        assert f"`{spec.id}`" in skill.instructions
        assert spec.knowledge_cutoff in skill.instructions
    assert registry.spec("gpt-5.6-terra").api_surface == "responses"
    assert "responses" in skill.instructions
    sonnet = registry.spec("claude-sonnet-4-6").price
    assert (sonnet.input, sonnet.output) == (3_000_000, 15_000_000)
    assert "$3.00 / $15.00" in skill.instructions


def test_catalog_skill_registers_in_the_loadable_index(tmp_path: Path) -> None:
    registry = skill_registry((), (model_catalog_skill(_registry(tmp_path)),))
    assert MODEL_CATALOG_SKILL_NAME in dict(registry.index())
    assert MODEL_CATALOG_SKILL_NAME not in (registry.bundled_names or ())
    assert MODEL_CATALOG_SKILL_NAME not in {skill.name for skill in registry.bundled_skills()}
    assert "sandbox" in {skill.name for skill in registry.bundled_skills()}


def test_skill_registry_rejects_a_colliding_generated_skill(tmp_path: Path) -> None:
    skill = model_catalog_skill(_registry(tmp_path))
    with pytest.raises(ValueError, match="already registered"):
        skill_registry((), (skill, skill))
