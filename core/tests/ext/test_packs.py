"""The packs seam at the loader: a named pack narrows the deploy to a coherent extension bundle.

Packs are discovered through `ufo.pack` entry points exactly as extensions are through
`ufo.extension`; activating one by name makes exactly its bundled extensions' manifests active
plus a manifest of the pack's own pack-level skills and onboarding. The assistant pack is the
flagship — memory with its index and embed backends, connectors, and Perplexity come up
together. The fail-loud cases ride along: an unknown pack name, a pack naming an uninstalled
extension, and a pack whose name collides with a bundled extension each raise (the uninstalled and
collision cases stub discovery — the real dependency — to drive the real narrowing)."""

import pytest
import ufo_ext_coding.manifest as coding
import ufo_pack_assistant as assistant

import ufo.host.ext.loader as loader
from ufo.harness.models.catalog import CORE_MODEL_SPECS
from ufo.host.ext.loader import (
    discovered_packs,
    load_manifests,
    skill_registry,
)
from ufo.runtime.ext.manifest import Pack
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY

SKILL_DESCRIPTION_MAX_WORDS = 50


def test_assistant_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert assistant.NAME in packs
    assert packs[assistant.NAME].extensions == assistant.EXTENSIONS


def test_activating_the_assistant_pack_makes_exactly_its_bundle_active() -> None:
    """Naming the assistant pack narrows the active set to its bundled extensions in declared order,
    followed by the pack's own (here empty) manifest — the coherent config a serve brings up."""
    names = [manifest.name for manifest in load_manifests(assistant.NAME)]
    assert names == [*assistant.EXTENSIONS, assistant.NAME]


def test_a_pack_that_ships_coding_registers_its_profile_models() -> None:
    """`coding` names model ids that other extensions register, so a pack that brings up `coding`
    without either registrar has a child that cannot resolve its model and never runs."""
    for pack in (assistant,):
        manifests = load_manifests(pack.NAME)
        if not any(manifest.name == "coding" for manifest in manifests):
            continue
        profiles = {
            profile.name: profile for manifest in manifests for profile in manifest.subagents
        }
        named = {
            model
            for name in (coding.CODING_PROFILE_NAME, coding.FABLE_ESCALATION_PROFILE_NAME)
            for model in profiles[name].models
        }
        served = {spec.id for spec in CORE_MODEL_SPECS}
        served |= {spec.id for manifest in manifests for spec in manifest.models}
        assert named <= served, (
            f"{pack.NAME} activates coding but does not register {named - served}"
        )


def test_assistant_packs_activate_nested_todos() -> None:
    for pack in (assistant,):
        manifests = load_manifests(pack.NAME)
        todos = next(manifest for manifest in manifests if manifest.name == "todos")
        assert {tool.name for tool in todos.tools} == {
            "update_todo_list",
            "update_todo_status",
            "delegate_todos",
        }
        assert {tool.name for tool in todos.tools if tool.subagent_default} == {
            "update_todo_list",
            "update_todo_status",
        }, "a subagent nests its own list; starting a turn stays with the parent"
        assert [section.name for section in todos.prompt_sections] == ["todo_list"]
        assert [hook.event for hook in todos.hooks] == ["user_prompt_submit"]


def test_the_assistant_pack_ships_no_skill_of_its_own() -> None:
    """The assistant bundle is extensions alone."""
    assert discovered_packs()[assistant.NAME].skills == ()


def test_no_pack_selected_leaves_the_unnarrowed_extension_set() -> None:
    active = {manifest.name for manifest in load_manifests()}
    assert set(assistant.EXTENSIONS) <= active
    assert assistant.NAME not in active


def test_every_shipped_skill_description_stays_a_routing_trigger() -> None:
    """A description rides in the skill index of every turn, so its length is a standing tax on
    every workspace the pack serves — AGENTS.md caps it at 50 words."""
    described = {
        skill.name: len(skill.description.split())
        for pack in discovered_packs()
        for skill in skill_registry(load_manifests(pack)).by_name.values()
    } | {
        skill.name: len(skill.description.split()) for skill in CORE_SKILL_REGISTRY.by_name.values()
    }
    over = {name: words for name, words in described.items() if words > SKILL_DESCRIPTION_MAX_WORDS}

    assert described, "no skills discovered — the cap would be vacuous"
    assert not over, f"skill descriptions over {SKILL_DESCRIPTION_MAX_WORDS} words: {over}"


def test_unknown_pack_name_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="no pack registers it"):
        load_manifests("nonesuch_pack")


def test_pack_naming_an_uninstalled_extension_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = Pack(name="badpack", version="0", extensions=("does-not-exist",))
    monkeypatch.setattr(loader, "discovered_packs", lambda: {bad.name: bad})
    with pytest.raises(RuntimeError, match="not installed and active"):
        load_manifests("badpack")


def test_pack_name_colliding_with_a_bundled_extension_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collide = Pack(name="memory", version="0", extensions=("memory",))
    monkeypatch.setattr(loader, "discovered_packs", lambda: {collide.name: collide})
    with pytest.raises(RuntimeError, match="collides with a bundled extension"):
        load_manifests("memory")
