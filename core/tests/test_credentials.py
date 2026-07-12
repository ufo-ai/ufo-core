import time
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.credentials import (
    CREDENTIAL_REQUEST_TTL_SECONDS,
    CredentialRequestInvalid,
    CredentialRequests,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    open_credential_request,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.sandbox.proxy.rules import (
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
)
from ufo.schema import tables


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


async def test_credential_round_trips_and_is_encrypted_at_rest(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "sk-secret-value")
    assert await store.get(workspace_id, "sample_api") == "sk-secret-value"
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.credential.c.ciphertext).where(
                    tables.credential.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert b"sk-secret-value" not in stored.ciphertext


async def test_put_upserts(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "one")
    await store.put(workspace_id, "sample_api", "two")
    assert await store.get(workspace_id, "sample_api") == "two"


async def test_put_rejects_an_empty_value(db: None) -> None:
    with pytest.raises(ValueError, match="empty"):
        await _store().put(await _workspace(), "sample_api", "")


async def test_unset_slot_raises(db: None) -> None:
    store = _store()
    with pytest.raises(CredentialSlotUnset, match="missing"):
        await store.get(await _workspace(), "missing")


async def test_derive_swaps_stored_secret_and_meters(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "real-secret-42")
    manifest = Manifest(
        name="sample",
        version="1",
        credentials=(
            CredentialSlot(
                name="sample_api",
                description="x",
                injection=InjectionTarget(
                    host="api.sample.test",
                    header="authorization",
                    sentinel="Bearer SENTINEL",
                    dimension="tokens",
                ),
            ),
        ),
    )
    rules = await derive_credential_rules((manifest,), workspace_id, store)
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    injection = next(r for r in rules if isinstance(r, InjectionRule))
    meter = next(r for r in rules if isinstance(r, MeterRule))
    assert scope.allowed_hosts == frozenset({"api.sample.test"})
    assert injection.real == "real-secret-42"
    assert injection.sentinel == "Bearer SENTINEL"
    assert meter == MeterRule(host="api.sample.test", dimension="tokens")


async def test_unset_slot_opens_no_egress(db: None) -> None:
    manifest = Manifest(
        name="s",
        version="1",
        credentials=(
            CredentialSlot(
                name="unset",
                description="x",
                injection=InjectionTarget(host="h", header="authorization", sentinel="S"),
            ),
        ),
    )
    assert await derive_credential_rules((manifest,), await _workspace(), _store()) == ()


async def test_code_only_slot_has_no_wire_rule(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "code_only", "x")
    manifest = Manifest(
        name="s", version="1", credentials=(CredentialSlot(name="code_only", description="x"),)
    )
    assert await derive_credential_rules((manifest,), workspace_id, store) == ()


def test_credential_request_seal_round_trips_and_expires() -> None:
    """The sealed grant a `request_credentials` call hands a surface: opens to exactly what was
    sealed, and refuses garbage, a foreign key, or a seal older than the TTL."""
    fernet = Fernet(Fernet.generate_key())
    state = CredentialRequestState(workspace_id=uuid4(), member_id=uuid4(), slots=("a", "b"))
    sealed = seal_credential_request(fernet, state)
    assert open_credential_request(fernet, sealed) == state
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, "garbage")
    stale = fernet.encrypt_at_time(
        state.model_dump_json().encode(),
        int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
    ).decode()
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, stale)
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(Fernet(Fernet.generate_key()), sealed)


def test_credential_requests_seal_only_declared_slots() -> None:
    requests = CredentialRequests(fernet=Fernet(Fernet.generate_key()), declared=frozenset({"a"}))
    assert requests.seal(uuid4(), uuid4(), ("a",))
    with pytest.raises(ValueError, match="declares credential slot"):
        requests.seal(uuid4(), uuid4(), ("a", "nope"))
