"""The keyed-connector table is the whole declaration: what a row implies must be exactly what the
proxy service and the sandbox then act on, so these read the derived slots and the binds the real
session policy compiles from them — a row, not new code, is what adding a provider costs."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_keyed_connectors import KEYED_PROVIDERS, KeyedSecret, manifest

from ufo.db import workspace_tx
from ufo.host.ext.loader import core_object_kinds, injecting_slots
from ufo.host.kinds.credential_kind import CREDENTIAL_KIND
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.access.egress_rules import Bind, derive_credential_binds
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

DATADOG = next(provider for provider in KEYED_PROVIDERS if provider.provider == "datadog")
US5_HOST = "api.us5.datadoghq.com"


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def test_every_row_declares_a_distinct_slot_sentinel_and_env() -> None:
    """A sentinel collision would draw another provider's secret and an env collision would
    overwrite it in the sandbox, so both are unique across the whole table, as are slot names."""
    slots = manifest().credentials
    sentinels = [slot.injection.sentinel for slot in slots if slot.injection]
    envs = [slot.injection.env for slot in slots if slot.injection and slot.injection.env]
    assert len(set(slot.name for slot in slots)) == len(slots)
    assert len(set(sentinels)) == len(sentinels)
    assert len(set(envs)) == len(envs)


def test_posthog_keys_ride_the_authorization_header_with_its_bearer_scheme() -> None:
    assert "posthog" in {provider.provider for provider in KEYED_PROVIDERS}
    posthog = next(provider for provider in KEYED_PROVIDERS if provider.provider == "posthog")
    host = posthog.target_host
    assert isinstance(host, HostChoice)
    assert host.slot == "posthog_api_host"
    assert host.default == "us.posthog.com"
    assert host.hosts == ("us.posthog.com", "eu.posthog.com")
    body = manifest().prompt_sections[0].body
    assert (
        'curl -sS "https://$POSTHOG_HOST/<path>" -H "Authorization: Bearer $POSTHOG_API_KEY"'
        in body
    )


def test_pandadoc_rides_its_own_api_key_scheme_on_the_authorization_header() -> None:
    """PandaDoc takes its key as `Authorization: API-Key <key>` — neither a bearer nor a bare
    value, and the third scheme the proxy re-prefixes."""
    pandadoc = next(provider for provider in KEYED_PROVIDERS if provider.provider == "pandadoc")
    assert pandadoc.target_host == "api.pandadoc.com"
    assert (
        'curl -sS "https://api.pandadoc.com/<path>" -H "Authorization: API-Key $PANDADOC_API_KEY"'
        in manifest().prompt_sections[0].body
    )


def test_a_scheme_the_proxy_cannot_swap_is_refused_at_declaration() -> None:
    with pytest.raises(ValueError, match="scheme"):
        KeyedSecret(key="k", header="Authorization", scheme="Basic", env="K", description="k")


async def test_posthog_is_connectable_end_to_end_through_workspace_credentials(db: None) -> None:
    """From the filled slot to the wire: the stored key binds once, on the Authorization header of
    the workspace's chosen PostHog cloud and nowhere else."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "posthog_api_key", "phx-real")
    await store.put(workspace_id, "posthog_api_host", "eu.posthog.com")

    binds = await derive_credential_binds(
        WorkspaceSlots(deploy=injecting_slots((manifest(),))), workspace_id, store
    )

    assert binds == (
        Bind(
            host="eu.posthog.com",
            header="Authorization",
            secret="posthog_api_key",
            env="POSTHOG_API_KEY",
        ),
    )


async def test_datadog_is_connectable_end_to_end_through_workspace_credentials(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", US5_HOST)

    binds = await derive_credential_binds(
        WorkspaceSlots(deploy=injecting_slots((manifest(),))), workspace_id, store
    )

    assert set(binds) == {
        Bind(host=US5_HOST, header="DD-API-KEY", secret="datadog_api_key", env="DD_API_KEY"),
        Bind(
            host=US5_HOST,
            header="DD-APPLICATION-KEY",
            secret="datadog_application_key",
            env="DD_APP_KEY",
        ),
    }


async def test_a_workspace_that_keyed_nothing_reaches_no_datadog_host(db: None) -> None:
    """Declaration alone opens nothing: the provider is egress only for a workspace whose owner
    filled it, so an unkeyed workspace's sandbox cannot reach Datadog at all."""
    empty: Manifest = manifest()
    assert (
        await derive_credential_binds(
            WorkspaceSlots(deploy=injecting_slots((empty,))), await _workspace(), _store()
        )
        == ()
    )


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def test_a_read_reports_the_host_this_workspace_actually_uses(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    bound = next(
        kind
        for kind in core_object_kinds((manifest(),), store)
        if kind.kind.name == CREDENTIAL_KIND
    )
    objects = bound.kind.store
    ctx = cast(ToolContext, None)
    with ws(workspace_id):
        await store.put(workspace_id, "datadog_api_key", "dd-api-real")
        unchosen = await objects.status(ctx, "datadog-api-key", expected_generation=None)
        assert unchosen is not None and unchosen["host"] == "api.datadoghq.com"

        await store.put(workspace_id, "datadog_api_host", US5_HOST)
        chosen = await objects.status(ctx, "datadog-api-key", expected_generation=None)
        assert chosen is not None and chosen["host"] == US5_HOST

        await store.put(workspace_id, "datadog_api_host", "169.254.169.254")
        refused = await objects.status(ctx, "datadog-api-key", expected_generation=None)
        assert refused is not None and refused["filled"] is True and refused["host"] is None

        keyed = await objects.get(ctx, "datadog-api-key")
        assert keyed is not None
        assert keyed.spec.host_slot == "datadog_api_host"
        assert US5_HOST in keyed.spec.host_options
        companion = await objects.get(ctx, "datadog-api-host")
        assert companion is not None and companion.spec.host_options == ()
