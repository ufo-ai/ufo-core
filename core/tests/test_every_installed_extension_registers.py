"""The keyless evidence floor: every installed extension and pack discovers, loads, and resolves.

Where `test_ext_conformance` drives the one sample extension through each point-type, this file
generalizes across the whole installed set: it enumerates every `selfhost.extension` and
`selfhost.pack` via the real loader (never a hardcoded list, so a newly installed extension is
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

from selfhost.blob import FilesystemBlobStore
from selfhost.config import (
    BUA_BROWSER_BACKEND,
    IN_PROCESS_BACKEND,
    BlobConfig,
    BrowserConfig,
    Config,
    ConnectConfig,
    DatabaseConfig,
    HubConfig,
    SandboxConfig,
)
from selfhost.credentials import CredentialStore
from selfhost.ext.context import context_for
from selfhost.ext.loader import (
    discovered,
    discovered_packs,
    embed_backend,
    index_backend,
    load_manifests,
    skill_registry,
    turn_hooks,
    turn_subagents,
    turn_tools,
)
from selfhost.ext.manifest import Manifest
from selfhost.jobs import bindings_from
from selfhost.loop.prompts.render import render_system_prompt
from selfhost.loop.subagents import SubagentRegistry
from selfhost.models.registry import model_registry
from selfhost.serve import (
    _connect_flow,
    _mount_ext_routes,
    _mount_surfaces,
    _select_browser,
    _select_carrier,
    _select_hub,
    _source_backends,
)
from selfhost.skills.runtime import parse_skill
from selfhost.tools.registry import ToolRegistry

INSTALLED: dict[str, tuple[Manifest, object]] = discovered()
PACKS = discovered_packs()
WORKSPACE_ID = uuid4()
REDIS_URL = "redis://localhost:6379/0"
PUBLIC_BASE_URL = "https://selfhost.test"
NOT_REGISTERED = ("no extension registers", "not a registered carrier")


def _credential_store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _config(
    *,
    hub_backend: str = IN_PROCESS_BACKEND,
    hub_url: str | None = None,
    browser_backend: str = BUA_BROWSER_BACKEND,
    sandbox_backend: str = "local",
) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        connect=ConnectConfig(public_base_url=PUBLIC_BASE_URL),
        hub=HubConfig(backend=hub_backend, url=hub_url),
        browser=BrowserConfig(backend=browser_backend),
        sandbox=SandboxConfig(backend=sandbox_backend),
    )


class _StubEmbed:
    """A stand-in EmbedClient handed to the index selection seam; index factories store it but never
    embed at construction, so its body is never reached — the seam resolution is what is asserted.
    """

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


class _StubDbos:
    """Stands in for the DBOS client the surface mount threads into its admission invoker; mounting
    only registers routes, so no method is called — the mounted route is what is asserted."""

    async def enqueue_async(self, options: object, workflow_id: str) -> None: ...


def _resolve_backend(select) -> None:
    """Run a backend selection seam and prove it routed to the extension's spec: a returned backend
    is full in-process construction; the only registration defect is core's own not-registered
    signal (always a RuntimeError raised by the selection seam before it reaches the factory). Any
    other error means the seam found the spec and a key- or service-gated factory declined to build
    keyless — e2b's carrier fails without its template env, embed-openai's SDK client refuses an
    empty key — which the Tier-B integration proofs cover, not this registration floor."""
    try:
        assert select() is not None
    except Exception as error:
        assert not any(signal in str(error) for signal in NOT_REGISTERED), str(error)


def _check_tools(manifest: Manifest, store: CredentialStore) -> None:
    declared = (
        *manifest.tools,
        *(tool for connector in manifest.connectors for tool in connector.tools),
    )
    if not declared:
        return
    tools, ext_by_tool = turn_tools((manifest,), WORKSPACE_ID, store)
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
        _resolve_backend(
            lambda spec=spec: index_backend(
                (manifest,), spec.name, _StubEmbed(), WORKSPACE_ID, store
            )
        )


def _check_embeds(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.embeds:
        _resolve_backend(
            lambda spec=spec: embed_backend((manifest,), spec.name, WORKSPACE_ID, store)
        )


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
            lambda spec=spec: _select_carrier(
                _config(sandbox_backend=spec.name), (manifest,)
            )
        )


def _check_browsers(manifest: Manifest, store: CredentialStore) -> None:
    for spec in manifest.browsers:
        _resolve_backend(
            lambda spec=spec: _select_browser(
                _config(browser_backend=spec.backend), (manifest,), WORKSPACE_ID, store
            )
        )


def _check_models(manifest: Manifest) -> None:
    if not manifest.models:
        return
    registry = model_registry(_config(), (manifest,))
    for spec in manifest.models:
        assert spec in registry.providers
        for model_id, _price in spec.prices:
            assert spec.matches(model_id)


def _check_sources(manifest: Manifest) -> None:
    if not manifest.sources:
        return
    backends = _source_backends((manifest,))
    for provider in manifest.sources:
        assert backends[provider.backend] is provider.source


def _check_surfaces(manifest: Manifest, store: CredentialStore, tmp_path: Path) -> None:
    if not manifest.surfaces:
        return
    app = FastAPI()
    _mount_surfaces(
        app,
        (manifest,),
        WORKSPACE_ID,
        store,
        FilesystemBlobStore(root=tmp_path),
        _StubDbos(),
        "",
        None,
    )
    mounted = {route.path for route in app.routes}
    for spec in manifest.surfaces:
        assert f"/surface/{spec.name}" in mounted


def _check_routes(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.routes:
        return
    app = FastAPI()
    _mount_ext_routes(app, (manifest,), WORKSPACE_ID, store, None, None)
    mounted = {route.path for route in app.routes}
    for spec in manifest.routes:
        assert f"/ext/{manifest.name}/{spec.path.lstrip('/')}" in mounted


def _check_hooks(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.hooks:
        return
    chain = turn_hooks((manifest,), WORKSPACE_ID, store)
    grouped = {
        "pre_tool_use": chain.pre_tool_use,
        "post_tool_use": chain.post_tool_use,
        "on_inbound": chain.on_inbound,
    }
    for spec in manifest.hooks:
        assert any(bound.spec is spec for bound in grouped[spec.event])


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
    context = context_for(WORKSPACE_ID, manifest.name, declared, store)
    for slot in manifest.credentials:
        assert slot.name in context.credentials.declared


def _check_onboarding(manifest: Manifest, store: CredentialStore) -> None:
    if not manifest.onboarding_steps:
        return
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(WORKSPACE_ID, manifest.name, declared, store)
    for step in manifest.onboarding_steps:
        assert callable(step.handler)
    assert context.store is not None


def _check_prompt_sections(manifest: Manifest) -> None:
    if not manifest.prompt_sections:
        return
    sections = tuple((section.name, section.body) for section in manifest.prompt_sections)
    rendered = render_system_prompt("You are the workspace assistant.", sections)
    assert rendered.digest.startswith("sha256:")
    for section in manifest.prompt_sections:
        assert section.body in rendered.content


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
    _check_browsers(manifest, store)
    _check_models(manifest)
    _check_sources(manifest)
    _check_surfaces(manifest, store, tmp_path)
    _check_routes(manifest, store)
    _check_hooks(manifest, store)
    _check_jobs(manifest)
    _check_skills(manifest)
    _check_subagents(manifest)
    _check_credentials(manifest, store)
    _check_onboarding(manifest, store)
    _check_prompt_sections(manifest)


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
