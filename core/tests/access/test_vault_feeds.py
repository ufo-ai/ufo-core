from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_composio.manifest import manifest as composio_manifest
from ufo_ext_gbrain.git import GITHUB_TOKEN_SLOT
from ufo_ext_gbrain.manifest import manifest as gbrain_manifest
from ufo_ext_pipedream.manifest import manifest as pipedream_manifest
from ufo_ext_sources.feeds import FEED_SLOTS, FEEDS
from ufo_ext_sources.manifest import manifest as sources_manifest
from ufo_ext_sources.registry import CONNECTORS, direct_slots

from core.tests.knowledge.model_read_text import PIN, collect, render
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import connector_clis, self_user_id_resolvers, workspace_slot_source
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, UFO_MODELS_SECRET
from ufo.runtime.access.grants import TENANT_URL_RULES
from ufo.runtime.access.vault import SecretDescription, SecretUnbound, VaultReads
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import CredentialSlot, Manifest, open_connector_namespace
from ufo.runtime.ext.surface import SurfaceIdentityContext, SurfaceSpec
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.serve import _connector_entries

pytestmark = pytest.mark.usefixtures("db")

GITHUB_HOST = "api.github.com"
CODELOAD_HOST = "codeload.github.com"
OTHER_HOST = "api.other.test"
ZENDESK_URL = "https://acme.zendesk.com"
SLACK_BOT = "UBOTSELF01"
INTERNAL_SLOT = CredentialSlot(name="acme_internal_key", description="A key read in-process.")
INTERNAL = Manifest(name="acme", version="0", credentials=(INTERNAL_SLOT,))


async def _bot_speaker(ctx: SurfaceIdentityContext) -> str | None:
    return SLACK_BOT


SLACK_SURFACE = Manifest(
    name="slack_surface",
    version="0",
    surfaces=(SurfaceSpec(name="slack", self_user_id=_bot_speaker),),
)
MANIFESTS = (
    sources_manifest(),
    gbrain_manifest(),
    pipedream_manifest(),
    composio_manifest(),
    INTERNAL,
    SLACK_SURFACE,
)


@dataclass(frozen=True)
class _AccountTokens:
    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        return f"token-{account_id}"


def _vault(store: CredentialStore, blob_root: Path) -> VaultReads:
    return VaultReads(
        store,
        workspace_slot_source(MANIFESTS),
        {
            provider: replace(cli, secret=_AccountTokens())
            for provider, cli in connector_clis(MANIFESTS).items()
        },
        {},
        {slot.name: slot for manifest in MANIFESTS for slot in manifest.credentials},
        ConnectorRegistry(
            entries=_connector_entries(MANIFESTS), resolver=open_connector_namespace(MANIFESTS)
        ),
        self_user_id_resolvers(
            MANIFESTS, store, WorkspaceBlobStore(backend=FilesystemBlobStore(blob_root))
        ),
    )


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member(workspace_id: UUID) -> UUID:
    member_id = uuid4()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=f"{member_id.hex}@x.test",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return member_id


async def _connection(
    workspace_id: UUID,
    provider: str,
    account_id: str,
    *,
    host: str = "",
    base_url: str | None = None,
    owner_member_id: UUID | None = None,
) -> str:
    connection_id = uuid4()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=connection_id,
                    workspace_id=workspace_id,
                    provider=provider,
                    account_id=account_id,
                    host=host,
                    base_url=base_url,
                    owner_member_id=owner_member_id,
                    shared=owner_member_id is None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return f"{CONNECTION_SECRET_PREFIX}{connection_id}"


async def _fill(store: CredentialStore, workspace_id: UUID, values: dict[str, str]) -> None:
    with ws(workspace_id):
        for slot, value in values.items():
            await store.put(workspace_id, slot, value)


async def _released(vault: VaultReads, workspace_id: UUID, name: str, host: str) -> bool:
    try:
        await vault.resolve(workspace_id, name, host)
    except SecretUnbound:
        return False
    return True


async def test_a_feed_slot_resolves_on_its_providers_hosts_alone(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    await _fill(store, workspace_id, {"github": "gh-feed-key", "googledocs": "docs-feed-key"})

    assert (await vault.resolve(workspace_id, "github", GITHUB_HOST)).value == "gh-feed-key"
    for host in ("www.googleapis.com", "docs.googleapis.com"):
        assert (await vault.resolve(workspace_id, "googledocs", host)).value == "docs-feed-key"
    for name, host in (
        ("github", CODELOAD_HOST),
        ("github", "github.com"),
        ("googledocs", "sheets.googleapis.com"),
        ("googledocs", GITHUB_HOST),
        ("github", OTHER_HOST),
    ):
        assert not await _released(vault, workspace_id, name, host)


async def test_a_tenant_feed_resolves_on_its_connections_admitted_host_alone(
    tmp_path: Path,
) -> None:
    store, workspace_id, unconnected = _store(), await _workspace(), await _workspace()
    vault = _vault(store, tmp_path)
    for workspace in (workspace_id, unconnected):
        await _fill(store, workspace, {"zendesk": "zd-key", "freshdesk": "fd-key"})
    await _connection(workspace_id, "zendesk", "", base_url=ZENDESK_URL)
    await _connection(workspace_id, "zendesk", "ca_zd", base_url="https://broker.zendesk.com")
    await _connection(workspace_id, "freshdesk", "", base_url="https://keys.exfiltrate.test")

    assert (await vault.resolve(workspace_id, "zendesk", "acme.zendesk.com")).value == "zd-key"
    for workspace, name, host in (
        (workspace_id, "zendesk", "broker.zendesk.com"),
        (workspace_id, "zendesk", "other.zendesk.com"),
        (workspace_id, "freshdesk", "keys.exfiltrate.test"),
        (unconnected, "zendesk", "acme.zendesk.com"),
    ):
        assert not await _released(vault, workspace, name, host)


async def test_a_connection_keeps_its_hosts(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    github = await _connection(workspace_id, "github", "apn_gh", host=GITHUB_HOST)
    cli = connector_clis(MANIFESTS)["github"]
    git_host = cli.git.host if cli.git is not None else ""

    released = {
        host: await _released(vault, workspace_id, github, host)
        for host in (GITHUB_HOST, git_host, CODELOAD_HOST, OTHER_HOST)
    }

    assert released == {GITHUB_HOST: True, git_host: True, CODELOAD_HOST: False, OTHER_HOST: False}


async def test_gbrains_token_resolves_on_the_github_hosts(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    await _fill(store, workspace_id, {GITHUB_TOKEN_SLOT: "gbrain-token"})

    for host in (GITHUB_HOST, CODELOAD_HOST):
        assert (await vault.resolve(workspace_id, GITHUB_TOKEN_SLOT, host)).value == "gbrain-token"
    assert not await _released(vault, workspace_id, GITHUB_TOKEN_SLOT, "github.com")


async def test_describe_answers_a_brokered_connection(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    owner = await _member(workspace_id)
    notion = await _connection(workspace_id, "notion", "ca_9x2", owner_member_id=owner)
    github = await _connection(workspace_id, "github", "apn_gh", host=GITHUB_HOST)
    zendesk = await _connection(workspace_id, "zendesk", "", base_url=ZENDESK_URL)

    assert await vault.describe(workspace_id, notion) == SecretDescription(
        name=notion,
        kind="oauth",
        provider="notion",
        usable=True,
        released=False,
        broker="composio",
        account="ca_9x2",
        base_url=None,
        shared=False,
        owner_member_id=owner,
        self_user_id=None,
    )
    assert await vault.describe(workspace_id, github) == SecretDescription(
        name=github,
        kind="oauth",
        provider="github",
        usable=True,
        released=True,
        broker="pipedream",
        account="apn_gh",
        base_url=None,
        shared=True,
        owner_member_id=None,
        self_user_id=None,
    )
    assert await vault.describe(workspace_id, zendesk) == SecretDescription(
        name=zendesk,
        kind="static",
        provider="zendesk",
        usable=True,
        released=False,
        broker=None,
        account=None,
        base_url=ZENDESK_URL,
        shared=True,
        owner_member_id=None,
        self_user_id=None,
    )


async def test_describe_answers_a_keyed_slot(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    await _fill(store, workspace_id, {"github": "gh-feed-key", INTERNAL_SLOT.name: "internal"})

    described = {
        name: await vault.describe(workspace_id, name)
        for name in ("github", "linear", INTERNAL_SLOT.name)
    }

    blank = SecretDescription(
        name="",
        kind="static",
        provider=None,
        usable=False,
        released=False,
        broker=None,
        account=None,
        base_url=None,
        shared=True,
        owner_member_id=None,
        self_user_id=None,
    )
    assert described == {
        "github": replace(blank, name="github", provider="github", usable=True, released=True),
        "linear": replace(blank, name="linear", provider="linear"),
        INTERNAL_SLOT.name: replace(blank, name=INTERNAL_SLOT.name, usable=True),
    }


type _Case = Callable[[CredentialStore, UUID], Awaitable[tuple[str, str]]]


async def _filled_feed_slot(store: CredentialStore, workspace_id: UUID) -> tuple[str, str]:
    await _fill(store, workspace_id, {"linear": "lin-key"})
    return "linear", "api.linear.app"


async def _empty_feed_slot(store: CredentialStore, workspace_id: UUID) -> tuple[str, str]:
    return "linear", "api.linear.app"


async def _broker_connection(store: CredentialStore, workspace_id: UUID) -> tuple[str, str]:
    return await _connection(workspace_id, "notion", "ca_notion"), "api.notion.com"


async def _cli_connection(store: CredentialStore, workspace_id: UUID) -> tuple[str, str]:
    return await _connection(workspace_id, "github", "apn_cli", host=GITHUB_HOST), GITHUB_HOST


@pytest.mark.parametrize(
    "case", [_filled_feed_slot, _empty_feed_slot, _broker_connection, _cli_connection]
)
async def test_released_is_true_exactly_when_resolve_answers(case: _Case, tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    name, host = await case(store, workspace_id)

    described = await vault.describe(workspace_id, name)

    assert described.released == await _released(vault, workspace_id, name, host)


async def test_describe_answers_slacks_self_user_id(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    slack = await _connection(workspace_id, "slack", "ca_slack_member")

    described = await vault.describe(workspace_id, slack)

    assert (described.account, described.self_user_id) == ("ca_slack_member", SLACK_BOT)


async def test_models_unknown_and_foreign_names_are_unbound(tmp_path: Path) -> None:
    store, workspace_id, other = _store(), await _workspace(), await _workspace()
    vault = _vault(store, tmp_path)
    foreign = await _connection(other, "notion", "ca_foreign")

    for name in (
        UFO_MODELS_SECRET,
        "acme_unknown_key",
        foreign,
        f"{CONNECTION_SECRET_PREFIX}not-a-uuid",
        f"{CONNECTION_SECRET_PREFIX}{uuid4()}",
    ):
        with pytest.raises(SecretUnbound):
            await vault.describe(workspace_id, name)


async def test_describe_needs_vault_read(tmp_path: Path) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, tmp_path)
    await _fill(store, workspace_id, {"github": "gh-feed-key"})

    with ws(workspace_id):
        described = await context_for(
            "core", frozenset(), vault_read=True, vault=vault
        ).describe_secret("github")
        with pytest.raises(PermissionError, match="cannot describe secrets"):
            await context_for("core", frozenset(), vault=vault).describe_secret("github")
        with pytest.raises(RuntimeError, match="vault; none is wired"):
            await context_for("core", frozenset(), vault_read=True).describe_secret("github")

    assert (described.name, described.usable, described.released) == ("github", True, True)


def test_the_sources_manifests_slots_are_the_feeds() -> None:
    feed_slots = tuple(slot for feed in FEEDS.values() for slot in feed.slots)

    assert sources_manifest().credentials == feed_slots
    assert FEED_SLOTS == frozenset(slot.name for slot in feed_slots)
    assert render(collect()) == PIN.read_text()


def test_the_feeds_are_todays_registry() -> None:
    assert FEEDS.keys() == CONNECTORS.keys()
    for provider, feed in FEEDS.items():
        connector = CONNECTORS[provider]
        base_host = urlsplit(connector.base_url).hostname
        assert feed.provider == provider
        assert tuple(slot.name for slot in feed.slots) == direct_slots(connector)
        assert feed.tenant == (provider in TENANT_URL_RULES)
        assert (feed.hosts[:1] == (base_host,)) == (base_host is not None)
        assert len(feed.headers) == (len(connector.key_headers) if len(feed.slots) > 1 else 0)
        for slot in feed.slots:
            assert slot.injection is None
            assert slot.feed is not None
            assert (slot.feed.provider, slot.feed.hosts, slot.feed.tenant) == (
                provider,
                feed.hosts,
                feed.tenant,
            )
