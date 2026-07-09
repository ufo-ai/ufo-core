"""The browserbase cdp provider: a static lease over a Browserbase CDP connect URL read from a
host-side BYOK slot. Verified against a real credential store — a seeded slot yields the endpoint,
an unset one fails loud — and the manifest declares exactly the provider plus its one slot."""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_browserbase as browserbase
from cryptography.fernet import Fernet

from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.schema import tables
from ufo.workspace import init_workspace_credentials, ws

CONNECT_URL = "wss://connect.browserbase.com?apiKey=bb-test-key"


async def _provider(url: str | None) -> tuple[browserbase.BrowserbaseCdpProvider, UUID]:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    if url is not None:
        await store.put(workspace_id, browserbase.CONNECT_URL_SLOT, url)
    credentials = context_for(
        browserbase.NAME, frozenset({browserbase.CONNECT_URL_SLOT})
    ).credentials
    return browserbase.BrowserbaseCdpProvider(credentials=credentials), workspace_id


def test_manifest_declares_the_slot_and_the_browserbase_cdp_provider() -> None:
    manifest = browserbase.manifest()
    assert manifest.name == "browserbase"
    (slot,) = manifest.credentials
    assert slot.name == "browserbase_cdp_url"
    assert slot.injection is None
    (spec,) = manifest.cdp_providers
    assert spec.backend == "browserbase"
    assert not manifest.tools


async def test_lease_yields_the_seeded_connect_url(db: None) -> None:
    provider, workspace_id = await _provider(CONNECT_URL)
    with ws(workspace_id):
        lease = await provider.lease()
        endpoint = await lease.endpoint()
        assert endpoint.url == CONNECT_URL
        assert await lease.token() == CONNECT_URL
        await lease.aclose()


async def test_reattach_reconnects_to_the_same_static_url(db: None) -> None:
    provider, workspace_id = await _provider(CONNECT_URL)
    with ws(workspace_id):
        lease = await provider.reattach("ignored-token")
        assert (await lease.endpoint()).url == CONNECT_URL


async def test_lease_without_the_slot_set_fails_loud(db: None) -> None:
    provider, workspace_id = await _provider(None)
    with ws(workspace_id), pytest.raises(CredentialSlotUnset):
        await provider.lease()
