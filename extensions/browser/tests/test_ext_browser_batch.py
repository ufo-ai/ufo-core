"""The action batch's partial-effect proof.

`BrowserComputer.run` takes the actions in order against a live page, so an action that fails part
way through leaves every action before it applied — a click landed, a field is typed, a form is
submitted. The loop is what is asserted here; `act` is stubbed, since driving it needs a real
browser and the integration suite already does that. What the loop has to carry back is which
actions were taken and where it stopped, so the caller can hand the agent something other than a
cause that reads as though nothing happened.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from ufo_ext_browser.bua.actions import ComputerAction
from ufo_ext_browser.bua.computer import BrowserComputer
from ufo_ext_browser.bua.coordinate import Size
from ufo_ext_browser.bua.errors import BatchInterrupted, HallucinationError
from ufo_ext_browser.bua.keys import KeyboardState
from ufo_ext_browser.bua.settle import Settle

VIEWPORT = Size(width=1024, height=768)


@dataclass
class _Tab:
    session_id: str = "s1"
    keyboard: KeyboardState = field(default_factory=KeyboardState)


@dataclass
class _Session:
    """Stands in for the CDP-driving session only — the loop under test never reaches the parts
    that need a browser, because it stops at the failing action."""

    model_size: Size = VIEWPORT
    is_mac: bool = False
    last_batch_scroll_only: bool = False
    settle: Settle = field(default_factory=Settle)
    downloads: list[Any] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    tab: _Tab = field(default_factory=_Tab)

    async def page(self, tab_id: int | None = None) -> _Tab:
        return self.tab


@dataclass(frozen=True)
class _ScriptedComputer(BrowserComputer):
    """Takes each action as a recorded message and fails at `fails_at` (zero-based)."""

    fails_at: int = 0
    taken: list[str] = field(default_factory=list)

    async def act(self, tab: Any, action: ComputerAction) -> tuple[str, None]:
        if len(self.taken) == self.fails_at:
            raise HallucinationError("browser ref 'e12' is not resolvable")
        message = f"Did {action.action}"
        self.taken.append(message)
        return message, None


def _computer(fails_at: int) -> _ScriptedComputer:
    return _ScriptedComputer(
        browser=_Session(),  # type: ignore[arg-type]
        viewport=VIEWPORT,
        max_wait_seconds=1.0,
        fails_at=fails_at,
    )


def _four_clicks() -> dict[str, Any]:
    """Four actions no fixup rewrites, so the planned list matches the batch one for one."""
    return {
        "actions": [
            {"action": "left_click", "coordinate": [10, 10]},
            {"action": "left_click", "coordinate": [20, 20]},
            {"action": "left_click", "coordinate": [30, 30]},
            {"action": "left_click", "coordinate": [40, 40]},
        ]
    }


def _typing_batch() -> dict[str, Any]:
    """A batch opening on a bare `type` at a coordinate. Nothing focuses it, so `fixup_actions`
    inserts a click before it and the planned list runs one longer than the batch sent here."""
    return {
        "actions": [
            {"action": "type", "coordinate": [20, 20], "text": "ada@example.com"},
            {"action": "left_click", "coordinate": [30, 30]},
        ]
    }


async def test_a_batch_that_fails_part_way_carries_the_actions_it_already_took() -> None:
    computer = _computer(fails_at=2)
    with pytest.raises(BatchInterrupted) as raised:
        await computer.run(_four_clicks())
    interrupted = raised.value
    assert interrupted.applied == ((0, "Did left_click"), (1, "Did left_click"))
    assert interrupted.origin == 2
    assert interrupted.total == 4
    assert isinstance(interrupted.cause, HallucinationError)
    assert str(interrupted) == "action 3 of 4 failed: browser ref 'e12' is not resolvable"


async def test_a_batch_that_fails_on_its_first_action_says_it_applied_nothing() -> None:
    """The one case where a retry of the whole batch is safe, and the agent can only know it
    because the empty list says so."""
    computer = _computer(fails_at=0)
    with pytest.raises(BatchInterrupted) as raised:
        await computer.run(_four_clicks())
    assert raised.value.applied == ()
    assert raised.value.origin == 0


async def test_a_failure_after_an_inserted_action_counts_in_the_batch_the_caller_sent() -> None:
    """A fixup may insert an action the caller never wrote. Numbered by the planned list, the
    failure names an action they did not send: they read one that never ran as applied, and
    re-issue one that already reached the page — the duplicate this failure shape exists to
    prevent."""
    computer = _computer(fails_at=2)
    with pytest.raises(BatchInterrupted) as raised:
        await computer.run(_typing_batch())
    interrupted = raised.value

    assert len(computer.taken) == 2
    assert interrupted.total == 2
    assert interrupted.origin == 1
    assert str(interrupted) == "action 2 of 2 failed: browser ref 'e12' is not resolvable"
    assert interrupted.applied == ((0, "Did left_click"), (0, "Did type"))


async def test_the_inserted_focus_click_is_reported_against_the_action_it_serves() -> None:
    """The insert is not a caller action of its own. When the type it focuses for is the one that
    fails, the click that already landed is reported under that same index — a partial inside one
    of their actions, which is what it is."""
    computer = _computer(fails_at=1)
    with pytest.raises(BatchInterrupted) as raised:
        await computer.run(_typing_batch())
    interrupted = raised.value

    assert interrupted.origin == 0
    assert interrupted.total == 2
    assert interrupted.applied == ((0, "Did left_click"),)
