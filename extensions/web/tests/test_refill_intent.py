"""The billing screen's one mutation, and what it dispatches to.

The screen exists because the workspace that most needs to arrange a refill is the one whose
balance refuses every turn, so the control cannot live only in chat. It carries no billing rule of
its own: it prepares the same verb an admin speaks, and the tool it names decides who may run it
and what the figures mean.
"""

from ufo_ext_web.panels import PanelIntent, RefillIntent, _tool_intent

from ufo.schema.records import ToolIntent

ARRANGE = {"verb": "refill", "kind": "billing", "amount_dollars": 100, "below_dollars": 25}
STOP = {"verb": "refill", "kind": "billing", "amount_dollars": None, "below_dollars": None}


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
