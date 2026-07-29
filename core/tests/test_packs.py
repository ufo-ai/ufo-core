"""The packs seam at the loader: a named pack narrows the deploy to a coherent extension bundle.

Packs are discovered through `ufo.pack` entry points exactly as extensions are through
`ufo.extension`; activating one by name makes exactly its bundled extensions' manifests active
plus a manifest of the pack's own pack-level skills and onboarding. The assistant pack is the
flagship — memory with its index and embed backends, the browser tools, connectors, and Exa come up
together. The fail-loud cases ride along: an unknown pack name, a pack naming an uninstalled
extension, and a pack whose name collides with a bundled extension each raise (the uninstalled and
collision cases stub discovery — the real dependency — to drive the real narrowing)."""

import pytest
import ufo_pack_assistant as assistant
import ufo_pack_assistant_billing as assistant_billing
import ufo_pack_assistant_eval as assistant_eval
import ufo_pack_assistant_hosted as assistant_hosted
import ufo_pack_chief_of_staff as chief_of_staff
import ufo_pack_dsqa_eval as dsqa_eval
import ufo_pack_gdpval_eval as gdpval
import ufo_pack_yc.manifest as yc

import ufo.ext.loader as loader
from ufo.ext.loader import discovered_packs, load_manifests, skill_registry
from ufo.ext.manifest import Pack
from ufo.skills.runtime import CORE_SKILL_REGISTRY, parse_skill

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


def test_assistant_billing_pack_is_the_local_bundle_plus_metronome() -> None:
    """The opt-in local billing bundle: everything the assistant pack has, plus the one extension
    that owns the billing chain — so `manage_billing` and the activation job exist on a laptop
    without dragging in the managed backends `assistant_hosted` needs."""
    packs = discovered_packs()
    assert assistant_billing.NAME in packs
    assert packs[assistant_billing.NAME].extensions == assistant_billing.EXTENSIONS
    assert set(assistant.EXTENSIONS) < set(assistant_billing.EXTENSIONS)
    assert set(assistant_billing.EXTENSIONS) - set(assistant.EXTENSIONS) == {"metronome"}
    assert "metronome" not in assistant.EXTENSIONS


def test_activating_the_assistant_billing_pack_brings_up_the_billing_surface() -> None:
    """Naming it makes the billing tool, job, and prompt section active — the proof that a local
    deploy can service the `Set up billing` choice hosted onboarding offers the owner."""
    manifests = load_manifests(assistant_billing.NAME)
    assert [m.name for m in manifests] == [*assistant_billing.EXTENSIONS, assistant_billing.NAME]
    metronome = next(m for m in manifests if m.name == "metronome")
    assert "manage_billing" in {tool.name for tool in metronome.tools}
    assert "billing_activation" in {job.name for job in metronome.jobs}
    assert "billing" in {section.name for section in metronome.prompt_sections}


def test_assistant_eval_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert assistant_eval.NAME in packs
    assert packs[assistant_eval.NAME].extensions == assistant_eval.EXTENSIONS


def test_activating_the_assistant_eval_pack_swaps_real_brokers_for_the_environment() -> None:
    """The eval variant is the assistant bundle with the deterministic environment added and the
    real broker extensions removed — in an eval deploy they are unkeyed decoys the agent burns
    turns failing against instead of finding the environment."""
    names = [manifest.name for manifest in load_manifests(assistant_eval.NAME)]
    assert names == [*assistant_eval.EXTENSIONS, assistant_eval.NAME]
    assert "eval_env" in names
    assert "connectors" in names
    assert not assistant_eval.REAL_BROKERS & set(names)


def test_assistant_hosted_pack_is_discovered_with_its_bundle_and_skills() -> None:
    packs = discovered_packs()
    assert assistant_hosted.NAME in packs
    assert packs[assistant_hosted.NAME].extensions == assistant_hosted.EXTENSIONS
    assert {skill.path.name for skill in packs[assistant_hosted.NAME].skills} == set(
        assistant_hosted.SKILL_NAMES
    )


def test_yc_pack_is_discovered_with_its_bundle_and_skills() -> None:
    packs = discovered_packs()
    assert packs[yc.NAME].extensions == yc.EXTENSIONS
    assert {skill.path.name for skill in packs[yc.NAME].skills} == set(yc.SKILL_NAMES)


def test_activating_the_assistant_hosted_pack_makes_exactly_its_bundle_active() -> None:
    """The hosted variant narrows to its managed-infra bundle (Turbopuffer, Slack, Redis, E2B on top
    of the assistant capabilities) in declared order, followed by the pack's own manifest carrying
    its onboarding-help corpus."""
    manifests = load_manifests(assistant_hosted.NAME)
    assert [manifest.name for manifest in manifests] == [
        *assistant_hosted.EXTENSIONS,
        assistant_hosted.NAME,
    ]
    own = manifests[-1]
    parsed = {parse_skill(spec.path).name for spec in own.skills}
    assert parsed == set(assistant_hosted.SKILL_NAMES)


def test_the_hosted_onboarding_corpus_ships_its_reference_files() -> None:
    """The corpus routes to one reference file per question, so a bundle that shipped only SKILL.md
    would answer every onboarding question from a table of contents pointing at nothing."""
    packs = discovered_packs()
    corpus = next(
        skill
        for skill in packs[assistant_hosted.NAME].skills
        if skill.path.name == "customer-onboarding-help"
    )
    references = {path.name for path in (corpus.path / "references").iterdir()}
    assert references == {
        "billing-and-seats.md",
        "capabilities.md",
        "getting-started.md",
        "internal-only.md",
        "not-yet.md",
        "slack-install.md",
        "troubleshooting.md",
    }


def test_chief_of_staff_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert chief_of_staff.NAME in packs
    assert packs[chief_of_staff.NAME].extensions == chief_of_staff.EXTENSIONS


def test_activating_the_chief_of_staff_pack_makes_exactly_its_bundle_active() -> None:
    """The chief-of-staff pack narrows to its feed-and-review bundle (brokered connectors plus
    sources, memory, the Slack front door, scheduling, todos, workspace skills, self-improvement)
    in declared order, followed by the pack's own manifest carrying its four workflow skills."""
    manifests = load_manifests(chief_of_staff.NAME)
    assert [manifest.name for manifest in manifests] == [
        *chief_of_staff.EXTENSIONS,
        chief_of_staff.NAME,
    ]
    own = manifests[-1]
    parsed = {parse_skill(spec.path).name for spec in own.skills}
    assert parsed == set(chief_of_staff.SKILL_NAMES)


def test_activating_the_yc_pack_makes_exactly_its_bundle_active() -> None:
    names = [manifest.name for manifest in load_manifests(yc.NAME)]
    assert names == [*yc.EXTENSIONS, yc.NAME]


@pytest.mark.parametrize(
    ("name", "extensions"),
    (
        (dsqa_eval.CORE_NAME, dsqa_eval.CORE_EXTENSIONS),
        (dsqa_eval.SEARCH_NAME, dsqa_eval.SEARCH_EXTENSIONS),
        (dsqa_eval.BROWSER_NAME, dsqa_eval.BROWSER_EXTENSIONS),
    ),
)
def test_dsqa_eval_packs_enforce_the_capability_tiers(
    name: str, extensions: tuple[str, ...]
) -> None:
    assert discovered_packs()[name].extensions == extensions
    assert [manifest.name for manifest in load_manifests(name)] == [*extensions, name]


@pytest.mark.parametrize(
    ("name", "extensions"),
    (
        (gdpval.CORE_NAME, gdpval.BASE_EXTENSIONS),
        (gdpval.DOCUMENTS_NAME, (*gdpval.BASE_EXTENSIONS, *gdpval.DOCUMENT_EXTENSIONS)),
        (gdpval.RESEARCH_NAME, (*gdpval.BASE_EXTENSIONS, *gdpval.RESEARCH_EXTENSIONS)),
        (
            gdpval.FULL_NAME,
            (*gdpval.BASE_EXTENSIONS, *gdpval.DOCUMENT_EXTENSIONS, *gdpval.RESEARCH_EXTENSIONS),
        ),
    ),
)
def test_gdpval_treatment_pack_is_discovered(name: str, extensions: tuple[str, ...]) -> None:
    assert discovered_packs()[name].extensions == extensions
    assert [manifest.name for manifest in load_manifests(name)] == [*extensions, name]


def test_no_pack_selected_leaves_the_unnarrowed_extension_set() -> None:
    active = {manifest.name for manifest in load_manifests()}
    assert set(assistant.EXTENSIONS) <= active
    assert assistant.NAME not in active


def test_every_shipped_skill_description_stays_a_routing_trigger() -> None:
    """A description rides in the skill index of every turn, so its length is a standing tax on
    every workspace the pack serves — CLAUDE.md caps it at 50 words. Enforced here rather than
    reviewed, because the cap is exactly the kind a growing description passes unnoticed."""
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
