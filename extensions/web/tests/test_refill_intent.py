"""The billing screen's acts, and what they dispatch to.

The screen exists because the workspace that most needs to arrange a refill is the one whose
balance refuses every turn, so the control cannot live only in chat. It carries no billing rule of
its own: it posts the workspace object's `manage_billing` action on the route that names the
workspace, and the action decides who may run it and what the figures mean.
"""

import json
from uuid import uuid4

from ufo_ext_web.panels import _action_outcome, _portal_outcome

from ufo.schema.records import TerminalFrame, ToolIntent, admits_spent_balance

WORKSPACE_ID = "0f5c4d1e-7b2a-4c3d-9e8f-1a2b3c4d5e6f"
ARRANGE = {"operation": "autopay", "autopay_dollars": 100, "autopay_below_dollars": 25}
STOP = {"operation": "autopay", "autopay_dollars": None, "autopay_below_dollars": None}
CARD = {"operation": "portal"}

"""The intents the lane writes for the screen's three presses, spelled as the wire carries them:
the route's target first — kind, action, the workspace row — then the press's own body under
`input`. Each is the turn's inbound and its audit record, so the literal is what a reader of either
finds."""
TARGET = (
    '{"tool":"object_action","input":{"kind":"workspace","action":"manage_billing","name":"'
    + WORKSPACE_ID
    + '",'
)
ARRANGE_INTENT = (
    TARGET + '"input":{"operation":"autopay","autopay_dollars":100,"autopay_below_dollars":25}}}'
)
STOP_INTENT = (
    TARGET + '"input":{"operation":"autopay","autopay_dollars":null,"autopay_below_dollars":null}}}'
)
CARD_INTENT = TARGET + '"input":{"operation":"portal"}}}'


def _billing_input(prepared: ToolIntent) -> dict[str, object]:
    assert prepared.tool == "object_action"
    assert prepared.input["kind"] == "workspace"
    assert prepared.input["action"] == "manage_billing"
    assert prepared.input["name"] == WORKSPACE_ID
    body = prepared.input["input"]
    assert isinstance(body, dict)
    return body


def test_the_screen_arranges_a_refill_through_the_action_an_admin_speaks() -> None:
    """Dispatched verbatim to the workspace object's `manage_billing`, so the action's own admin
    gate answers and nothing about who may charge a card is decided a second time in the portal."""
    body = _billing_input(ToolIntent.model_validate_json(ARRANGE_INTENT))

    assert body == ARRANGE


def test_the_screen_stops_a_refill_by_naming_neither_figure() -> None:
    """Both figures together arrange it and neither stops it — the shape the action already takes,
    so a stop from the screen and a stop in chat are the same act."""
    body = _billing_input(ToolIntent.model_validate_json(STOP_INTENT))

    assert body == STOP


def test_the_screen_reaches_a_provider_through_the_verb_a_spent_balance_admits() -> None:
    """A refill is refused until a card is on file, and the only other way to put one there was a
    chat act the stopped balance refuses — so the screen carries that step too. It dispatches to the
    one action the balance gate exempts, which is what lets an admin whose workspace has stopped
    reach a provider at all."""
    prepared = ToolIntent.model_validate_json(CARD_INTENT)

    assert admits_spent_balance(prepared)
    assert _billing_input(prepared) == CARD
    assert not admits_spent_balance(
        ToolIntent(tool="object_action", input={"kind": "workspace", "action": "other"})
    )


def test_the_intent_is_the_wire_shape_in_the_routes_key_order() -> None:
    """The serialized intent is compared byte for byte at admission, so the literal is canonical:
    reading it back and writing it again changes nothing, and its keys stand in the route's order —
    kind, action, name, then the press's body under `input`."""
    for literal in (ARRANGE_INTENT, STOP_INTENT, CARD_INTENT):
        prepared = ToolIntent.model_validate_json(literal)
        assert list(prepared.input) == ["kind", "action", "name", "input"]
        assert prepared.model_dump_json() == literal


def test_the_portal_link_is_read_off_the_turns_own_answer() -> None:
    """The action states a JSON object inside the wall every untrusted result renders in, so the key
    is read rather than the first address in the text — and only the portal operation reads one: an
    autopay answer is done or refused like any other act."""
    turn_id = uuid4()
    frame = TerminalFrame(
        status="done",
        text='Here is the portal. {"portal_url": "https://billing.stripe.test/session/abc", '
        '"stripe_customer_id": "cus_1"}',
    )
    answered = json.loads(_portal_outcome(frame, turn_id).body)
    assert answered["applied"] is True
    assert answered["url"] == "https://billing.stripe.test/session/abc"

    routed = json.loads(_action_outcome("workspace", "manage_billing", CARD, frame, turn_id).body)
    assert routed["url"] == "https://billing.stripe.test/session/abc"
    arranged = json.loads(
        _action_outcome(
            "workspace", "manage_billing", ARRANGE, TerminalFrame(status="done", text="ok"), turn_id
        ).body
    )
    assert arranged == {"applied": True, "message": "Saved.", "turn_id": str(turn_id)}


def test_a_refused_portal_keeps_the_tools_own_words() -> None:
    """Only an admin may reach billing, and that sentence is the answer — never a minted link."""
    frame = TerminalFrame(status="failed", text="only a workspace admin can manage billing")
    answered = json.loads(_portal_outcome(frame, uuid4()).body)

    assert answered["applied"] is False
    assert answered["message"] == "only a workspace admin can manage billing"
    assert "url" not in answered
