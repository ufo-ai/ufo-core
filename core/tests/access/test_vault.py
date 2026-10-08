import logging
import pickle
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from ufo.config import BlobConfig, Config, DatabaseConfig, ModelsConfig
from ufo.db import workspace_tx
from ufo.host.ext.loader import workspace_slot_source
from ufo.proxy_serve import MODEL_KEY_ENVS
from ufo.runtime.access.connectors import CliCredential, ConnectorRegistry, GitWire
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, UFO_MODELS_SECRET
from ufo.runtime.access.vault import SecretUnbound, SecretValue, VaultReads
from ufo.runtime.billing.accounting import UNGATED_LEDGER
from ufo.runtime.billing.spend import NO_SPEND_GATES
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import (
    CredentialSlot,
    InjectionTarget,
    Manifest,
    RouteSpec,
    WorkspaceCredentials,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.serve import _mount_ext_routes

ACME_HOST = "api.acmekeys.com"
OTHER_HOST = "api.other.test"
US_HOST = "api.us.acme.test"
EU_HOST = "api.eu.acme.test"
DECLARED_HOST = "api.declared.test"
GITHUB = "github"
GITHUB_HOST = "api.github.com"
GIT = GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential")
ACME_SLOT = CredentialSlot(
    name="acme_api_key",
    description="Acme's API key.",
    injection=InjectionTarget(
        host=ACME_HOST, header="x-api-key", sentinel="UFO_SENTINEL_ACME_API_KEY", env="ACME_KEY"
    ),
)
SITE_SLOT = CredentialSlot(name="acme_site", description="The Acme site the account lives on.")
REGIONAL_SLOT = CredentialSlot(
    name="acme_regional_key",
    description="Acme's key for the workspace's own site.",
    injection=InjectionTarget(
        host=HostChoice(
            slot=SITE_SLOT.name,
            description="The Acme site the account lives on.",
            hosts=(US_HOST, EU_HOST),
            default=US_HOST,
        ),
        header="x-api-key",
        sentinel="UFO_SENTINEL_ACME_REGIONAL_KEY",
        env="ACME_REGIONAL_KEY",
    ),
)
SYSTEM_TOKEN_SLOT = CredentialSlot(
    name="acme_system_token", description="A token the extension mints for itself."
)
DECLARED_SLOT = CredentialSlot(
    name="acme_declared_key",
    description="A key the workspace declared for itself.",
    injection=InjectionTarget(
        host=DECLARED_HOST,
        header="authorization",
        sentinel="UFO_SENTINEL_ACME_DECLARED_KEY",
        env="ACME_DECLARED_KEY",
    ),
)
ACME_SECRET = "acme-real-secret"
REGIONAL_SECRET = "acme-regional-secret"
SYSTEM_TOKEN = "acme-system-token"
DECLARED_SECRET = "acme-declared-secret"
CONFIG = Config(
    database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
    blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
    models=ModelsConfig(openai_api_key_env="ACME_OPENAI_KEY"),
)
WORKSPACE_HEADER = "x-test-workspace"


async def _declared(ctx: ExtensionContext, workspace_id: UUID) -> tuple[CredentialSlot, ...]:
    return (DECLARED_SLOT,)


MANIFEST = Manifest(
    name="acme",
    version="0",
    credentials=(ACME_SLOT, SITE_SLOT, REGIONAL_SLOT, SYSTEM_TOKEN_SLOT),
    workspace_credentials=WorkspaceCredentials(read=_declared),
)


@dataclass(frozen=True)
class _Broker:
    asked: list[tuple[UUID, str]] = field(default_factory=list)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        self.asked.append((workspace_id, account_id))
        return f"token-{account_id}"


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _vault(store: CredentialStore, broker: _Broker) -> VaultReads:
    return VaultReads(
        store,
        workspace_slot_source((MANIFEST,)),
        {GITHUB: CliCredential(env="GH_TOKEN", header="authorization", secret=broker, git=GIT)},
        MODEL_KEY_ENVS(CONFIG),
        {slot.name: slot for slot in MANIFEST.credentials},
        ConnectorRegistry(entries={}),
        {},
    )


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _connection(workspace_id: UUID, provider: str, account_id: str, host: str) -> str:
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
                    shared=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return f"{CONNECTION_SECRET_PREFIX}{connection_id}"


async def _fill(store: CredentialStore, workspace_id: UUID, values: dict[str, str]) -> None:
    with ws(workspace_id):
        for slot, value in values.items():
            await store.put(workspace_id, slot, value)


async def _unbound(vault: VaultReads, workspace_id: UUID, name: str, host: str) -> None:
    with pytest.raises(SecretUnbound):
        await vault.resolve(workspace_id, name, host)


def _model_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "ufo-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bare-anthropic-key")
    monkeypatch.setenv("ACME_OPENAI_KEY", "acme-openai-key")
    monkeypatch.setenv("OPENAI_API_KEY", "bare-openai-key")
    monkeypatch.delenv("UFO_ACME_OPENAI_KEY", raising=False)
    monkeypatch.delenv("UFO_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.delenv("UFO_OPENROUTER_API_KEY", raising=False)


async def test_a_keyed_slot_resolves_on_its_own_host_once_filled(db: None) -> None:
    store, filled, empty = _store(), await _workspace(), await _workspace()
    vault = _vault(store, _Broker())
    await _fill(store, filled, {ACME_SLOT.name: ACME_SECRET, DECLARED_SLOT.name: DECLARED_SECRET})

    assert await vault.resolve(filled, ACME_SLOT.name, ACME_HOST) == SecretValue(
        value=ACME_SECRET, expires_at=None
    )
    assert await vault.resolve(filled, DECLARED_SLOT.name, DECLARED_HOST) == SecretValue(
        value=DECLARED_SECRET, expires_at=None
    )
    await _unbound(vault, filled, ACME_SLOT.name, OTHER_HOST)
    await _unbound(vault, filled, ACME_SLOT.name, DECLARED_HOST)
    await _unbound(vault, filled, DECLARED_SLOT.name, ACME_HOST)
    await _unbound(vault, filled, "acme_unknown_key", ACME_HOST)
    await _unbound(vault, empty, ACME_SLOT.name, ACME_HOST)


async def test_a_refusal_survives_the_pickle_a_durable_step_records_it_in(db: None) -> None:
    vault, workspace_id = _vault(_store(), _Broker()), await _workspace()

    with pytest.raises(SecretUnbound) as unbound:
        await vault.resolve(workspace_id, ACME_SLOT.name, OTHER_HOST)

    assert pickle.loads(pickle.dumps(unbound.value)).args == unbound.value.args


async def test_a_host_choice_slot_resolves_on_the_selected_host_only(db: None) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, _Broker())
    await _fill(store, workspace_id, {REGIONAL_SLOT.name: REGIONAL_SECRET, SITE_SLOT.name: EU_HOST})

    assert await vault.resolve(workspace_id, REGIONAL_SLOT.name, EU_HOST) == SecretValue(
        value=REGIONAL_SECRET, expires_at=None
    )
    await _unbound(vault, workspace_id, REGIONAL_SLOT.name, US_HOST)
    await _unbound(vault, workspace_id, SITE_SLOT.name, EU_HOST)


async def test_a_slot_with_no_injection_target_resolves_on_no_host(db: None) -> None:
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, _Broker())
    await _fill(store, workspace_id, {SYSTEM_TOKEN_SLOT.name: SYSTEM_TOKEN})

    for host in (ACME_HOST, US_HOST, EU_HOST, DECLARED_HOST, GITHUB_HOST, GIT.host, OTHER_HOST):
        await _unbound(vault, workspace_id, SYSTEM_TOKEN_SLOT.name, host)


async def test_a_connection_resolves_to_its_own_accounts_token_on_its_hosts(db: None) -> None:
    broker = _Broker()
    vault = _vault(_store(), broker)
    workspace_id, other = await _workspace(), await _workspace()
    github = await _connection(workspace_id, GITHUB, "acct-gh", GITHUB_HOST)
    notion = await _connection(workspace_id, "notion", "acct-notion", "api.notion.com")
    elsewhere = await _connection(other, GITHUB, "acct-elsewhere", GITHUB_HOST)

    for host in (GITHUB_HOST, GIT.host):
        assert await vault.resolve(workspace_id, github, host) == SecretValue(
            value="token-acct-gh", expires_at=None
        )
    await _unbound(vault, workspace_id, github, "example.com")
    await _unbound(vault, workspace_id, elsewhere, GITHUB_HOST)
    await _unbound(vault, workspace_id, notion, "api.notion.com")
    await _unbound(vault, workspace_id, f"{CONNECTION_SECRET_PREFIX}acct-gh", GITHUB_HOST)

    assert broker.asked == [(workspace_id, "acct-gh"), (workspace_id, "acct-gh")]


async def test_the_model_key_resolves_on_each_provider_host_from_the_env_the_config_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _model_keys(monkeypatch)
    vault, workspace_id = _vault(_store(), _Broker()), uuid4()

    resolved = {
        host: await vault.resolve(workspace_id, UFO_MODELS_SECRET, host)
        for host in ("api.anthropic.com", "api.openai.com", "openrouter.ai")
    }

    assert resolved == {
        "api.anthropic.com": SecretValue(value="ufo-anthropic-key", expires_at=None),
        "api.openai.com": SecretValue(value="acme-openai-key", expires_at=None),
        "openrouter.ai": SecretValue(value="openrouter-key", expires_at=None),
    }
    await _unbound(vault, workspace_id, UFO_MODELS_SECRET, OTHER_HOST)
    await _unbound(vault, workspace_id, UFO_MODELS_SECRET, GITHUB_HOST)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    await _unbound(vault, workspace_id, UFO_MODELS_SECRET, "openrouter.ai")


async def _resolve_route(ctx: ExtensionContext, request: Request) -> Response:
    resolved = await ctx.resolve_secret(request.query_params["name"], request.query_params["host"])
    return PlainTextResponse(resolved.value)


def _workspace_header(request: Request) -> UUID | None:
    return UUID(request.headers[WORKSPACE_HEADER])


async def test_a_route_resolves_through_the_mount_only_for_a_vault_read_extension(
    db: None,
) -> None:
    store, workspace_id = _store(), await _workspace()
    await _fill(store, workspace_id, {ACME_SLOT.name: ACME_SECRET})
    route = RouteSpec(
        method="GET", path="secret", handler=_resolve_route, identify=_workspace_header
    )
    app = FastAPI()
    _mount_ext_routes(
        app,
        (
            replace(MANIFEST, vault_read=True, routes=(route,)),
            Manifest(name="beta", version="0", routes=(route,)),
        ),
        store,
        None,
        None,
        None,
        NO_SPEND_GATES,
        UNGATED_LEDGER,
        vault=_vault(store, _Broker()),
    )
    query = {"name": ACME_SLOT.name, "host": ACME_HOST}
    headers = {WORKSPACE_HEADER: str(workspace_id)}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://serve"
    ) as client:
        resolved = await client.get("/ext/acme/secret", params=query, headers=headers)
        with pytest.raises(PermissionError, match="cannot resolve secrets"):
            await client.get("/ext/beta/secret", params=query, headers=headers)

    assert (resolved.status_code, resolved.text) == (200, ACME_SECRET)


async def test_no_resolve_puts_a_value_in_a_log_record(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _model_keys(monkeypatch)
    store, workspace_id = _store(), await _workspace()
    vault = _vault(store, _Broker())
    github = await _connection(workspace_id, GITHUB, "acct-gh", GITHUB_HOST)
    await _fill(
        store,
        workspace_id,
        {
            ACME_SLOT.name: ACME_SECRET,
            REGIONAL_SLOT.name: REGIONAL_SECRET,
            SITE_SLOT.name: EU_HOST,
            SYSTEM_TOKEN_SLOT.name: SYSTEM_TOKEN,
            DECLARED_SLOT.name: DECLARED_SECRET,
        },
    )
    caplog.set_level(logging.DEBUG)

    resolved = [
        await vault.resolve(workspace_id, name, host)
        for name, host in (
            (ACME_SLOT.name, ACME_HOST),
            (REGIONAL_SLOT.name, EU_HOST),
            (DECLARED_SLOT.name, DECLARED_HOST),
            (github, GITHUB_HOST),
            (UFO_MODELS_SECRET, "api.anthropic.com"),
            (UFO_MODELS_SECRET, "api.openai.com"),
            (UFO_MODELS_SECRET, "openrouter.ai"),
        )
    ]
    for name, host in (
        (ACME_SLOT.name, OTHER_HOST),
        (REGIONAL_SLOT.name, US_HOST),
        (SYSTEM_TOKEN_SLOT.name, ACME_HOST),
        (github, OTHER_HOST),
        (UFO_MODELS_SECRET, OTHER_HOST),
    ):
        await _unbound(vault, workspace_id, name, host)

    held = {secret.value for secret in resolved} | {SYSTEM_TOKEN}
    logged = "\n".join(repr(vars(record)) for record in caplog.records)
    assert not any(value in logged for value in held)
