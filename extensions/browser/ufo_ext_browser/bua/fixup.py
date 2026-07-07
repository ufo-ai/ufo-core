from __future__ import annotations

from ufo_ext_browser.bua.actions import CLICK_ACTIONS, ComputerAction, ScrollParameters
from ufo_ext_browser.bua.coordinate import Size, effective_model_size

DEFAULT_WAIT_DURATION = 3
ESCAPED_LITERALS = {"\\t": "\t", "\\n": "\n"}


def fixup_actions(
    actions: list[ComputerAction],
    viewport: Size,
    model_size: Size | None = None,
) -> list[ComputerAction]:
    """Repair common model mistakes in an action batch before dispatch: focus before typing,
    literal escape sequences in text, scrolls without an anchor, scroll_to without a ref, waits
    without a duration, and multi-clicks aimed at a ref."""
    model = effective_model_size(viewport, model_size)
    center = (model.width // 2, model.height // 2)
    out: list[ComputerAction] = []
    for action in actions:
        match action.action:
            case "type":
                focused = bool(out) and out[-1].action in CLICK_ACTIONS
                if not focused and (action.coordinate or action.ref):
                    out.append(_focus_click(action))
                out.append(_unescape_text(action))
            case "scroll" if action.coordinate is None:
                out.append(action.model_copy(update={"coordinate": center}))
            case "scroll_to" if not action.ref:
                out.append(
                    ComputerAction(
                        action="scroll",
                        coordinate=action.coordinate or center,
                        scroll_parameters=action.scroll_parameters or ScrollParameters(),
                    )
                )
            case "wait" if not action.duration:
                out.append(action.model_copy(update={"duration": DEFAULT_WAIT_DURATION}))
            case "double_click" | "triple_click" if action.ref and action.coordinate is None:
                out.append(ComputerAction(action="left_click", ref=action.ref))
            case _:
                out.append(action)
    return out


def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]:
    """Group actions so each wait closes its batch; the browser settles between batches."""
    batches: list[list[ComputerAction]] = []
    current: list[ComputerAction] = []
    for action in actions:
        current.append(action)
        if action.action == "wait":
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
