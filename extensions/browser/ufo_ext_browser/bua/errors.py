from __future__ import annotations

from ufo_ext_browser.bua.wire import ValidationError


class HallucinationError(ValidationError):
    """The model supplied a value that cannot exist, such as an invented element ref."""
