"""The packs seam at the loader: a named pack narrows the deploy to a coherent extension bundle.

Packs are discovered through `ufo.pack` entry points exactly as extensions are through
`ufo.extension`; activating one by name makes exactly its bundled extensions' manifests active
plus a manifest of the pack's own pack-level skills and onboarding. The assistant pack is the
flagship — memory with its index and embed backends, browser tools, connectors, and Perplexity come
up together. The fail-loud cases ride along: an unknown pack name, a pack naming an uninstalled
extension, and a pack whose name collides with a bundled extension each raise (the uninstalled and
collision cases stub discovery — the real dependency — to drive the real narrowing)."""

import pytest
import ufo_ext_coding.manifest as coding
import ufo_pack_assistant as assistant
import ufo_pack_assistant_billing as assistant_billing
import ufo_pack_assistant_eval as assistant_eval
import ufo_pack_assistant_hosted as assistant_hosted
import ufo_pack_dsqa_eval as dsqa_eval
import ufo_pack_gdpval_eval as gdpval

import ufo.host.ext.loader as loader
from ufo.harness.models.catalog import CORE_MODEL_SPECS
from ufo.host.ext.loader import discovered_packs, load_manifests, skill_registry
from ufo.runtime.ext.manifest import Pack
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, parse_skill

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
    without either registrar has a child that cannot resolve its model and never runs. The names are
    only as good as the set they come up in, which is why this is asserted per activated pack rather
    than against every installed extension.

    Both ways a profile reaches a model count: the escalation rung pins one, and the coding rung
    names one per provider a member can connect. A routed id nothing registers fails in the child's
    own dispatch exactly as a bad pin would, and only for members who connected that provider."""
    for pack in (assistant, assistant_billing, assistant_eval, assistant_hosted):
        manifests = load_manifests(pack.NAME)
        if not any(manifest.name == "coding" for manifest in manifests):
            continue
        profiles = {
            profile.name: profile for manifest in manifests for profile in manifest.subagents
        }
        named = {
            model
            for name in (coding.CODING_PROFILE_NAME, coding.FABLE_ESCALATION_PROFILE_NAME)
            for model in (
                *profiles[name].own_key_models.values(),
                *((profiles[name].model,) if profiles[name].model else ()),
            )
        }
        served = {spec.id for spec in CORE_MODEL_SPECS}
        served |= {spec.id for manifest in manifests for spec in manifest.models}
        assert named <= served, (
            f"{pack.NAME} activates coding but does not register {named - served}"
        )


def test_assistant_packs_mount_the_member_portal() -> None:
    """The gateway posts every signed-in member's bearer to /surface/web, so any pack a deploy
    fronts with that sign-in must mount the web surface — the hosted fleet answering the
    portal with 404 is the outage this pins."""
    for pack in (assistant, assistant_hosted):
        manifests = load_manifests(pack.NAME)
        web = next(manifest for manifest in manifests if manifest.name == "web")
        assert [surface.name for surface in web.surfaces] == ["web"]


def test_assistant_packs_activate_durable_objectives() -> None:
    for pack in (assistant, assistant_hosted):
        manifests = load_manifests(pack.NAME)
        objectives = next(manifest for manifest in manifests if manifest.name == "objectives")
        assert {tool.name for tool in objectives.tools} == {
            "plan_objective",
            "run_independent_steps",
            "read_objective",
            "record_step",
        }
        assert [section.name for section in objectives.prompt_sections] == ["objectives"]
        assert [hook.event for hook in objectives.hooks] == ["user_prompt_submit"]


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
    assert packs[assistant_billing.NAME].skills == ()


def test_activating_the_assistant_billing_pack_brings_up_the_billing_surface() -> None:
    """Naming it makes the billing tool, jobs, and prompt section active — the proof that a local
    deploy can service the `Set up billing` choice hosted onboarding offers the owner."""
    manifests = load_manifests(assistant_billing.NAME)
    assert [m.name for m in manifests] == [*assistant_billing.EXTENSIONS, assistant_billing.NAME]
    metronome = next(m for m in manifests if m.name == "metronome")
    assert "manage_billing" in {tool.name for tool in metronome.tools}
    assert {"usage_shipper", "balance_topup"} <= {job.name for job in metronome.jobs}
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


def test_the_assistant_pack_ships_no_skill_of_its_own() -> None:
    """The assistant bundle is extensions alone. Its skills come from those extensions, so a pack
    composing it carries them by composing the extension tuple — there is no second thing to
    remember to copy across."""
    assert discovered_packs()[assistant.NAME].skills == ()


def test_assistant_hosted_pack_is_discovered_with_its_bundle_and_skills() -> None:
    packs = discovered_packs()
    assert assistant_hosted.NAME in packs
    assert packs[assistant_hosted.NAME].extensions == assistant_hosted.EXTENSIONS
    assert {skill.path.name for skill in packs[assistant_hosted.NAME].skills} == set(
        assistant_hosted.SKILL_NAMES
    )


def test_assistant_hosted_pack_activates_the_imessage_surface() -> None:
    manifests = load_manifests(assistant_hosted.NAME)
    imessage = next(manifest for manifest in manifests if manifest.name == "imessage")
    assert len(imessage.surfaces) == 1
    assert imessage.surfaces[0].listen is not None
    assert imessage.deploy_keys == ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET")


def test_assistant_hosted_pack_activates_the_gbrain_source_backends() -> None:
    """`serve` validates every configured `[[sources]]` block and every `source` row against the
    backend map the activated manifests build, so a hosted deploy carrying a gbrain origin boots
    only while this pack brings the extension up — the backend names and the object kind that keys
    those rows are the contract, not the extension's presence on disk."""
    manifests = load_manifests(assistant_hosted.NAME)
    gbrain = next(manifest for manifest in manifests if manifest.name == "gbrain")
    assert {provider.backend for provider in gbrain.sources} == {"gbrain_git", "gbrain_folder"}
    assert [kind.name for kind in gbrain.objects] == ["gbrain_source"]
    assert [slot.name for slot in gbrain.credentials] == ["github_token"]


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


def test_the_hosted_pack_registers_both_context_strategies() -> None:
    """`enable-context-rollover` selects between the two names in `[context]` per turn, so the pack
    serving the flagged fleet must bring up both strategies. A bundle carrying only compaction fails
    the flagged name at boot the moment somebody turns the flag on for one workspace."""
    manifests = load_manifests(assistant_hosted.NAME)
    strategies = {spec.strategy for manifest in manifests for spec in manifest.context_boundaries}
    assert {"compact", "rollover"} <= strategies
    assert {"context_compact", "context_rollover"} <= set(assistant_hosted.EXTENSIONS)


def test_the_hosted_pack_manifest_carries_its_customers_section() -> None:
    manifests = load_manifests(assistant_hosted.NAME)
    own = manifests[-1]
    assert own.prompt_sections == (assistant_hosted.CUSTOMERS_SECTION,)
    assert "customer-onboarding-help" in assistant_hosted.CUSTOMERS_SECTION.body


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


def test_dsqa_eval_packs_enforce_the_capability_tiers() -> None:
    for name, extensions in (
        (dsqa_eval.CORE_NAME, dsqa_eval.CORE_EXTENSIONS),
        (dsqa_eval.SEARCH_NAME, dsqa_eval.SEARCH_EXTENSIONS),
        (dsqa_eval.BROWSER_NAME, dsqa_eval.BROWSER_EXTENSIONS),
    ):
        assert discovered_packs()[name].extensions == extensions
        assert [manifest.name for manifest in load_manifests(name)] == [*extensions, name]


def test_gdpval_treatment_packs_are_discovered() -> None:
    for name, extensions in (
        (gdpval.CORE_NAME, gdpval.BASE_EXTENSIONS),
        (gdpval.DOCUMENTS_NAME, (*gdpval.BASE_EXTENSIONS, *gdpval.DOCUMENT_EXTENSIONS)),
        (gdpval.RESEARCH_NAME, (*gdpval.BASE_EXTENSIONS, *gdpval.RESEARCH_EXTENSIONS)),
        (
            gdpval.FULL_NAME,
            (*gdpval.BASE_EXTENSIONS, *gdpval.DOCUMENT_EXTENSIONS, *gdpval.RESEARCH_EXTENSIONS),
        ),
    ):
        assert discovered_packs()[name].extensions == extensions
        assert [manifest.name for manifest in load_manifests(name)] == [*extensions, name]


def test_no_pack_selected_leaves_the_unnarrowed_extension_set() -> None:
    active = {manifest.name for manifest in load_manifests()}
    assert set(assistant.EXTENSIONS) <= active
    assert assistant.NAME not in active


def test_every_shipped_skill_description_stays_a_routing_trigger() -> None:
    """A description rides in the skill index of every turn, so its length is a standing tax on
    every workspace the pack serves — AGENTS.md caps it at 50 words. Enforced here rather than
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


def test_every_pack_serving_the_portal_also_carries_the_digest_writer() -> None:
    """The radar projection reads `report_digest_entry`, a table the `report_digest` extension's own
    migration creates and only its packs apply. A pack that served the portal without the writer
    would migrate no such table and 500 the feed on every read, so the pairing is asserted here
    rather than left to whoever edits a pack's extension list next."""
    for name, pack in discovered_packs().items():
        if "web" in pack.extensions:
            assert "report_digest" in pack.extensions, name
