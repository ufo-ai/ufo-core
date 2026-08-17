"""The billing workflow against REAL Stripe (test mode) and REAL Metronome (sandbox).

The unit suite proves the gates, the wire, and idempotence over a MockTransport; this proves the
provider contracts themselves — that the parameters we send are the ones Stripe and Metronome
accept, and that the workspace UUID we stamp on every usage event really is the id Metronome
matches to the customer. Nothing here is mocked.

The one act a headless test cannot perform is a human completing the hosted portal, so it does what
the portal does — mint a test card, attach it, make it the invoice default — and then asserts the
module reads that card back. The portal session itself is still created and its URL asserted.

Opt-in: skips unless every provider setting is present. Point it at a Stripe **test-mode** key and a
Metronome **sandbox** token — it creates a real customer in whichever account the credentials
name."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_metronome as metronome

from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.schema import tables
from ufo.workspace import ws

REQUIRED = (
    metronome.STRIPE_SECRET_KEY_ENV,
    metronome.STRIPE_PORTAL_CONFIGURATION_ENV,
    metronome.METRONOME_BEARER_TOKEN_ENV,
)

pytestmark = pytest.mark.skipif(
    not all(os.environ.get(name) for name in REQUIRED),
    reason=f"needs a live provider smoke configuration: {', '.join(REQUIRED)}",
)

TEST_CARD_TOKEN = "tok_visa"
PORTAL_HOST = "billing.stripe.com"


async def _stripe_form(path: str, data: dict[str, str]) -> dict[str, object]:
    """What the hosted portal does for the member, done directly: this is setup for the assertions,
    never one of the module's own calls."""
    key = os.environ[metronome.STRIPE_SECRET_KEY_ENV]
    async with httpx.AsyncClient(timeout=metronome.BILLING_TIMEOUT_SECONDS) as http:
        response = await http.post(
            f"{metronome.STRIPE_API}{path}",
            data=data,
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.is_success, response.text
    return response.json()


async def _save_a_card(customer_id: str) -> None:
    method = await _stripe_form(
        "/payment_methods", {"type": "card", "card[token]": TEST_CARD_TOKEN}
    )
    method_id = str(method["id"])
    await _stripe_form(f"/payment_methods/{method_id}/attach", {"customer": customer_id})
    await _stripe_form(
        f"/customers/{customer_id}",
        {"invoice_settings[default_payment_method]": method_id},
    )


@pytest.mark.serial
async def test_live_portal_and_payment_method(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    ctx = context_for(metronome.NAME, frozenset())
    config = metronome.BillingConfig.from_env()

    with ws(workspace_id):
        portal = await metronome._billing_portal(ctx, config)
        record = await metronome._billing_record(ctx)
    assert record is not None
    assert record.stripe_customer_id.startswith("cus_")
    assert PORTAL_HOST in str(portal.content[0].text)

    with ws(workspace_id):
        assert not await metronome._has_default_payment_method(
            config, record.stripe_customer_id, None
        )

    await _save_a_card(record.stripe_customer_id)

    with ws(workspace_id):
        assert await metronome._has_default_payment_method(config, record.stripe_customer_id, None)
        assert (await metronome._billing_record(ctx)) == record

    token = os.environ[metronome.METRONOME_BEARER_TOKEN_ENV]
    with ws(workspace_id):
        await metronome._ensure_metronome_customer(ctx, token, None)
    async with httpx.AsyncClient(timeout=metronome.BILLING_TIMEOUT_SECONDS) as http:
        assert (
            await metronome._customer_by_alias(
                http, {"Authorization": f"Bearer {token}"}, str(workspace_id)
            )
        ) is not None

    await metronome._ingest(
        token,
        [
            {
                "transaction_id": f"smoke:{workspace_id}",
                "customer_id": str(workspace_id),
                "event_type": metronome.EVENT_TYPE,
                "timestamp": metronome._rfc3339(datetime.now(UTC)),
                "properties": {"dimension": "tokens", "model": "smoke", "amount": "1"},
            }
        ],
        None,
    )

    with ws(workspace_id):
        again = await metronome._billing_portal(ctx, config)
    fresh = str(again.content[0].text)
    assert PORTAL_HOST in fresh
    assert fresh != str(portal.content[0].text)
