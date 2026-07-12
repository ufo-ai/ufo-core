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
import ufo_pack_assistant_hosted as assistant_hosted
import ufo_pack_chief_of_staff as chief_of_staff

import ufo.ext.loader as loader
from ufo.ext.loader import discovered_packs, load_manifests
from ufo.ext.manifest import Pack
from ufo.skills.runtime import parse_skill


def test_assistant_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert assistant.NAME in packs
    assert packs[assistant.NAME].extensions == assistant.EXTENSIONS


def test_activating_the_assistant_pack_makes_exactly_its_bundle_active() -> None:
    """Naming the assistant pack narrows the active set to its bundled extensions in declared order,
    followed by the pack's own (here empty) manifest — the coherent config a serve brings up."""
    names = [manifest.name for manifest in load_manifests(assistant.NAME)]
    assert names == [*assistant.EXTENSIONS, assistant.NAME]


def test_assistant_hosted_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert assistant_hosted.NAME in packs
    assert packs[assistant_hosted.NAME].extensions == assistant_hosted.EXTENSIONS


def test_activating_the_assistant_hosted_pack_makes_exactly_its_bundle_active() -> None:
    """The hosted variant narrows to its managed-infra bundle (Turbopuffer, Slack, Redis, E2B on top
    of the assistant capabilities) in declared order, followed by the pack's own manifest."""
    names = [manifest.name for manifest in load_manifests(assistant_hosted.NAME)]
    assert names == [*assistant_hosted.EXTENSIONS, assistant_hosted.NAME]


def test_chief_of_staff_pack_is_discovered_with_its_bundle() -> None:
    packs = discovered_packs()
    assert chief_of_staff.NAME in packs
    assert packs[chief_of_staff.NAME].extensions == chief_of_staff.EXTENSIONS


def test_activating_the_chief_of_staff_pack_makes_exactly_its_bundle_active() -> None:
    """The chief-of-staff pack narrows to its feed-and-review bundle (brokered connectors plus
    sources, memory and the graph, the Slack front door, scheduling, watches, todos, workspace
    skills, self-improvement) in declared order, followed by the pack's own manifest carrying its
    four workflow skills."""
    manifests = load_manifests(chief_of_staff.NAME)
    assert [manifest.name for manifest in manifests] == [
        *chief_of_staff.EXTENSIONS,
        chief_of_staff.NAME,
    ]
    own = manifests[-1]
    parsed = {parse_skill(spec.path).name for spec in own.skills}
    assert parsed == set(chief_of_staff.SKILL_NAMES)


def test_no_pack_selected_leaves_the_unnarrowed_extension_set() -> None:
    active = {manifest.name for manifest in load_manifests()}
    assert set(assistant.EXTENSIONS) <= active
    assert assistant.NAME not in active


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
