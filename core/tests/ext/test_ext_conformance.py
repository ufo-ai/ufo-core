"""The extension seam's conformance probe: drive the installed sample through each public entry.

The sample is a real workspace member discovered via its `ufo.extension` entry point, so this
file first proves discovery (`load_manifests` finds it) and then exercises every declared point —
tool, credential slot, job, route — through the narrowest public surface core uses, reading the
sample's own recorded rows back through `ScopedStore`. The negative cases ride along: an undeclared
slot is refused, and a second workspace can reach none of the first's rows. Breaking the sample
breaks this probe, and a Manifest field the sample stops registering breaks the conformance gate."""

import asyncio
import json
from base64 import urlsafe_b64decode
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_index_default as index_default
import ufo_ext_sample as sample
import ufo_pack_sample as sample_pack
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openfeature.provider.in_memory_provider import InMemoryProvider
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import (
    BlobConfig,
    BrowserConfig,
    Config,
    ConnectorsConfig,
    DatabaseConfig,
    FlagsConfig,
    HubConfig,
    ResearchConfig,
    SandboxConfig,
    TerminalConfig,
)
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.models.interface import Message, ModelRequest, TextDelta
from ufo.harness.models.registry import model_registry
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.exec_env import ProbeEnv
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.select import select_carriers
from ufo.harness.sandbox.session import (
    ExecResult,
    ProbeTokenCodec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.harness.sandbox.terminal import Terminals
from ufo.host.ext.loader import (
    discovered_packs,
    embed_backend,
    index_backend,
    load_manifests,
    memory_search,
    skill_registry,
    turn_hooks,
    turn_subagent_grants,
    turn_subagents,
    turn_tools,
    turn_workspace_facts,
)
from ufo.onboard.onboarding import run_onboarding_steps
from ufo.runtime.access.connectors import UnknownBrokerTool
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import (
    ConversationProbes,
    ExtensionContext,
    ScopedStore,
    SourceReader,
    TrajectoryCorpus,
    UndeclaredCredentialSlot,
    context_for,
)
from ufo.runtime.ext.conversation_slots import ConversationSlotContext
from ufo.runtime.ext.manifest import AuthProxySpec, CarrierSpec, Manifest
from ufo.runtime.ext.surface import WRITEBACK_DELIVERED
from ufo.runtime.hub import InProcessHub
from ufo.runtime.indexing import OWNER_KIND_MEMORY_ITEM, Chunk, TextChunker
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.kinds.governance import prompt_digest
from ufo.runtime.listings import ListingCursor
from ufo.runtime.prompts.render import render_system_prompt
from ufo.runtime.search import FetchRequest, SearchQuery
from ufo.runtime.skills.runtime import install_skill
from ufo.runtime.sources.sync import CorePageFeed, SyncDriver
from ufo.runtime.subagents import FINISH_CONTRACT, SubagentRegistry, subagent_system_prompt
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.audience import SHARED_AUDIENCE, audience_subjects, conversation_audience
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.turns.transcript import Conversation, transcript_key
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, Agent, Turn, Usage
from ufo.serve import (
    _connector_registry,
    _mount_ext_routes,
    _mount_shared_surfaces,
    _select_auth_proxy,
    _select_cdp_provider,
    _select_flag_provider,
    _select_hub,
    _select_search_provider,
    _select_terminal_transport,
    _source_backends,
    _validate_requires,
)

SANDBOX_UNTOUCHED = "the sample tool records through its store and must not reach the sandbox"


def _sample_manifest() -> Manifest:
    found = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert found is not None, "sample extension not discovered via entry points — run `uv sync`"
    return found


def _credential_store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def test_sample_conversation_slot_callbacks_are_typed() -> None:
    provider = _sample_manifest().conversation_slots[0]
    audience = conversation_audience(None)
    slot_context = ConversationSlotContext(
        ext=context_for(sample.NAME, frozenset(), audience=audience),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        audience=audience,
        messages=(),
        public_base_url=None,
    )

    assert await provider.summarize(slot_context) is None
    assert (await provider.read(slot_context)).model_dump(mode="json") == {
        "type": "changes",
        "changes": [],
        "truncated": False,
    }


TOKEN_SECRET = "conformance-token-secret"
CURSOR_STAMP = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
CURSOR_ITEM = "11111111-1111-4111-8111-111111111111"


def _bearer(workspace_id: UUID) -> dict[str, str]:
    """A signed member bearer the sample's `identify` resolvers claim their workspace from — the
    shared fleet scopes each request by it, exactly as the `ufo` surface does."""
    token = mint_token(TOKEN_SECRET, str(workspace_id), "probe@x.test", timedelta(hours=1))
    return {"authorization": f"Bearer {token}"}


def _forged_bearer(workspace_id: UUID) -> dict[str, str]:
    """A correctly-shaped bearer minted by an untrusted signer (a foreign secret): it reaches the
    sample's `identify`, but its signature does not verify, so `workspace_claim` returns None and
    the request is refused before any handler — the forbidden-act half of the seam."""
    token = mint_token(
        "untrusted-signer-secret", str(workspace_id), "probe@x.test", timedelta(hours=1)
    )
    return {"authorization": f"Bearer {token}"}


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_note(workspace_id: UUID) -> None:
    """Give the workspace a row in the sample's own `sample_ext_note` table — the table its tick
    job's candidate selector reads — so the dispatcher names this workspace and binds it before
    firing the handler, exactly as the fleet fires the job."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(sample.NOTE_TABLE).values(workspace_id=workspace_id, note="seed")
        )


async def _grantable(workspace_id: UUID) -> tuple[UUID, UUID, UUID]:
    """A member, an agent, and their conversation — the foreign-key rows a recorded grant
    references, so the connector tool can authenticate the turn-agent's grant."""
    member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, agent_id, conversation_id


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("the sample tool must not spawn a subagent")


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
        workspace_root=root,
    )


def _probes(sandboxes: ConversationSandbox) -> ConversationProbes:
    return ConversationProbes(
        sandboxes, ProbeTokenCodec(b"conformance-probe-secret"), ProbeEnv().exports
    )


def _tool_context(workspace_id: UUID, ext: ExtensionContext, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=ext,
    )


async def test_sample_is_discovered_via_its_entry_point() -> None:
    manifest = _sample_manifest()
    assert {tool.name for tool in manifest.tools} == {
        sample.TOOL_NAME,
        sample.NOTE_TOOL_NAME,
        sample.AUDIT_ACTION,
        sample.POLISH_ACTION,
        sample.ENGRAVE_ACTION,
        sample.DIVINE_ACTION,
        sample.CALIBRATE_ACTION,
        sample.BLESS_ACTION,
        sample.BESEECH_ACTION,
    }
    assert {job.name for job in manifest.jobs} == {sample.JOB_NAME}
    assert {route.path for route in manifest.routes} == {sample.ROUTE_PATH}
    assert {slot.name for slot in manifest.credentials} == {sample.API_SLOT}
    assert {step.name for step in manifest.onboarding_steps} == {sample.ONBOARDING_NAME}
    assert {connector.oauth.provider for connector in manifest.connectors} == {
        sample.CONNECTOR_PROVIDER
    }
    assert {tool.name for connector in manifest.connectors for tool in connector.tools} == {
        sample.CONNECTOR_EXECUTE_TOOL_NAME
    }
    assert {section.name for section in manifest.prompt_sections} == {sample.SECTION_NAME}
    assert {fact.name for fact in manifest.workspace_facts} == {sample.WORKSPACE_FACT_NAME}
    assert {profile.name for profile in manifest.subagents} == {sample.SUBAGENT_NAME}
    assert {surface.name for surface in manifest.surfaces} == {
        sample.SURFACE_NAME,
        sample.SURFACE_LIVE_NAME,
    }
    assert {source.backend for source in manifest.sources} == {sample.SOURCE_BACKEND}
    assert {kind.name for kind in manifest.objects} == {sample.WIDGET_KIND, sample.RELIC_KIND}
    assert {spec.name for spec in manifest.indexes} == {sample.INDEX_BACKEND}
    assert {spec.backend for spec in manifest.hubs} == {sample.HUB_BACKEND}
    assert {spec.backend for spec in manifest.terminal_transports} == {sample.TERMINAL_BACKEND}
    assert {spec.path.name for spec in manifest.skills} == {sample.SKILL_NAME}
    assert {spec.backend for spec in manifest.cdp_providers} == {sample.CDP_PROVIDER}
    assert {carrier.name for carrier in manifest.carriers} == {sample.CARRIER_NAME}
    assert {spec.backend for spec in manifest.auth_proxies} == {sample.AUTH_PROXY_BACKEND}
    assert {spec.backend for spec in manifest.search_providers} == {sample.SEARCH_PROVIDER}
    assert {spec.backend for spec in manifest.flag_providers} == {sample.FLAG_BACKEND}


@pytest.mark.parametrize("topic", ["", "\n", " \t"])
def test_sample_source_requires_a_nonempty_topic(topic: str) -> None:
    with pytest.raises(ValueError):
        sample.SampleSourceConfig(topic=topic)


def test_core_selects_a_manifest_contributed_hub() -> None:
    """The `hubs` seam end to end: core's boot-time selection knows only the in-process default, so
    resolving the sample's backend name proves the Manifest `hubs` point flowed into selection.
    Selecting a name no manifest registers, and two manifests claiming one name, both fail loud."""
    manifest = _sample_manifest()

    def _config(backend: str) -> Config:
        return Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
            blob=BlobConfig(backend="filesystem", root=Path()),
            hub=HubConfig(backend=backend),
        )

    assert isinstance(_select_hub(_config("in_process"), ()), InProcessHub)
    assert isinstance(_select_hub(_config(sample.HUB_BACKEND), (manifest,)), InProcessHub)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_hub(_config(sample.HUB_BACKEND), ())
    with pytest.raises(RuntimeError, match="two extensions register hub backend"):
        _select_hub(_config(sample.HUB_BACKEND), (manifest, manifest))


def test_core_selects_a_manifest_contributed_terminal_transport(tmp_path: Path) -> None:
    """The `terminal_transports` seam end to end, mirroring the hub: core's boot-time selection
    knows only the in-process default, so resolving the sample's backend name proves the Manifest
    `terminal_transports` point flowed into selection. Selecting a name no manifest registers, and
    two manifests claiming one name, both fail loud."""
    manifest = _sample_manifest()
    blob = FilesystemBlobStore(root=tmp_path)

    def _config(backend: str) -> Config:
        return Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
            blob=BlobConfig(backend="filesystem", root=Path()),
            terminal=TerminalConfig(backend=backend),
        )

    assert isinstance(_select_terminal_transport(_config("in_process"), (), blob), Terminals)
    assert isinstance(
        _select_terminal_transport(_config(sample.TERMINAL_BACKEND), (manifest,), blob), Terminals
    )
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_terminal_transport(_config(sample.TERMINAL_BACKEND), (), blob)
    with pytest.raises(RuntimeError, match="two extensions register terminal transport"):
        _select_terminal_transport(_config(sample.TERMINAL_BACKEND), (manifest, manifest), blob)


def test_a_cross_process_hub_with_the_in_process_terminal_transport_fails_loud(
    tmp_path: Path,
) -> None:
    """A cross-process hub declares a multi-instance fleet, whose held connection and turn workflow
    land on different pods; the process-local terminal transport cannot reach across them, so that
    combination fails loud at boot rather than stranding every cross-pod turn. The coherent combos
    resolve normally: in-process both, or a cross-process transport beside a cross-process hub."""
    manifest = _sample_manifest()
    blob = FilesystemBlobStore(root=tmp_path)

    def _config(terminal_backend: str, hub_backend: str) -> Config:
        return Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
            blob=BlobConfig(backend="filesystem", root=Path()),
            terminal=TerminalConfig(backend=terminal_backend),
            hub=HubConfig(backend=hub_backend),
        )

    with pytest.raises(RuntimeError, match="cannot serve"):
        _select_terminal_transport(_config("in_process", "redis"), (manifest,), blob)
    assert isinstance(
        _select_terminal_transport(_config("in_process", "in_process"), (), blob), Terminals
    )
    assert isinstance(
        _select_terminal_transport(_config(sample.TERMINAL_BACKEND, "redis"), (manifest,), blob),
        Terminals,
    )


def _cdp_config(cdp_provider: str) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
        browser=BrowserConfig(cdp_provider=cdp_provider),
    )


def test_core_selects_a_manifest_contributed_cdp_provider() -> None:
    """The `cdp_providers` seam end to end: core ships no provider, so resolving the sample's
    registered provider name proves the Manifest `cdp_providers` point flowed into selection.
    Selecting a name no manifest registers yields None (a no-browser deploy needs none); two
    manifests claiming one name fails loud; a provider whose extension declares credential slots
    with no key set fails loud."""
    manifest = _sample_manifest()
    store = _credential_store()

    assert _select_cdp_provider(_cdp_config("nonesuch"), (), None) is None
    selected = _select_cdp_provider(_cdp_config(sample.CDP_PROVIDER), (manifest,), store)
    assert isinstance(selected, sample.SampleCdpProvider)
    assert _select_cdp_provider(_cdp_config(sample.CDP_PROVIDER), (), store) is None
    with pytest.raises(RuntimeError, match="two extensions register cdp provider"):
        _select_cdp_provider(_cdp_config(sample.CDP_PROVIDER), (manifest, manifest), store)
    with pytest.raises(RuntimeError, match="declares credential slots"):
        _select_cdp_provider(_cdp_config(sample.CDP_PROVIDER), (manifest,), None)


async def test_sample_cdp_provider_yields_a_drivable_lease() -> None:
    """The consumer half of the `cdp_providers` seam through the probe: the registered provider
    mints a per-turn lease driven through the same `CdpLease` protocol the browser engine calls — a
    real object leased and released end to end, not a mock."""
    lease = await sample.SampleCdpProvider().lease()
    endpoint = await lease.endpoint()
    assert endpoint.url == sample.SAMPLE_CDP_URL
    assert await lease.token() == sample.SAMPLE_CDP_URL

    async def unread() -> bytes:
        raise AssertionError("a sandbox-local transport must not pull the file across")

    assert await lease.place_file("/workspace/report.pdf", unread) == "/workspace/report.pdf"
    download_dir = Path(await lease.download_dir())
    await asyncio.to_thread(download_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((download_dir / "guid-3").write_bytes, b"probe download")
    assert await lease.fetch_download("guid-3") == b"probe download"
    await lease.aclose()


def test_boot_validation_of_requires_fails_when_no_cdp_provider_is_registered() -> None:
    """The `requires` + boot-validation seam end to end: an extension declaring `requires` a seam
    whose backend is unusable fails `serve` at boot. With the browser extension's
    `requires=("cdp_providers",)` and the selected provider registered by no active extension,
    `_validate_requires` fails loud naming the extension and the seam; registering that provider
    clears it. A `requires` an extension names that core does not know also fails."""
    browser = Manifest(name="browser", version="0", requires=("cdp_providers",))
    with pytest.raises(RuntimeError, match=r"requires the 'cdp_providers' seam"):
        _validate_requires(_cdp_config(sample.CDP_PROVIDER), (browser,), None)

    provider = _sample_manifest()
    _validate_requires(_cdp_config(sample.CDP_PROVIDER), (browser, provider), _credential_store())

    unknown = Manifest(name="needs-nothing-real", version="0", requires=("nonesuch",))
    with pytest.raises(RuntimeError, match="unknown seam 'nonesuch'"):
        _validate_requires(_cdp_config(sample.CDP_PROVIDER), (unknown,), None)


def _connectors_config(backend: str | None) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
        connectors=ConnectorsConfig(auth_backend=backend),
    )


def test_core_selects_a_manifest_contributed_auth_proxy() -> None:
    """The `auth_proxies` seam end to end: core's boot-time selection has no built-in auth proxy, so
    automatically resolving the sample's sole backend proves the Manifest `auth_proxies` point
    flowed into selection and was built with a credential reader. An explicit name selects among
    multiple backends; an unset ambiguous choice names every option. An unknown name, duplicate
    registration, and a selected backend with no credential key each fail loud."""
    manifest = _sample_manifest()
    store = _credential_store()
    other_backend = "other_auth_proxy"
    other = Manifest(
        name="other-auth-proxy",
        version="1",
        auth_proxies=(
            AuthProxySpec(
                backend=other_backend,
                build=lambda _credentials: pytest.fail("selected the wrong auth proxy"),
            ),
        ),
    )

    assert _select_auth_proxy(_connectors_config(None), (), store) is None
    automatic = _select_auth_proxy(_connectors_config(None), (manifest,), store)
    assert isinstance(automatic, sample.SampleAuthProxy)
    selected = _select_auth_proxy(_connectors_config(sample.AUTH_PROXY_BACKEND), (manifest,), store)
    assert isinstance(selected, sample.SampleAuthProxy)
    explicit = _select_auth_proxy(
        _connectors_config(sample.AUTH_PROXY_BACKEND), (other, manifest), store
    )
    assert isinstance(explicit, sample.SampleAuthProxy)
    with pytest.raises(RuntimeError, match="auth_backend is unset") as ambiguity:
        _select_auth_proxy(_connectors_config(None), (other, manifest), store)
    assert other_backend in str(ambiguity.value)
    assert sample.AUTH_PROXY_BACKEND in str(ambiguity.value)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_auth_proxy(_connectors_config("nope"), (manifest,), store)
    with pytest.raises(RuntimeError, match="two extensions register auth proxy"):
        _select_auth_proxy(
            _connectors_config(sample.AUTH_PROXY_BACKEND), (manifest, manifest), store
        )
    with pytest.raises(RuntimeError, match="needs a credential key"):
        _select_auth_proxy(_connectors_config(sample.AUTH_PROXY_BACKEND), (manifest,), None)


def test_connector_registry_holds_broker_routes_and_the_selected_direct_backend() -> None:
    """The registry holds broker routing and the selected direct backend; source credential
    resolution stays behind core's connection-bound resolver rather than exposing an unchecked
    account-handle method to extension tools."""
    manifest = _sample_manifest()
    store = _credential_store()

    registry = _connector_registry(
        _connectors_config(sample.AUTH_PROXY_BACKEND), (manifest,), store
    )
    entry = registry.entry(sample.CONNECTOR_PROVIDER)
    assert entry.label == sample.CONNECTOR_LABEL
    assert isinstance(registry.fallback, sample.SampleAuthProxy)

    bare = _connector_registry(
        _connectors_config(None), (replace(manifest, auth_proxies=()),), store
    )
    assert bare.fallback is None
    with pytest.raises(KeyError, match="no installed connector"):
        bare.entry("unbrokered")


async def test_sample_broker_answers_the_dynamic_tool_surface() -> None:
    """The broker half through the probe: catalog, schema (an unknown slug raises
    `UnknownBrokerTool`), search, and an execute whose response echoes exactly what core dispatched
    — provider, slug, arguments, account, and the per-call idempotency key — so a consumer asserts
    the dispatch off the broker's own public answer, never a mock log."""
    broker = sample._SampleBroker()
    workspace_id = uuid4()
    listed = await broker.tools(workspace_id, sample.CONNECTOR_PROVIDER, "widgets")
    assert [tool.slug for tool in listed] == [sample.BROKER_TOOL_SLUG]
    described = await broker.schema(
        workspace_id, sample.CONNECTOR_PROVIDER, sample.BROKER_TOOL_SLUG
    )
    assert described.input_schema["properties"] == {"limit": {"type": "integer"}}
    with pytest.raises(UnknownBrokerTool):
        await broker.schema(workspace_id, sample.CONNECTOR_PROVIDER, "NOT_A_TOOL")
    found = await broker.search(workspace_id, sample.CONNECTOR_PROVIDER, "list widgets")
    assert found.plan == (sample.BROKER_SEARCH_PLAN,)
    executed = await broker.execute(
        workspace_id,
        sample.CONNECTOR_PROVIDER,
        sample.BROKER_TOOL_SLUG,
        {"limit": 3},
        sample.CONNECTOR_ACCOUNT,
        "t1/x/c1",
    )
    assert executed == {
        "provider": sample.CONNECTOR_PROVIDER,
        "slug": sample.BROKER_TOOL_SLUG,
        "arguments": {"limit": 3},
        "account": sample.CONNECTOR_ACCOUNT,
        "idempotency_key": "t1/x/c1",
    }


async def test_sample_auth_proxy_resolves_a_credential() -> None:
    """The consumer half of the `auth_proxies` seam through the probe: the registered proxy resolves
    a `Credential` through the same protocol the connector backend calls — a real object exercised,
    not a mock."""
    credential = await sample.SampleAuthProxy().credential(uuid4(), "provider", "account")
    assert credential.bearer == sample.AUTH_PROXY_BEARER
    assert credential.transport is None


def _search_config(search_provider: str | None) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
        research=ResearchConfig(search_provider=search_provider),
    )


def test_core_selects_a_manifest_contributed_search_provider() -> None:
    """The `search_providers` seam end to end: core ships no default search backend, so resolving
    the sample's provider name proves the Manifest `search_providers` point flowed into selection,
    built with a credential reader. An unset knob yields None (no research backend selected);
    selecting a name no extension registers, two extensions claiming one name, and a selected
    backend with no credential key each fail loud."""
    manifest = _sample_manifest()
    store = _credential_store()

    assert _select_search_provider(_search_config(None), (manifest,), store) is None
    selected = _select_search_provider(_search_config(sample.SEARCH_PROVIDER), (manifest,), store)
    assert isinstance(selected, sample.SampleSearchProvider)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_search_provider(_search_config("nope"), (manifest,), store)
    with pytest.raises(RuntimeError, match="two extensions register search provider"):
        _select_search_provider(_search_config(sample.SEARCH_PROVIDER), (manifest, manifest), store)
    with pytest.raises(RuntimeError, match="needs a credential key"):
        _select_search_provider(_search_config(sample.SEARCH_PROVIDER), (manifest,), None)


def _flags_config(backend: str | None, cache_ttl_seconds: float = 30.0) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
        flags=FlagsConfig(backend=backend, cache_ttl_seconds=cache_ttl_seconds),
    )


def test_core_selects_a_manifest_contributed_flag_provider() -> None:
    """The `flag_providers` seam end to end: core ships no flag backend, so resolving the sample's
    backend name proves the Manifest `flag_providers` point flowed into selection, built with the
    deploy's cache window. An unset knob yields None — the deploy then reads every flag as its code
    default — while a name no extension registers and a name two register each fail loud, because a
    deploy that thinks it reads flags and reads none would gate features on nothing."""
    manifest = _sample_manifest()

    assert _select_flag_provider(_flags_config(None), (manifest,)) is None
    selected = _select_flag_provider(_flags_config(sample.FLAG_BACKEND), (manifest,))
    assert isinstance(selected, InMemoryProvider)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_flag_provider(_flags_config("nope"), (manifest,))
    with pytest.raises(RuntimeError, match="two extensions register flag provider"):
        _select_flag_provider(_flags_config(sample.FLAG_BACKEND), (manifest, manifest))


async def test_sample_search_provider_answers_a_query_and_fetches() -> None:
    """The consumer half of the `search_providers` seam through the probe: the registered provider
    answers a `SearchQuery` and fetches a URL through the same protocol the research tools call — a
    real object driven end to end, not a mock."""
    provider = sample.SampleSearchProvider()
    assert provider.supports_fetch is True
    results = await provider.search(SearchQuery(query="orbital widgets", num_results=3))
    assert results.answer == sample.SAMPLE_SEARCH_ANSWER
    assert results.hits[0].url == sample.SAMPLE_SEARCH_URL
    page = await provider.fetch(FetchRequest(url="https://sample.test/page"))
    assert page.url == "https://sample.test/page"
    assert page.text == sample.SAMPLE_FETCH_TEXT


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sample_memory_search_provider_receives_the_exact_subjects(db: None) -> None:
    workspace_id = await _workspace()
    member_id = uuid4()
    audience = conversation_audience(member_id)
    reader = SourceReader(
        agent_id=uuid4(),
        requesting_member_id=member_id,
        subjects=audience_subjects(audience),
    )
    access = memory_search(
        (_sample_manifest(),), _credential_store(), name=sample.MEMORY_SEARCH_PROVIDER
    )
    assert access is not None
    with ws(workspace_id):
        matches = await access.search(reader, ("customer history",))
        recorded = await ScopedStore(extension=sample.NAME).get(sample.MEMORY_SEARCH_KEY)
    assert matches[0].text == sample.SAMPLE_MEMORY_TEXT
    assert recorded == {
        "queries": ["customer history"],
        "subjects": sorted(audience_subjects(audience)),
        "start": None,
        "end": None,
    }
    with ws(workspace_id):
        listed = await access.list_recent(audience_subjects(audience), 25)
        browsed = await ScopedStore(extension=sample.NAME).get(sample.MEMORY_RECENT_KEY)
        narrowed = await access.list_recent(
            audience_subjects(audience),
            25,
            frozenset({sample.SAMPLE_MEMORY_KIND}),
            ListingCursor(created_at=CURSOR_STAMP, item_id=CURSOR_ITEM, newer=True),
        )
        walked = await ScopedStore(extension=sample.NAME).get(sample.MEMORY_RECENT_KEY)
    assert listed.rows[0].text == sample.SAMPLE_MEMORY_TEXT
    assert browsed == {
        "subjects": sorted(audience_subjects(audience)),
        "limit": 25,
        "kinds": None,
        "cursor": None,
    }
    assert narrowed.rows[0].text == sample.SAMPLE_MEMORY_TEXT
    assert access.listable_kinds() == (sample.SAMPLE_MEMORY_KIND,)
    assert walked == {
        "subjects": sorted(audience_subjects(audience)),
        "limit": 25,
        "kinds": [sample.SAMPLE_MEMORY_KIND],
        "cursor": {
            "created_at": CURSOR_STAMP.isoformat(),
            "item_id": CURSOR_ITEM,
            "newer": True,
        },
    }


def test_memory_search_registration_is_unique_and_required() -> None:
    provider = next(manifest for manifest in load_manifests() if manifest.name == "memory")
    consumer = Manifest(name="memory-consumer", version="0", requires=("memory_search",))
    assert memory_search((), _credential_store()) is None
    assert memory_search(load_manifests(), _credential_store()) is not None
    with pytest.raises(RuntimeError, match=r"requires the 'memory_search' seam"):
        _validate_requires(_cdp_config(sample.CDP_PROVIDER), (consumer,), _credential_store())
    _validate_requires(_cdp_config(sample.CDP_PROVIDER), (consumer, provider), _credential_store())
    with pytest.raises(RuntimeError, match="two extensions register memory search"):
        memory_search((provider, provider), _credential_store())


def test_boot_validation_of_requires_fails_when_no_search_backend_is_configured() -> None:
    """The `requires` + boot-validation seam for search: an extension declaring `requires`
    `search_providers` fails `serve` at boot when `[research] search_provider` is unset or names no
    registered backend, and clears once the knob names a registered backend the sample provides."""
    store = _credential_store()
    manifest = _sample_manifest()
    research = Manifest(name="research", version="0", requires=("search_providers",))
    with pytest.raises(RuntimeError, match=r"requires the 'search_providers' seam"):
        _validate_requires(_search_config(None), (research, manifest), store)
    with pytest.raises(RuntimeError, match=r"requires the 'search_providers' seam"):
        _validate_requires(_search_config("nope"), (research, manifest), store)
    _validate_requires(_search_config(sample.SEARCH_PROVIDER), (research, manifest), store)


def _carrier_config(backend: str) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend=backend),
    )


def test_config_backend_defaults_to_the_built_in_local_carrier() -> None:
    selected = select_carriers(_carrier_config("local"), ())
    assert isinstance(selected.carrier, LocalCarrier)
    assert selected.spec.off_cluster is False
    assert selected.spec.sizes == ()


def test_config_backend_selects_a_manifest_contributed_carrier() -> None:
    """The carriers seam end to end: the sample registers a carrier through its Manifest, and with
    `[sandbox] backend` naming it `serve` builds exactly that carrier — a deploy swaps the sandbox
    backend to an extension's without core naming it."""
    manifest = _sample_manifest()
    selected = select_carriers(_carrier_config(sample.CARRIER_NAME), (manifest,))
    assert isinstance(selected.carrier, sample.SampleCarrier)
    assert selected.spec.off_cluster is False


async def test_a_workspace_write_reaches_the_manifest_contributed_carrier() -> None:
    """The copy-in half of the carriers seam: core's `write_file` hands the bytes to the carrier's
    own `write` under the resolved workspace path, so the sample reads back exactly what core copied
    in — never through `exec`, whose command line is what a provider rejects once a payload is
    large. The size limit itself is the real carriers' proof; this is the dispatch."""
    manifest = _sample_manifest()
    carrier = select_carriers(_carrier_config(sample.CARRIER_NAME), (manifest,)).carrier
    assert isinstance(carrier, sample.SampleCarrier)
    handle = SandboxHandle(conversation_id=uuid4(), container_id="test")

    await SandboxSession(carrier=carrier, handle=handle).write_file("notes/report.txt", b"payload")

    assert carrier.written == {"/workspace/notes/report.txt": b"payload"}


def test_an_unregistered_backend_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="not a registered carrier"):
        select_carriers(_carrier_config("nope"), ())


def test_resume_backends_build_beside_the_default() -> None:
    """The coexistence seam: `[sandbox] resume_backends` keeps a prior backend live for the stored
    handles bearing its scheme, while new sandboxes open on `backend`."""
    manifest = _sample_manifest()
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="local", resume_backends=(sample.CARRIER_NAME,)),
    )
    selected = select_carriers(config, (manifest,))
    assert isinstance(selected.carrier, LocalCarrier)
    resumed, spec = selected.resume[sample.CARRIER_NAME]
    assert isinstance(resumed, sample.SampleCarrier)
    assert spec.name == sample.CARRIER_NAME


def test_a_resume_backend_no_carrier_registers_fails_loud() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="local", resume_backends=("nope",)),
    )
    with pytest.raises(RuntimeError, match="not a registered carrier"):
        select_carriers(config, ())


def test_a_resume_backend_duplicating_the_default_fails_loud() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="local", resume_backends=("local",)),
    )
    with pytest.raises(RuntimeError, match="already the default"):
        select_carriers(config, ())


def test_an_off_cluster_resume_backend_requires_the_public_proxy_url() -> None:
    remote = Manifest(
        name="remote",
        version="0",
        carriers=(CarrierSpec(name="remote", factory=sample.SampleCarrier, off_cluster=True),),
    )
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="local", resume_backends=("remote",)),
    )
    with pytest.raises(RuntimeError, match="proxy_public_url"):
        select_carriers(config, (remote,))


def test_a_carrier_colliding_with_a_built_in_fails_loud() -> None:
    collide = Manifest(
        name="collide",
        version="0",
        carriers=(CarrierSpec(name="local", factory=sample.SampleCarrier),),
    )
    with pytest.raises(RuntimeError, match="two carriers register backend"):
        select_carriers(_carrier_config("local"), (collide,))


def test_pack_prompt_section_reaches_the_rendered_system_prompt() -> None:
    """The contribution seam end to end: the section the sample declares renders into the shell's
    `{{sections}}` slot beside the agent's own prompt, and the render carries a digest — the same
    tuple the loop builds from `manifest.prompt_sections` and hands the engine each turn."""
    manifest = _sample_manifest()
    sections = tuple((section.name, section.body) for section in manifest.prompt_sections)
    rendered = render_system_prompt(
        "You are the workspace assistant.", sections, knowledge_cutoff="2026-01"
    )
    assert sample.SECTION_BODY in rendered.content
    assert "You are the workspace assistant." in rendered.content
    assert rendered.digest.startswith("sha256:")


async def test_sample_skill_parses_indexes_and_mounts_with_its_script() -> None:
    """The skills seam end to end through the probe: the skill the sample contributes parses into
    the registry the loader aggregates, renders into the `{{skill_index}}` the main prompt carries,
    and loads into `$UFO_HOME/skills/<name>/` with its bundled script — every step the real
    `load_skill` path runs, minus the live-sandbox execution deferred to the Docker proofs."""
    manifest = _sample_manifest()
    registry = skill_registry((manifest,))

    assert sample.SKILL_NAME in dict(registry.index())
    prompt = render_system_prompt(
        "You are the assistant.", (), skills=registry.index(), knowledge_cutoff="2026-01"
    )
    assert sample.SKILL_NAME in prompt.content

    written: dict[str, bytes] = {}

    class _Recorder:
        async def load_skills(self, payload: dict[str, object]) -> dict[str, str]:
            user = payload["user"]
            assert isinstance(user, dict)
            roots = {}
            for name, wire in user.items():
                assert isinstance(name, str) and isinstance(wire, dict)
                files = wire["files"]
                assert isinstance(files, dict)
                root = f"$UFO_HOME/skills/{name}"
                for path, content in files.items():
                    assert isinstance(path, str) and isinstance(content, str)
                    written[f"{root}/{path}"] = urlsafe_b64decode(content)
                roots[name] = root
            return roots

    for entry in await registry.materialize(registry.closure(sample.SKILL_NAME)):
        await install_skill(_Recorder(), entry.skill)

    root = f"$UFO_HOME/skills/{sample.SKILL_NAME}"
    assert f"name: {sample.SKILL_NAME}" in written[f"{root}/SKILL.md"].decode()
    assert sample.SKILL_SCRIPT_MARKER in written[f"{root}/{sample.SKILL_SCRIPT}"].decode()


def test_sample_subagent_profile_flows_through_the_loader_into_the_registry() -> None:
    manifest = _sample_manifest()
    registry = SubagentRegistry(turn_subagents((manifest,)))
    profile = registry.get(sample.SUBAGENT_NAME)
    assert profile.tool_names == (sample.TOOL_NAME,)
    assert "maxLength" not in profile.input_model.model_json_schema()["properties"]["task"]
    assert subagent_system_prompt(profile).endswith(FINISH_CONTRACT)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_subagent_tool_grant_and_default_widen_a_child_beyond_its_named_tools(
    db: None,
) -> None:
    """subagent_tool_grants + ToolDef.subagent_default end to end: the sample grants its note tool
    to its own profile and flags its connector tool a subagent default, so a child resolves both
    though its profile names neither — the seam that attaches a capability's tools to the profiles
    the research docs list, cross-extension, without the profile hard-coding a foreign name.
    Resolved here exactly as the turn loop does (own names plus grants, plus any subagent-default
    tool) over the real tool pool."""
    manifest = _sample_manifest()
    all_tools, _, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    profile = SubagentRegistry(turn_subagents((manifest,))).get(sample.SUBAGENT_NAME)
    grants = turn_subagent_grants((manifest,))
    assert grants[sample.SUBAGENT_NAME] == frozenset({sample.NOTE_TOOL_NAME})
    allowed = set(profile.tool_names) | grants.get(sample.SUBAGENT_NAME, frozenset())
    child = {tool.name for tool in all_tools if tool.name in allowed or tool.subagent_default}
    assert sample.TOOL_NAME in child  # its own named tool
    assert sample.NOTE_TOOL_NAME in child  # arrived via the grant
    assert sample.CONNECTOR_EXECUTE_TOOL_NAME in child  # arrived via subagent_default
    assert sample.NOTE_TOOL_NAME not in profile.tool_names
    assert sample.CONNECTOR_EXECUTE_TOOL_NAME not in profile.tool_names


async def test_sample_model_provider_is_selected_priced_and_streams(tmp_path: Path) -> None:
    """The models Manifest point end to end through the probe: the registry selects the sample's
    contributed backend for its model id, that client streams a real ModelEvent, and the registry
    prices the id against the contributed rate — core's direct providers still claim bare ids."""
    manifest = _sample_manifest()
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (manifest,))
    client = await registry.client_for(sample.SAMPLE_MODEL)
    assert isinstance(client.client, sample.SampleModelClient)
    request = ModelRequest(
        model=sample.SAMPLE_MODEL,
        system="",
        messages=(Message(role="user", content="hi"),),
        max_tokens=16,
        conversation_cache_ttl="5m",
    )
    events = [event async for event in client.complete(request)]
    assert TextDelta(text=sample.SAMPLE_MODEL_REPLY) in events
    priced = registry.pricing.micro_usd(
        sample.SAMPLE_MODEL, Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert priced == 6_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_tool_dispatches_with_its_scoped_context(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    tools, ext_by_tool, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    tool = next(tool for tool in tools if tool.name == sample.TOOL_NAME)
    context = ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"message": "conformance-echo"})
    with ws(workspace_id):
        result = await tool.handler(context, args)
        assert result.is_error is False
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.TOOL_KEY) == {
            "message": "conformance-echo",
        }


async def test_extension_owns_a_table_through_its_own_migration(db: None, tmp_path: Path) -> None:
    """The migration seam end to end: the sample's own migration created `sample_ext_note`, so its
    note tool writes and reads that table through the extension's workspace-scoped transaction. Two
    workspaces keep separate rows — the extension's table is scoped exactly as core's are, so an
    extension owns a real table and reaches only its own workspace's rows."""
    manifest = _sample_manifest()
    first, second = await _workspace(), await _workspace()
    tools, ext_by_tool, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    note = next(tool for tool in tools if tool.name == sample.NOTE_TOOL_NAME)

    with ws(first):
        write = await note.handler(
            _tool_context(first, ext_by_tool[note.name], tmp_path),
            note.input_model.model_validate({"text": "first note"}),
        )
        assert write.is_error is False
        assert write.content[0].text == "first note"

    _, other_ext, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    with ws(second):
        await note.handler(
            _tool_context(second, other_ext[note.name], tmp_path),
            note.input_model.model_validate({"text": "second note"}),
        )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sample.NOTE_TABLE.c.workspace_id, sample.NOTE_TABLE.c.note).order_by(
                    sample.NOTE_TABLE.c.note
                )
            )
        ).all()
    assert [(row.workspace_id, row.note) for row in rows] == [
        (first, "first note"),
        (second, "second note"),
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connector_execute_tool_resolves_the_bound_account_without_the_sandbox(
    db: None, tmp_path: Path
) -> None:
    """The server-side-execution seam: the sample's execute connector tool resolves the turn-agent's
    bound connected-account id through `connector_account` and records it, never touching the
    sandbox (its carrier raises on any reach). The test reads the account back through the store."""
    workspace_id = await _workspace()
    member_id, agent_id, conversation_id = await _grantable(workspace_id)
    grants = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await grants.record(
            provider=sample.CONNECTOR_PROVIDER,
            account_id=sample.CONNECTOR_ACCOUNT,
            host=sample.CONNECTOR_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    manifest = _sample_manifest()
    tools, ext_by_tool, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(member_id)
    )
    tool = next(tool for tool in tools if tool.name == sample.CONNECTOR_EXECUTE_TOOL_NAME)
    context = ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=conversation_id, container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        grants=grants,
        ext=ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"tool_name": "sample_list"})
    idempotency_key = f"{context.turn.id}/{sample.CONNECTOR_EXECUTE_TOOL_NAME}/c1"
    with ws(workspace_id), agent(agent_id):
        result = await tool.handler(replace(context, idempotency_key=idempotency_key), args)
        assert result.is_error is False
        assert result.content[0].text == sample.CONNECTOR_ACCOUNT
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.CONNECTOR_EXECUTE_KEY) == {
            "account": sample.CONNECTOR_ACCOUNT,
            "tool_name": "sample_list",
            "idempotency_key": idempotency_key,
        }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_undeclared_credential_slot_is_refused(db: None) -> None:
    manifest = _sample_manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(manifest.name, declared)
    assert sample.UNDECLARED_SLOT not in declared
    with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
        await context.credentials.get(sample.UNDECLARED_SLOT)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_only_an_off_turn_role_carries_the_probe_seam(db: None, tmp_path: Path) -> None:
    """A job's context carries the off-turn exec. A tool's and a turn hook's do not: those run
    inside a turn that already holds its sandbox, and a probe there would open a second,
    unattributed exec beside it. The absence is structural — those roles never pass the seam to
    `context_for` — so it reads as no capability at all rather than a disabled one.

    The capability a job does hold is one verb. No carrier, no handle, no session: a handler cannot
    widen a bounded command into arbitrary reach into the container."""
    manifest = _sample_manifest()
    probes = _probes(_sandboxes(tmp_path / "workspaces"))
    jobs_role = context_for(sample.NAME, frozenset(), probes=probes)

    assert jobs_role.probes is probes
    assert {name for name in dir(probes) if not name.startswith("_")} == {"run"}

    _, ext_by_tool, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    hooks = turn_hooks((manifest,), _credential_store(), audience=conversation_audience(None))
    bound = [hook.ext for event in hooks.hooks.values() for hook in event]
    assert bound
    for context in (ext_by_tool[sample.TOOL_NAME], *bound):
        assert context.probes is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_context_confines_the_credential_handle(db: None) -> None:
    """The credential handle a context carries exposes only its gated methods: no raw
    CredentialStore field to read an undeclared slot. The same confinement holds whether the context
    is built for a job/route (`context_for`) or a tool (`turn_tools`)."""
    manifest = _sample_manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    _, ext_by_tool, _ = turn_tools(
        (manifest,), _credential_store(), audience=conversation_audience(None)
    )
    for context in (
        context_for(manifest.name, declared),
        ext_by_tool[sample.TOOL_NAME],
    ):
        assert {name for name in dir(context.credentials) if not name.startswith("_")} == {
            "get",
            "rotate",
            "stored",
            "workspace_id",
            "declared",
        }
        with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
            await context.credentials.get(sample.UNDECLARED_SLOT)
        with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
            await context.credentials.rotate(sample.UNDECLARED_SLOT, "old", "new")
        with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
            await context.credentials.stored(sample.UNDECLARED_SLOT)


def test_a_route_without_a_credential_key_fails_loud() -> None:
    manifest = _sample_manifest()
    with pytest.raises(RuntimeError, match="serves routes but no credential key"):
        _mount_ext_routes(FastAPI(), (manifest,), None, None, None, None)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_job_fires_through_its_scoped_context(db: None) -> None:
    workspace_id = await _workspace()
    await _seed_note(workspace_id)
    manifest = _sample_manifest()
    runner = JobRunner(bindings=bindings_from((manifest,), ()), manifests=(manifest,))
    with ws(workspace_id):
        for workspace_id in await runner.candidates(f"{manifest.name}:{sample.JOB_NAME}"):
            await runner.fire(f"{manifest.name}:{sample.JOB_NAME}", workspace_id)
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.JOB_KEY) == {"ran": True}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_step_runs_through_its_scoped_context(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    await run_onboarding_steps((manifest,), workspace_id, _credential_store())
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.ONBOARDING_KEY) == {"onboarded": True}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_route_reaches_its_scoped_context(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    app = FastAPI()
    _mount_ext_routes(app, (manifest,), _credential_store(), None, None, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://serve") as client:
        response = await client.post(
            f"/ext/{sample.NAME}/{sample.ROUTE_PATH}",
            content="ping",
            headers=_bearer(workspace_id),
        )
        no_bearer = await client.post(f"/ext/{sample.NAME}/{sample.ROUTE_PATH}", content="ping")
        forged = await client.post(
            f"/ext/{sample.NAME}/{sample.ROUTE_PATH}",
            content="ping",
            headers=_forged_bearer(uuid4()),
        )
    assert response.status_code == 200
    assert response.text == "ping"
    assert no_bearer.status_code == 401
    assert forged.status_code == 401
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.ROUTE_KEY) == {"body": "ping", "home_url": None}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_route_is_handed_the_deploy_base_and_the_browser_home(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider's return leg lands on an extension route with no conversation behind it, so the
    page it answers with has nowhere of its own to send the member. Only core knows the deploy's
    public base and which surface a browser belongs on, and it hands the route's context both, as
    one link the extension does not have to assemble from core's mount paths."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    homed = replace(
        manifest,
        surfaces=(replace(manifest.surfaces[0], home=True), *manifest.surfaces[1:]),
    )
    app = FastAPI()
    _mount_ext_routes(app, (homed,), _credential_store(), None, None, "https://ufo.test/")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://serve") as client:
        response = await client.post(
            f"/ext/{sample.NAME}/{sample.ROUTE_PATH}",
            content="ping",
            headers=_bearer(workspace_id),
        )

    assert response.status_code == 200
    with ws(workspace_id):
        recorded = await ScopedStore(extension=sample.NAME).get(sample.ROUTE_KEY)
    assert recorded == {
        "body": "ping",
        "home_url": f"https://ufo.test/surface/{manifest.surfaces[0].name}",
    }


def test_a_tool_is_handed_the_deploy_base_and_the_browser_home() -> None:
    """A tool that hands a member a link into the portal — the billing screen a card return lands on
    — reads it off its own context, so no extension carries a surface name of its own. Both halves
    come from core: a tool holding only the base would have to guess the name."""
    manifest = _sample_manifest()
    _tools, ext_by_tool, _ = turn_tools(
        (manifest,),
        _credential_store(),
        audience=conversation_audience(None),
        public_base_url="https://ufo.test/",
        home_surface="portal",
    )
    context = ext_by_tool[sample.TOOL_NAME]
    assert context.home_url("#/workspace/billing") == (
        "https://ufo.test/surface/portal#/workspace/billing"
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_second_workspace_reaches_none_of_the_firsts_rows(db: None) -> None:
    first = await _workspace()
    await _seed_note(first)
    manifest = _sample_manifest()
    runner = JobRunner(bindings=bindings_from((manifest,), ()), manifests=(manifest,))
    with ws(first):
        for workspace_id in await runner.candidates(f"{manifest.name}:{sample.JOB_NAME}"):
            await runner.fire(f"{manifest.name}:{sample.JOB_NAME}", workspace_id)
    second = await _workspace()
    store = _credential_store()
    await store.put(first, sample.API_SLOT, "sk-first")

    with ws(second):
        assert await ScopedStore(extension=sample.NAME).get(sample.JOB_KEY) is None
    with pytest.raises(CredentialSlotUnset):
        await store.get(second, sample.API_SLOT)

    with ws(first):
        assert await ScopedStore(extension=sample.NAME).get(sample.JOB_KEY) == {"ran": True}


SEED_PROMPT = "You are helpful."


async def _seed_trajectory(
    workspace_id: UUID, blob: FilesystemBlobStore, *, corrupt: bool = False
) -> UUID:
    """A real agent, conversation, terminal turn, and durable transcript — the corpus the trajectory
    read enumerates and the target the governed proposal is pinned against. `corrupt` lands
    undecodable bytes at the transcript key instead, to prove one bad transcript is skipped."""
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=agent_id.hex,
                prompt=SEED_PROMPT,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=str(conversation_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="hi",
                terminal={"status": "done", "text": "hello", "model": "claude-opus-4-8"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    if corrupt:
        await blob.put(transcript_key(conversation_id), b"\x00 not a transcript")
    else:
        await Transcript(blob=blob, conversation_id=conversation_id).write(
            Conversation(
                seq=2,
                messages=(
                    Message(role="user", content="hi"),
                    Message(role="assistant", content="hello"),
                ),
            )
        )
    return agent_id


async def _conversation_of(agent_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.agent_id == agent_id
                )
            )
        ).scalar_one()


async def _sole_conversation() -> UUID:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.select(tables.conversation.c.id))).scalar_one()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_job_reads_trajectories_and_opens_a_governed_proposal(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_note(workspace_id)
    manifest = _sample_manifest()
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_root = tmp_path / "workspaces"
    agent_id = await _seed_trajectory(workspace_id, blob)
    runner = JobRunner(
        bindings=bindings_from((manifest,), ()),
        manifests=(manifest,),
        blob=blob,
        sandboxes=_sandboxes(workspace_root),
        probes=_probes(_sandboxes(workspace_root)),
    )
    with ws(workspace_id):
        for workspace_id in await runner.candidates(f"{manifest.name}:{sample.JOB_NAME}"):
            await runner.fire(f"{manifest.name}:{sample.JOB_NAME}", workspace_id)
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.TRAJECTORY_KEY) == {"count": 1}
        assert await scoped.get(sample.PROPOSAL_KEY) is not None
        conversation_id = await _sole_conversation()
        assert await scoped.get(sample.JOB_WORKSPACE_KEY) == {
            "path": f"/workspace/{sample.JOB_WORKSPACE_REL}"
        }
        assert await scoped.get(sample.JOB_PROBE_KEY) == {
            "stdout": sample.JOB_WORKSPACE_BODY,
            "exit_code": 0,
        }
        landed = workspace_root / str(conversation_id) / sample.JOB_WORKSPACE_REL
        assert landed.read_text() == sample.JOB_WORKSPACE_BODY

    async with workspace_tx() as connection:
        proposal = (
            await connection.execute(
                sa.select(
                    tables.proposal.c.extension,
                    tables.proposal.c.agent_id,
                    tables.proposal.c.status,
                    tables.proposal.c.from_digest,
                    tables.proposal.c.body,
                ).where(tables.proposal.c.workspace_id == workspace_id)
            )
        ).one()
        prompt = (
            await connection.execute(
                sa.select(tables.agent.c.prompt).where(tables.agent.c.id == agent_id)
            )
        ).scalar_one()
    assert proposal.extension == sample.NAME
    assert proposal.agent_id == agent_id
    assert proposal.status == "pending"
    assert proposal.from_digest == prompt_digest(SEED_PROMPT)
    assert proposal.body == {"prompt": SEED_PROMPT + sample.PROPOSAL_SUFFIX}
    assert prompt == SEED_PROMPT


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_job_context_confines_blob_to_a_workspace_scoped_trajectory_read(
    db: None, tmp_path: Path
) -> None:
    """The context a job receives carries no raw blob handle — only a workspace-scoped, read-only
    TrajectoryCorpus. It cannot get or put an arbitrary key (another workspace's transcript, an
    artifact), and its trajectory read returns only this workspace's conversations."""
    first = await _workspace()
    second = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    first_agent = await _seed_trajectory(first, blob)
    await _seed_trajectory(second, blob)
    await blob.put("artifacts/leak/report.txt", b"private")

    context = context_for(sample.NAME, frozenset(), blob=blob)

    assert "blob" not in {field.name for field in fields(context)}
    assert isinstance(context.corpus, TrajectoryCorpus)
    assert {name for name in dir(context.corpus) if not name.startswith("_")} == {
        "conversations",
        "trajectories",
        "workspace_id",
        "limit",
    }

    with ws(first):
        trajectories = await context.trajectories()
    assert len(trajectories) == 1
    assert trajectories[0].agent_id == first_agent


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_corpus_reads_only_the_most_recent_conversations(
    db: None, tmp_path: Path
) -> None:
    """The corpus is bounded to the `limit` most recently created conversations — a workspace with
    a long history hands a job a bounded read, so older transcripts are never fetched."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_trajectory(workspace_id, blob)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
            .where(tables.conversation.c.workspace_id == workspace_id)
        )
    recent_agent = await _seed_trajectory(workspace_id, blob)
    corpus = TrajectoryCorpus(blob, limit=1)
    with ws(workspace_id):
        trajectories = await corpus.trajectories()
    assert [trajectory.agent_id for trajectory in trajectories] == [recent_agent]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_corpus_reads_the_conversations_a_handler_names_whatever_the_bound(
    db: None, tmp_path: Path
) -> None:
    """A job that already knows which conversations it works on reads exactly those: a
    conversation older than the `limit` most recent still answers, and no transcript around it is
    decoded to reach it. The workspace stays the boundary — an id another workspace holds answers
    nothing."""
    workspace_id, other = await _workspace(), await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    older_agent = await _seed_trajectory(workspace_id, blob)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
            .where(tables.conversation.c.workspace_id == workspace_id)
        )
    await _seed_trajectory(workspace_id, blob)
    elsewhere_agent = await _seed_trajectory(other, blob)

    corpus = TrajectoryCorpus(blob, limit=1)
    with ws(workspace_id):
        older = await _conversation_of(older_agent)
        elsewhere = await _conversation_of(elsewhere_agent)
        assert [trajectory.agent_id for trajectory in await corpus.conversations((older,))] == [
            older_agent
        ]
        assert await corpus.conversations((elsewhere,)) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_corrupt_transcript_is_skipped_not_aborting_the_corpus(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    good_agent = await _seed_trajectory(workspace_id, blob)
    await _seed_trajectory(workspace_id, blob, corrupt=True)

    context = context_for(sample.NAME, frozenset(), blob=blob)
    with ws(workspace_id):
        trajectories = await context.trajectories()
    assert len(trajectories) == 1
    assert trajectories[0].agent_id == good_agent


@dataclass
class _StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _surface_workspace() -> tuple[UUID, UUID, str]:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    email = "member@x.test"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, email


@dataclass(frozen=True)
class _NamedModel:
    """A surface model that answers its own id and nothing else — the seam under test is which
    model a route is handed, never what it returns."""

    model: str

    async def turn(self, request: ModelRequest) -> Message:
        raise AssertionError("this probe calls no model")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_surface_route_is_handed_the_model_the_deploy_wired(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A surface route may generate what it answers, and what it generates on is wired per surface
    at boot — one metered seam labelled by the surface's own name. The sample reads it back through
    a real route, so the wiring is proved by a consumer rather than by core asserting against
    itself, and a deploy that wires none still answers the page."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, _member_id, _email = await _surface_workspace()
    manifest = _sample_manifest()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (manifest,),
        _credential_store(),
        WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        _sandboxes(tmp_path / "workspaces"),
        InProcessHub(),
        _StubDbos(),
        "",
        None,
        None,
        ("auto",),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
        surface_model=lambda name: _NamedModel(f"model-for-{name}"),
    )
    bare = FastAPI()
    _mount_shared_surfaces(
        bare,
        (manifest,),
        _credential_store(),
        WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        _sandboxes(tmp_path / "workspaces-bare"),
        InProcessHub(),
        _StubDbos(),
        "",
        None,
        None,
        ("auto",),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    path = f"/surface/{sample.SURFACE_NAME}/{sample.SURFACE_MODEL_PATH}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        wired = await client.get(path, headers=_bearer(workspace_id))
    async with AsyncClient(transport=ASGITransport(app=bare), base_url="http://surface") as client:
        unwired = await client.get(path, headers=_bearer(workspace_id))

    assert wired.status_code == 200
    assert wired.json() == {"model": f"model-for-{sample.SURFACE_NAME}"}
    assert unwired.status_code == 200
    assert unwired.json() == {"model": None}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sample_surface_admits_links_streams_and_delivers(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The surface seam end to end through the probe: an inbound event admits a turn, links a
    surface identity, and streams an inbound file into the workspace; then the writeback poller
    delivers the terminal turn and streams a shared file back out — every step read through the
    durable rows and blobs core wrote, never a mock. A redelivery of the same event proves the
    other half of what admission hands back: the same turn, and `opened_run` false, so a surface
    can start per-run work on the one delivery that opened the run."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, member_id, email = await _surface_workspace()
    manifest = _sample_manifest()
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    workspace_root = tmp_path / "workspaces"
    dbos = _StubDbos()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (manifest,),
        _credential_store(),
        blob,
        _sandboxes(workspace_root),
        InProcessHub(),
        dbos,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    body = json.dumps(
        {"external_id": "ext-1", "email": email, "message": "hello", "inbound_text": "note!"}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        no_bearer = await client.post(f"/surface/{sample.SURFACE_NAME}", content=body)
        forged = await client.post(
            f"/surface/{sample.SURFACE_NAME}", content=body, headers=_forged_bearer(uuid4())
        )
        response = await client.post(
            f"/surface/{sample.SURFACE_NAME}", content=body, headers=_bearer(workspace_id)
        )
        redelivered = await client.post(
            f"/surface/{sample.SURFACE_NAME}", content=body, headers=_bearer(workspace_id)
        )
    assert no_bearer.status_code == 401
    assert forged.status_code == 401
    assert response.status_code == 200
    turn_id = UUID(response.json()["turn_id"])
    conversation_id = UUID(response.json()["conversation_id"])
    assert response.json()["opened_run"] is True
    assert redelivered.json() == {**response.json(), "opened_run": False}

    assert dbos.enqueued == [str(turn_id), str(turn_id)]
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.inbound).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == sample.SURFACE_NAME,
                    tables.surface_identity.c.external_id == "ext-1",
                )
            )
        ).one()
        writeback_status = (
            await connection.execute(
                sa.select(tables.writeback.c.status).where(tables.writeback.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert turn.status == "queued"
    assert "note!" in turn.inbound or turn.inbound == "hello"
    assert linked.member_id == member_id
    assert writeback_status == WRITEBACK_PENDING
    inbound = workspace_root / str(conversation_id) / sample.SURFACE_INBOX_REL
    assert inbound.read_bytes() == b"note!"

    with ws(workspace_id):
        await blob.put("artifacts/z/out.txt", b"shared-bytes")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(status="done", terminal={"status": "done", "text": "done!"})
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key="artifacts/z/out.txt",
                workspace_id=workspace_id,
                filename="out.txt",
                subject=None,
                media_type="text/plain",
                size_bytes=12,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await app.state.writeback_poller.drain()
    async with workspace_tx() as connection:
        delivered = (
            await connection.execute(
                sa.select(tables.writeback.c.status, tables.writeback.c.reply_ref).where(
                    tables.writeback.c.turn_id == turn_id
                )
            )
        ).one()
    assert delivered.status == WRITEBACK_DELIVERED
    assert delivered.reply_ref == sample.SURFACE_POST_REF
    with ws(workspace_id):
        round_tripped = await blob.get(f"{sample.SURFACE_DELIVERED_PREFIX}/{turn_id}/out.txt")
    assert round_tripped == b"shared-bytes"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sample_surface_live_admit_tails_and_stays_off_writeback(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam's LIVE mode end to end through the same probe surface: the live route adopts a
    member from a peer surface's identity, admits WITHOUT writeback, and reads back turn owner and
    spend;
    the contrast against the durable ingest is that NO writeback row is written — the poller never
    sees this turn. Then the tail route drives `ctx.tail` and streams the turn's terminal frame off
    the hub, exercising the live delivery path a live surface uses instead of the poller."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, member_id, _email = await _surface_workspace()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface=sample.SURFACE_PEER,
                external_id="ext-live-1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    manifest = _sample_manifest()
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    dbos = _StubDbos()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (manifest,),
        _credential_store(),
        blob,
        _sandboxes(tmp_path / "workspaces"),
        InProcessHub(),
        dbos,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    body = json.dumps({"external_id": "ext-live-1", "message": "hello"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        admitted = await client.post(
            f"/surface/{sample.SURFACE_LIVE_NAME}/{sample.SURFACE_LIVE_PATH}",
            content=body,
            headers=_bearer(workspace_id),
        )
        assert admitted.status_code == 200
        result = admitted.json()
        turn_id = UUID(result["turn_id"])

        assert dbos.enqueued == [str(turn_id)]
        assert result["owner"] == str(member_id)
        assert result["spend_total_micro_usd"] == 0
        async with workspace_tx() as connection:
            turn = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).one()
            adopted = (
                await connection.execute(
                    sa.select(tables.surface_identity.c.member_id).where(
                        tables.surface_identity.c.surface == sample.SURFACE_LIVE_NAME,
                        tables.surface_identity.c.external_id == "ext-live-1",
                    )
                )
            ).one()
            writeback = (
                await connection.execute(
                    sa.select(tables.writeback.c.turn_id).where(
                        tables.writeback.c.turn_id == turn_id
                    )
                )
            ).one_or_none()
        assert turn.status == "queued"
        assert adopted.member_id == member_id
        assert writeback is None

        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == turn_id)
                .values(status="done", terminal={"status": "done", "text": "live done!"})
            )
        frames = []
        async with asyncio.timeout(30):
            async with client.stream(
                "GET",
                f"/surface/{sample.SURFACE_LIVE_NAME}/live/{turn_id}/stream",
                headers=_bearer(workspace_id),
            ) as stream:
                assert stream.status_code == 200
                async for line in stream.aiter_lines():
                    if line.strip():
                        frames.append(json.loads(line))
    assert any(frame.get("frame", {}).get("status") == "done" for frame in frames)


def _vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class _StubEmbed:
    """Deterministic stand-in EmbedClient the page indexer embeds through and search queries
    through; the test asserts the recalled SourceMatch, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


async def test_sample_source_syncs_a_page_recallable_through_memory(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The source seam end to end through the sample: onboarding registers a source row via the SDK,
    the core sync driver drives the sample's registered SourceBackend and lands one page (embedding
    deferred), the page index job derives its chunk, and the page is recalled through
    `search_sources` — proving register_source + the `sources` Manifest point + the runner + memory,
    all through public surfaces."""
    workspace_id = await _workspace()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="main",
                prompt="p",
                model="m",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    manifest = _sample_manifest()
    await run_onboarding_steps((manifest,), workspace_id, _credential_store())

    embed = _StubEmbed(_vec((3, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    blob = FilesystemBlobStore(root=tmp_path)
    memory_context = context_for("memory", frozenset())
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(
        backends=_source_backends((manifest,)),
        blob=blob,
        postgres=postgres,
    )
    page_feed = CorePageFeed(blob=blob)
    page_indexer = PageIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=memory_context.page_states,
    )
    service = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=memory_context.page_states,
        readable_page_states=memory_context.readable_page_states,
        readable_source_ids=memory_context.readable_source_ids,
    )

    with ws(workspace_id):
        await driver.run()
    async with workspace_tx() as connection:
        page = (
            await connection.execute(
                sa.select(tables.page.c.subject).where(tables.page.c.workspace_id == workspace_id)
            )
        ).one()
        chunks = (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()
    assert page.subject == SHARED_SUBJECT
    assert chunks == 0

    with ws(workspace_id):
        await page_indexer.apply((await page_feed.pages_changed_since(None, 50)).changes)
    with ws(workspace_id):
        matches = await service.search_sources(
            "migrating orbital widget fleet",
            frozenset({SHARED_SUBJECT}),
            5,
            source_reader=SourceReader(
                agent_id=agent_id,
                requesting_member_id=None,
                subjects=frozenset({SHARED_SUBJECT}),
            ),
        )
    assert matches and "orbital widget" in matches[0].text


async def test_core_selects_a_manifest_index_backend_by_name(db: None, database_url: str) -> None:
    """The `indexes` seam end to end through the probe: with `memory.index_backend` naming the
    sample's backend, core builds the manifest-contributed IndexBackend (not the base-pinned
    default) and it is driven through the protocol — upsert then retrieve. Unset resolves the
    base-pinned `index_default` extension's `"default"` backend; an unknown name and a missing
    credential key each fail loud."""
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    store = _credential_store()

    selected = index_backend((manifest,), sample.INDEX_BACKEND, store)
    assert isinstance(selected, sample.SampleIndex)
    with ws(workspace_id):
        await selected.upsert(
            (
                Chunk(
                    "d1", OWNER_KIND_MEMORY_ITEM, "m1", SHARED_SUBJECT, 0, "orbital", _vec((0, 1.0))
                ),
            )
        )
        hits = await selected.lexical(
            "orbital", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 5
        )
    assert [hit.chunk_digest for hit in hits] == ["d1"]

    default = index_backend((manifest, index_default.manifest()), None, store)
    assert isinstance(default, DefaultIndex)

    with pytest.raises(RuntimeError, match="no extension registers"):
        index_backend((manifest,), "nonesuch", store)
    with pytest.raises(RuntimeError, match="needs a credential key"):
        index_backend((manifest,), sample.INDEX_BACKEND, None)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_core_selects_a_manifest_embed_backend_by_name(db: None, database_url: str) -> None:
    """The `embeds` seam end to end through the probe: with `memory.embed_backend` naming the
    sample's backend, core builds the manifest-contributed EmbedClient and it is driven through the
    protocol. Unset resolves the base-pinned `embed_openai` extension's `"default"` backend; an
    unknown name fails loud."""
    manifest = _sample_manifest()
    store = _credential_store()

    selected = embed_backend((manifest,), sample.EMBED_BACKEND, store)
    assert isinstance(selected, sample.SampleEmbed)
    vectors = await selected.embed(("one", "two"))
    assert vectors == (sample.SAMPLE_EMBED_VECTOR, sample.SAMPLE_EMBED_VECTOR)

    with pytest.raises(RuntimeError, match="no extension registers"):
        embed_backend((manifest,), "nonesuch", store)


def test_sample_pack_activates_its_bundled_extension_and_own_contributions() -> None:
    """The packs seam end to end through the probe: the sample pack is discovered through its
    `ufo.pack` entry point, and activating it by name narrows the active set to exactly the
    sample extension's real manifest (its tools present) plus a manifest carrying the pack's own
    pack-level skill and onboarding step — the shape `load_manifests` returns for a serve or init
    that names the pack."""
    packs = discovered_packs()
    assert sample_pack.NAME in packs
    assert packs[sample_pack.NAME].extensions == (sample_pack.BUNDLED_EXTENSION,)

    manifests = load_manifests(sample_pack.NAME)
    assert [manifest.name for manifest in manifests] == [sample.NAME, sample_pack.NAME]

    bundled = next(manifest for manifest in manifests if manifest.name == sample.NAME)
    assert {tool.name for tool in bundled.tools} == {
        sample.TOOL_NAME,
        sample.NOTE_TOOL_NAME,
        sample.AUDIT_ACTION,
        sample.POLISH_ACTION,
        sample.ENGRAVE_ACTION,
        sample.DIVINE_ACTION,
        sample.CALIBRATE_ACTION,
        sample.BLESS_ACTION,
        sample.BESEECH_ACTION,
    }

    pack_manifest = next(manifest for manifest in manifests if manifest.name == sample_pack.NAME)
    assert {spec.path.name for spec in pack_manifest.skills} == {sample_pack.SKILL_NAME}
    assert {step.name for step in pack_manifest.onboarding_steps} == {sample_pack.ONBOARDING_NAME}


def test_sample_pack_skill_reaches_the_skill_registry() -> None:
    """The pack-level `skills` contribution end to end: the skill the sample pack ships folds into
    the same registry the loader aggregates an extension's into, and renders in the loadable-skill
    index beside core's own — a pack contributes a skill through the identical path an extension
    does."""
    registry = skill_registry(load_manifests(sample_pack.NAME))
    assert sample_pack.SKILL_NAME in dict(registry.index())


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sample_pack_onboarding_step_runs_through_its_scoped_context(db: None) -> None:
    """The pack-level `onboarding` contribution end to end: activating the pack fires its
    onboarding step with a context scoped to the pack's name, which records through its scoped store
    — read back here through the same public surface, proving the step ran scoped to the pack."""
    workspace_id = await _workspace()
    await run_onboarding_steps(load_manifests(sample_pack.NAME), workspace_id, _credential_store())
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample_pack.NAME)
        assert await scoped.get(sample_pack.ONBOARDING_KEY) == {"pack_onboarded": True}


@pytest.mark.parametrize("held", [True, False])
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_core_reads_each_declared_workspace_fact_through_its_own_context(
    db: None, held: bool
) -> None:
    """The seam, proved through the sample rather than a fake: the sample's fact reads its own
    scoped store, so a line in the answer is core having called that read with this extension's
    context bound to this workspace. The state is written through the same public store an
    extension writes, and read back through `turn_workspace_facts` — no mock call log.

    The false arm is half the point: a workspace that holds nothing contributes no line, so a fresh
    workspace's prompt carries no capability it does not have."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        if held:
            await ScopedStore(extension=sample.NAME).put(sample.WORKSPACE_FACT_KEY, True)
        lines = await turn_workspace_facts((_sample_manifest(),), audience=SHARED_AUDIENCE)
    assert lines == ((sample.WORKSPACE_FACT_LINE,) if held else ())
