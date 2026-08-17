"""The keyed-connector table is the whole declaration: what a row implies must be exactly what the
egress proxy and the sandbox then act on, so these read the derived slots and the derived rules the
real proxy derivation produces from them — a row, not new code, is what adding a provider costs."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_keyed_connectors import KEYED_PROVIDERS, manifest

from ufo.credential_kind import CREDENTIAL_KIND
from ufo.credentials import CredentialStore, HostChoice
from ufo.db import workspace_tx
from ufo.egress_rules import (
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
)
from ufo.ext.loader import core_object_kinds, injecting_slots
from ufo.ext.manifest import Manifest
from ufo.schema import tables
from ufo.tools.context import ToolContext
from ufo.workspace import ws

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


def test_a_sites_row_declares_one_choice_its_keys_share() -> None:
    """The host companion is a non-secret setting, not a key: it carries no injection of its own, so
    nothing about it reaches the wire — it only decides which of the row's published hosts the keys
    do. Every key selects through the *same* choice object, which is what makes sharing `DD_HOST`
    legal and a divergence between two keys unrepresentable rather than merely checked."""
    slots = {slot.name: slot for slot in manifest().credentials}
    assert slots["datadog_api_host"].injection is None
    choices = {slots[f"datadog_{secret.key}"].injection.host for secret in DATADOG.secrets}
    assert choices == {DATADOG.target_host}
    choice = choices.pop()
    assert isinstance(choice, HostChoice)
    assert choice.slot == "datadog_api_host"
    assert choice.default == "api.datadoghq.com"
    assert US5_HOST in choice.hosts


def test_the_prompt_section_tells_the_agent_the_call_it_can_make() -> None:
    """The agent's only route to a keyed provider is its own HTTP call, so the section names the
    slots to request and the exact shape of the call, built from the row."""
    body = manifest().prompt_sections[0].body
    assert "request_credentials" in body
    assert 'curl -sS "https://$DD_HOST/<path>" -H "DD-API-KEY: $DD_API_KEY"' in body
    assert "DD-APPLICATION-KEY: $DD_APP_KEY" in body


async def test_datadog_is_connectable_end_to_end_through_workspace_credentials(
    db: None, tmp_path: Path
) -> None:
    """The reference implementation, from filled slots to the wire: the two keys the member stored
    become two header injections on this workspace's own Datadog site, the site is admitted and
    metered, and no secret is anywhere in the sandbox's view of the world — the sentinels are."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", US5_HOST)

    rules = await derive_credential_rules(injecting_slots((manifest(),)), workspace_id, store)

    assert [rule for rule in rules if isinstance(rule, ScopeRule)] == [
        ScopeRule(allowed_hosts=frozenset({US5_HOST}))
    ]
    assert [rule for rule in rules if isinstance(rule, MeterRule)] == [
        MeterRule(host=US5_HOST, dimension="requests")
    ]
    assert {
        (rule.header, rule.sentinel, rule.real) for rule in rules if isinstance(rule, InjectionRule)
    } == {
        ("DD-API-KEY", "UFO_SENTINEL_KEYED_DATADOG_API_KEY", "dd-api-real"),
        ("DD-APPLICATION-KEY", "UFO_SENTINEL_KEYED_DATADOG_APPLICATION_KEY", "dd-app-real"),
    }


async def test_a_workspace_that_keyed_nothing_reaches_no_datadog_host(db: None) -> None:
    """Declaration alone opens nothing: the provider is egress only for a workspace whose owner
    filled it, so an unkeyed workspace's sandbox cannot reach Datadog at all."""
    empty: Manifest = manifest()
    assert (
        await derive_credential_rules(injecting_slots((empty,)), await _workspace(), _store()) == ()
    )


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def test_a_read_reports_the_host_this_workspace_actually_uses(db: None) -> None:
    """The third consumer of the host, and the one that used to lie: the `credential` projection
    read the declared default, so a US5 workspace was told it injects to US1 while the
    proxy and the engine both used US5. It resolves through the same resolver they do, so a read
    cannot report a host the wire would not use — and a selection the row does not offer reads as no
    host rather than as the default, which is what makes a filled-but-unusable slot visible instead
    of looking like a member who has not chosen yet.

    Built through the real `core_object_kinds`, the production transform `turn_tools` wires into the
    kind, so dropping the store or changing how the host is projected fails here."""
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
