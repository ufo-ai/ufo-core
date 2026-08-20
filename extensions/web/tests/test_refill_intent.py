"""The billing screen's one mutation, and what it dispatches to.

The screen exists because the workspace that most needs to arrange a refill is the one whose
balance refuses every turn, so the control cannot live only in chat. It carries no billing rule of
its own: it prepares the same verb an admin speaks, and the tool it names decides who may run it
and what the figures mean.
"""

import json
from uuid import uuid4

from ufo_ext_web.panels import (
    PanelIntent,
    PaymentMethodIntent,
    RefillIntent,
    _portal_outcome,
    _tool_intent,
)

from ufo.schema.records import BILLING_INTENT_TOOL, TerminalFrame, ToolIntent

ARRANGE = {"verb": "refill", "kind": "billing", "amount_dollars": 100, "below_dollars": 25}
STOP = {"verb": "refill", "kind": "billing", "amount_dollars": None, "below_dollars": None}
CARD = {"verb": "save_card", "kind": "billing"}


def _prepared(submitted: dict[str, object]) -> ToolIntent:
    intent = PanelIntent.model_validate({"submitted": submitted}).submitted
    assert isinstance(intent, RefillIntent)
    return _tool_intent(intent, None)


def test_the_screen_arranges_a_refill_through_the_verb_an_admin_speaks() -> None:
    """Dispatched verbatim to `manage_billing`, so the tool's own admin gate answers and nothing
    about who may charge a card is decided a second time in the portal."""
    prepared = _prepared(ARRANGE)

    assert prepared.tool == "manage_billing"
    assert prepared.input["action"] == "autopay"
    assert prepared.input["autopay_dollars"] == 100
    assert prepared.input["autopay_below_dollars"] == 25


def test_the_screen_stops_a_refill_by_naming_neither_figure() -> None:
    """Both figures together arrange it and neither stops it — the shape the tool already takes, so
    a stop from the screen and a stop in chat are the same act."""
    prepared = _prepared(STOP)

    assert prepared.tool == "manage_billing"
    assert prepared.input["action"] == "autopay"
    assert prepared.input["autopay_dollars"] is None
    assert prepared.input["autopay_below_dollars"] is None


def test_the_screen_reaches_a_provider_through_the_verb_a_spent_balance_admits() -> None:
    """A refill is refused until a card is on file, and the only other way to put one there was a
    chat act the stopped balance refuses — so the screen carries that step too. It dispatches to the
    one tool the balance gate exempts, which is what lets an admin whose workspace has stopped reach
    a provider at all."""
    intent = PanelIntent.model_validate({"submitted": CARD}).submitted
    assert isinstance(intent, PaymentMethodIntent)
    prepared = _tool_intent(intent, None)

    assert prepared.tool == BILLING_INTENT_TOOL
    assert prepared.input["action"] == "portal"


def test_the_portal_link_is_read_off_the_turns_own_answer() -> None:
    """The tool states a JSON object inside the wall every untrusted result renders in, so the key
    is read rather than the first address in the text."""
    frame = TerminalFrame(
        status="done",
        text='Here is the portal. {"portal_url": "https://billing.stripe.test/session/abc", '
        '"stripe_customer_id": "cus_1"}',
    )
    answered = json.loads(_portal_outcome(frame, uuid4()).body)

    assert answered["applied"] is True
    assert answered["url"] == "https://billing.stripe.test/session/abc"


def test_a_refused_portal_keeps_the_tools_own_words() -> None:
    """Only an admin may reach billing, and that sentence is the answer — never a minted link."""
    frame = TerminalFrame(status="failed", text="only a workspace admin can manage billing")
    answered = json.loads(_portal_outcome(frame, uuid4()).body)

    assert answered["applied"] is False
    assert answered["message"] == "only a workspace admin can manage billing"
    assert "url" not in answered
