from __future__ import annotations

from dataclasses import dataclass

from ufo_ext_browser.bua.actions import CLICK_ACTIONS, ComputerAction, ScrollParameters
from ufo_ext_browser.bua.coordinate import Size, effective_model_size

DEFAULT_WAIT_DURATION = 3
ESCAPED_LITERALS = {"\\t": "\t", "\\n": "\n"}


@dataclass(frozen=True)
class PlannedAction:
    """One action to take, and the place in the caller's own batch it answers for.

    A fixup may insert an action the caller never sent — a focus click before a type — so a
    position in the planned list is not a position in theirs. A failure numbered by the planned
    list points the caller at an action they did not send: they read one that never ran as
    applied, and re-issue one that already reached the page. `origin` is what the caller counts
    in, so it is what a failure reports."""

    origin: int
    action: ComputerAction


def fixup_actions(
    actions: list[ComputerAction],
    viewport: Size,
    model_size: Size | None = None,
) -> list[PlannedAction]:
    """Repair common model mistakes in an action batch before dispatch: focus before typing,
    literal escape sequences in text, scrolls without an anchor, scroll_to without a ref, waits
    without a duration, and multi-clicks aimed at a ref. Each repair keeps the index of the action
    the caller sent, including an inserted one, which answers for the action that needed it."""
    model = effective_model_size(viewport, model_size)
    center = (model.width // 2, model.height // 2)
    out: list[PlannedAction] = []
    for origin, action in enumerate(actions):
        match action.action:
            case "type":
                focused = bool(out) and out[-1].action.action in CLICK_ACTIONS
                if not focused and (action.coordinate or action.ref):
                    out.append(PlannedAction(origin, _focus_click(action)))
                out.append(PlannedAction(origin, _unescape_text(action)))
            case "scroll" if action.coordinate is None:
                out.append(PlannedAction(origin, action.model_copy(update={"coordinate": center})))
            case "scroll_to" if not action.ref:
                out.append(
                    PlannedAction(
                        origin,
                        ComputerAction(
                            action="scroll",
                            coordinate=action.coordinate or center,
                            scroll_parameters=action.scroll_parameters or ScrollParameters(),
                        ),
                    )
                )
            case "wait" if not action.duration:
                out.append(
                    PlannedAction(
                        origin, action.model_copy(update={"duration": DEFAULT_WAIT_DURATION})
                    )
                )
            case "double_click" | "triple_click" if action.ref and action.coordinate is None:
                out.append(
                    PlannedAction(origin, ComputerAction(action="left_click", ref=action.ref))
                )
            case _:
                out.append(PlannedAction(origin, action))
    return out


def split_at_waits(planned: list[PlannedAction]) -> list[list[PlannedAction]]:
    """Group actions so each wait closes its batch; the browser settles between batches."""
    batches: list[list[PlannedAction]] = []
    current: list[PlannedAction] = []
    for item in planned:
        current.append(item)
        if item.action.action == "wait":
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def _focus_click(action: ComputerAction) -> ComputerAction:
    if action.coordinate:
        return ComputerAction(action="left_click", coordinate=action.coordinate)
    return ComputerAction(action="left_click", ref=action.ref)


def _unescape_text(action: ComputerAction) -> ComputerAction:
    text = action.text or ""
    if not any(literal in text for literal in ESCAPED_LITERALS):
        return action
    for literal, replacement in ESCAPED_LITERALS.items():
        text = text.replace(literal, replacement)
    return action.model_copy(update={"text": text})
