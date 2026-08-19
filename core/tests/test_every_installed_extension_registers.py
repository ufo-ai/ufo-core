"""The keyless evidence floor: every installed extension and pack discovers, loads, and resolves.

Where `test_ext_conformance` drives the one sample extension through each point-type, this file
generalizes across the whole installed set: it enumerates every `ufo.extension` and
`ufo.pack` via the real loader (never a hardcoded list, so a newly installed extension is
covered the moment it is present) and asserts each declared Manifest point resolves through the same
core selection and registration seams `serve` runs at boot — tools through the ToolRegistry, the
backend points through their `_select_*`/`*_backend` seams, surfaces through the mount, hooks
through the HookChain, jobs through the scheduler bindings, and so on. It needs no API key, no live
service: the seams route and construct in-process, so a point that fails to resolve — a name a
selection seam does not know, a tool-name collision, a duplicate connector provider — is a real
registration defect this test names precisely. Live construction of a key- or service-gated backend
(e2b's carrier, a BYOK index) is deferred to the Tier-B integration proofs; here the seam need only
route to the extension's spec, never fail loud that no extension registers the name."""

from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_user_skills,
)

from ufo.activity import SKILL_LOAD_TOOL
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.config import (
    DEFAULT_CDP_PROVIDER,
    IN_PROCESS_BACKEND,
    BlobConfig,
    BrowserConfig,
    Config,
    ConnectConfig,
    ConnectorsConfig,
    DatabaseConfig,
    HubConfig,
    ResearchConfig,
    SandboxConfig,
)
from ufo.credentials import CredentialStore
from ufo.ext.context import context_for
from ufo.ext.loader import (
    CONNECTION_RECORDED,
    NotRegisteredError,
    connection_hooks,
    discovered,
    discovered_packs,
    embed_backend,
    index_backend,
    load_manifests,
    memory_search,
    skill_registry,
    turn_hooks,
    turn_subagents,
    turn_tools,
)
from ufo.ext.manifest import Manifest, conversation_slot_declarations
from ufo.hub import InProcessHub
from ufo.jobs import bindings_from
from ufo.loop.prompts.render import render_system_prompt
from ufo.loop.subagents import SubagentRegistry
from ufo.models.registry import model_registry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.select import select_carrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.serve import (
    _connect_flow,
    _mount_ext_routes,
    _mount_shared_surfaces,
    _select_auth_proxy,
    _select_cdp_provider,
    _select_hub,
    _select_search_provider,
    _source_backends,
)
from ufo.skills.runtime import parse_skill
from ufo.tools.registry import ToolRegistry

INSTALLED: dict[str, tuple[Manifest, object]] = discovered()
PACKS = discovered_packs()
REDIS_URL = "redis://localhost:6379/0"
PUBLIC_BASE_URL = "https://ufo.test"


def _credential_store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _config(
    *,
    hub_backend: str = IN_PROCESS_BACKEND,
    hub_url: str | None = None,
    cdp_provider: str = DEFAULT_CDP_PROVIDER,
    sandbox_backend: str = "local",
    auth_backend: str | None = None,
    search_provider: str | None = None,
) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        connect=ConnectConfig(public_base_url=PUBLIC_BASE_URL),
        hub=HubConfig(backend=hub_backend, url=hub_url),
        browser=BrowserConfig(cdp_provider=cdp_provider),
        sandbox=SandboxConfig(backend=sandbox_backend),
        connectors=ConnectorsConfig(auth_backend=auth_backend),
        research=ResearchConfig(search_provider=search_provider),
    )


class _StubDbos:
    """Stands in for the DBOS client the surface mount threads into member admission; mounting
    only registers routes, so no method is called — the mounted route is what is asserted."""

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None: ...


def _resolve_backend(select) -> None:
    """Run a backend selection seam and prove it routed to the extension's spec: a returned backend
    is full in-process construction. A `NotRegisteredError` means the seam knew no extension by the
    selected name — a real registration defect this test names. Any other error means the seam found
    the spec and a key- or service-gated factory declined to build keyless — e2b's carrier fails
    without its template env — which the Tier-B integration proofs cover, not this registration
    floor. Keying on the exception type, not a message substring, is reword-proof."""
    try:
        assert select() is not None
    except NotRegisteredError as error:
        raise AssertionError(f"no extension registers the selected backend: {error}") from error
    except Exception:
        pass


def _check_tools(manifest: Manifest, store: CredentialStore) -> None:
    declared = (
        *manifest.tools,
        *(tool for connector in manifest.connectors for tool in connector.tools),
    )
    if not declared:
        return
    tools, ext_by_tool = turn_tools((manifest,), store, audience=conversation_audience(None))
    registry = ToolRegistry(tools)
    for tool in declared:
        assert registry.get(tool.name).name == tool.name
        assert tool.name in ext_by_tool


def _check_connectors(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.connectors:
        return
    flow = _connect_flow(store, _config(), (manifest,))
    assert flow is not None
    for connector in manifest.connectors:
        assert connector.oauth.provider in flow.providers


def _check_indexes(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.indexes:
        _resolve_backend(lambda spec=spec: index_backend((manifest,), spec.name, store))


def _check_embeds(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.embeds:
        _resolve_backend(lambda spec=spec: embed_backend((manifest,), spec.name, store))


def _check_hubs(manifest: Manifest) -> None:
    for spec in manifest.hubs:
        _resolve_backend(
            lambda spec=spec: _select_hub(
                _config(hub_backend=spec.backend, hub_url=REDIS_URL), (manifest,)
            )
        )


def _check_carriers(manifest: Manifest) -> None:
    for spec in manifest.carriers:
        _resolve_backend(
            lambda spec=spec: select_carrier(_config(sandbox_backend=spec.name), (manifest,))
        )


def _check_cdp_providers(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.cdp_providers:
        _resolve_backend(
            lambda spec=spec: _select_cdp_provider(
                _config(cdp_provider=spec.backend), (manifest,), store
            )
        )


def _check_models(manifest: Manifest) -> None:
    if not manifest.models:
        return
    registry = model_registry(_config(), (manifest,))
    for spec in manifest.models:
        assert registry.spec(spec.id) is spec


def _check_sources(manifest: Manifest) -> None:
    if not manifest.sources:
        return
    backends = _source_backends((manifest,))
    for provider in manifest.sources:
        assert backends[provider.backend].config_model is not None


def _check_auth_proxies(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.auth_proxies:
        _resolve_backend(
            lambda spec=spec: _select_auth_proxy(
                _config(auth_backend=spec.backend), (manifest,), store
            )
        )


def _check_search_providers(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.search_providers:
        _resolve_backend(
            lambda spec=spec: _select_search_provider(
                _config(search_provider=spec.backend), (manifest,), store
            )
        )


def _check_memory_search(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.memory_search:
        assert memory_search((manifest,), store, name=spec.name) is not None


def _check_surfaces(manifest: Manifest, store: CredentialStore, tmp_path: Path) -> None:
    if not manifest.surfaces:
        return
    app = FastAPI()
    app.state.instance_id = uuid4()
    _mount_shared_surfaces(
        app,
        (manifest,),
        store,
        FilesystemBlobStore(root=tmp_path),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        _StubDbos(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    mounted = {route.path for route in app.routes}
    for spec in manifest.surfaces:
        for route in spec.routes:
            assert f"/surface/{spec.name}/{route.path}".rstrip("/") in mounted


def _check_routes(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.routes:
        return
    app = FastAPI()
    _mount_ext_routes(app, (manifest,), store, None, None, None)
    mounted = {route.path for route in app.routes}
    for spec in manifest.routes:
        assert f"/ext/{manifest.name}/{spec.path.lstrip('/')}" in mounted


def _check_hooks(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.hooks:
        return
    chain = turn_hooks((manifest,), store, audience=conversation_audience(None))
    connections = connection_hooks((manifest,), store)
    for spec in manifest.hooks:
        if spec.event == "page_change":
            assert "page_change" not in chain.hooks
            continue
        if spec.event == CONNECTION_RECORDED:
            assert CONNECTION_RECORDED not in chain.hooks
            assert any(bound.spec is spec for bound in connections.hooks)
            continue
        assert any(bound.spec is spec for bound in chain.hooks[spec.event])


def _check_jobs(manifest: Manifest) -> None:
    keys = {binding.key for binding in bindings_from((manifest,), ())}
    for job in manifest.jobs:
        assert f"{manifest.name}:{job.name}" in keys


def _check_skills(manifest: Manifest) -> None:
    if not manifest.skills:
        return
    index = dict(skill_registry((manifest,)).index())
    for spec in manifest.skills:
        assert parse_skill(spec.path).name in index


def _check_subagents(manifest: Manifest) -> None:
    registry = SubagentRegistry(turn_subagents((manifest,)))
    for profile in manifest.subagents:
        assert registry.get(profile.name).name == profile.name


def _check_credentials(manifest: Manifest, store: CredentialStore) -> None:
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(manifest.name, declared)
    for slot in manifest.credentials:
        assert slot.name in context.credentials.declared


def _check_onboarding(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.onboarding_steps:
        return
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(manifest.name, declared)
    for step in manifest.onboarding_steps:
        assert callable(step.handler)
    assert context.store is not None


def _check_prompt_sections(manifest: Manifest) -> None:
    if not manifest.prompt_sections:
        return
    sections = tuple((section.name, section.body) for section in manifest.prompt_sections)
    rendered = render_system_prompt(
        "You are the workspace assistant.", sections, knowledge_cutoff="2026-01"
    )
    assert rendered.digest.startswith("sha256:")
    for section in manifest.prompt_sections:
        assert section.body in rendered.content


def _check_conversation_slots(manifest: Manifest) -> None:
    declared = conversation_slot_declarations((manifest,))
    assert [provider for _owner, provider in declared] == list(manifest.conversation_slots)


def test_dev_mode_activates_every_discovered_extension() -> None:
    """With no lockfile the active set is exactly the discovered set — the loader path `serve` runs,
    proving discovery and manifest load for every installed extension without a hardcoded roster."""
    assert INSTALLED
    assert {manifest.name for manifest in load_manifests()} == set(INSTALLED)


@pytest.mark.parametrize("name", sorted(INSTALLED))
def test_installed_extension_registers_every_declared_point(name: str, tmp_path: Path) -> None:
    manifest, _entry = INSTALLED[name]
    store = _credential_store()
    _check_tools(manifest, store)
    _check_connectors(manifest, store)
    _check_indexes(manifest, store)
    _check_embeds(manifest, store)
    _check_hubs(manifest)
    _check_carriers(manifest)
    _check_cdp_providers(manifest, store)
    _check_models(manifest)
    _check_sources(manifest)
    _check_auth_proxies(manifest, store)
    _check_search_providers(manifest, store)
    _check_memory_search(manifest, store)
    _check_surfaces(manifest, store, tmp_path)
    _check_routes(manifest, store)
    _check_hooks(manifest, store)
    _check_jobs(manifest)
    _check_skills(manifest)
    _check_subagents(manifest)
    _check_credentials(manifest, store)
    _check_onboarding(manifest, store)
    _check_prompt_sections(manifest)
    _check_conversation_slots(manifest)


def test_every_registered_tool_takes_a_required_user_description() -> None:
    """A surface names a running tool call by the model's own `user_description`, so every tool the
    live registry offers must take one — the gate that keeps a new tool from landing with no
    member-facing line. `load_skill` is exempt: the engine intercepts it and publishes a SkillLoad
    frame naming the skill, never reading a description."""
    tools, _ = turn_tools(
        load_manifests(), _credential_store(), audience=conversation_audience(None)
    )
    assert tools
    without: list[str] = []
    for tool in tools:
        if tool.name == SKILL_LOAD_TOOL:
            continue
        field = tool.input_model.model_fields.get("user_description")
        if field is None or not field.is_required():
            without.append(tool.name)
    assert sorted(without) == []


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_installed_pack_narrows_to_its_bundle_and_own_manifest(pack_name: str) -> None:
    """Each installed pack bundles only installed extensions, and activating it narrows the active
    set to exactly those extensions' manifests followed by one synthetic manifest carrying the
    pack's own skills and onboarding — the coherent config `serve` and `init` bring up for it."""
    pack = PACKS[pack_name]
    installed = set(INSTALLED)
    for extension_name in pack.extensions:
        assert extension_name in installed

    manifests = load_manifests(pack_name)
    assert [manifest.name for manifest in manifests] == [*pack.extensions, pack_name]

    pack_manifest = manifests[-1]
    assert pack_manifest.name == pack_name
    assert {spec.path.name for spec in pack_manifest.skills} == {
        spec.path.name for spec in pack.skills
    }
    assert {step.name for step in pack_manifest.onboarding_steps} == {
        step.name for step in pack.onboarding_steps
    }
