from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

ActionType = Literal[
    "left_click",
    "right_click",
    "double_click",
    "triple_click",
    "left_click_drag",
    "type",
    "key",
    "scroll",
    "scroll_to",
    "screenshot",
    "wait",
]

CLICK_ACTIONS = frozenset({"left_click", "right_click", "double_click", "triple_click"})


class ScrollParameters(BaseModel):
    """Direction and distance of a scroll. The action vocabulary follows the Anthropic computer-use
    tool's set, extended with `scroll_to` and element refs from read_page/find."""

    scroll_direction: Annotated[
        Literal["up", "down", "left", "right"],
        Field(description="Which way to scroll."),
    ] = "down"
    scroll_amount: Annotated[
        Annotated[float, Field(ge=0, le=5.0)] | Literal["max"],
        Field(
            description="How many viewport heights to scroll: 0 to 5, where 0.5 is half a "
            'screen. "max" jumps to the far end of the page — pair it with get_page_text to '
            "harvest infinite-scroll content."
        ),
    ] = 1.0


class ComputerAction(BaseModel):
    action: Annotated[
        ActionType,
        Field(
            description="What to do: a click action at `coordinate` or `ref`; `type` text "
            'into the focused element; `key` for a combo such as "cmd+a" or "ctrl+a"; '
            "`scroll` by `scroll_parameters`; `scroll_to` a `ref`; `left_click_drag` from "
            "`start_coordinate` to `coordinate`; `screenshot`; or `wait` for `duration` "
            "seconds."
        ),
    ]
    coordinate: Annotated[
        tuple[int, int] | None,
        Field(
            description="Target point as (x, y) pixels from the top-left corner. Click and "
            "scroll actions take this (or `ref`); for `left_click_drag` it is the drop point."
        ),
    ] = None
    text: Annotated[
        str | None,
        Field(
            description="Text to `type`, or the combo to press for `key`. Combos join keys "
            'with "+" and use the platform modifier: "cmd" on macOS, "ctrl" on Windows/Linux.'
        ),
    ] = None
    scroll_parameters: Annotated[
        ScrollParameters | None,
        Field(description="Direction and distance for `scroll`."),
    ] = None
    duration: Annotated[
        int | None,
        Field(ge=0, le=30, description="Seconds to pause for `wait`, at most 30."),
    ] = None
    start_coordinate: Annotated[
        tuple[int, int] | None,
        Field(description="Pick-up point (x, y) for `left_click_drag`."),
    ] = None
    ref: Annotated[
        str | None,
        Field(
            description='Element ref from read_page or find, e.g. "e5", or "f1e3" inside an '
            "iframe. Required for `scroll_to`; click actions accept it in place of "
            "`coordinate`."
        ),
    ] = None
