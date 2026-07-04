from __future__ import annotations

from dataclasses import dataclass

CLAUDE_MAX_LONG_EDGE = 1568
CLAUDE_MAX_PIXELS = 1_150_000
GEMINI_MAX_COORD = 1000


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True)
class Coord:
    x: int
    y: int


def compute_screenshot_dimensions(viewport: Size) -> Size:
    """Largest screenshot size the model receives without server-side downscaling. Anthropic vision
    input downscales images longer than 1568px on a side or above ~1.15M pixels, so screenshots are
    pre-fit to the largest size the model sees losslessly."""
    scale = min(1.0, CLAUDE_MAX_LONG_EDGE / max(viewport.width, viewport.height))
    width = int(viewport.width * scale)
    height = int(viewport.height * scale)
    if width * height > CLAUDE_MAX_PIXELS:
        shrink = (CLAUDE_MAX_PIXELS / (width * height)) ** 0.5
        width = int(width * shrink)
        height = int(height * shrink)
    return Size(width, height)


def effective_model_size(viewport: Size, model_size: Size | None = None) -> Size:
    """The coordinate space the model reasons in: an explicit override, else screenshot size."""
    return model_size or compute_screenshot_dimensions(viewport)


def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None = None) -> Coord:
    """Map a model-space point onto browser viewport pixels for input dispatch."""
    model = effective_model_size(viewport, model_size)
    return Coord(
        x=int(coord.x * (viewport.width / model.width)),
        y=int(coord.y * (viewport.height / model.height)),
    )


def model_coordinate_space(model: str | None) -> Size | None:
    """Gemini reports points on a fixed 0-1000 grid regardless of image dimensions; Claude-family
    models use screenshot pixels, so they take the computed screenshot dimensions instead."""
    if model and "gemini" in model.lower():
        return Size(GEMINI_MAX_COORD, GEMINI_MAX_COORD)
    return None


def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None = None) -> Coord:
    """Map browser viewport pixels back into the model's coordinate space."""
    model = effective_model_size(viewport, model_size)
    return Coord(
        x=int(coord.x * (model.width / viewport.width)),
        y=int(coord.y * (model.height / viewport.height)),
    )
