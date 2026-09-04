from __future__ import annotations

from ufo_ext_browser.bua.wire import ValidationError


class HallucinationError(ValidationError):
    """The model supplied a value that cannot exist, such as an invented element ref."""


class BatchInterrupted(Exception):
    """An action batch that failed part-way through. The actions before the failing one already
    reached the page — a click landed, text was typed, a form was submitted — and the browser holds
    their result whether or not the batch finished. `applied` pairs each message with the caller's
    own index for it, and `origin` is the caller's index of the action that failed, so the caller
    hands back both instead of a bare cause that reads as though nothing happened. Every number
    here counts in the caller's batch, never the fixed-up one: a fixup may insert an action they
    never sent, and a position from the longer list names an action they did not write.
    Re-issuing the whole batch would repeat every applied action, which is the mistake this
    exists to prevent."""

    def __init__(
        self,
        applied: tuple[tuple[int, str], ...],
        origin: int,
        total: int,
        cause: Exception,
    ) -> None:
        super().__init__(f"action {origin + 1} of {total} failed: {cause}")
        self.applied = applied
        self.origin = origin
        self.total = total
        self.cause = cause


class TabLeftOpen(Exception):
    """A tab that was created and then failed to reach its url. `Target.createTarget` already
    returned, so the tab is open, attached, and holding a place in the tab list whatever the
    navigation did — a caller told only that the navigation failed leaves it there and opens
    another on the retry. `tab_id` is the index it holds, so the caller can navigate it again or
    close it."""

    def __init__(self, tab_id: int, url: str, cause: Exception) -> None:
        super().__init__(f"tab {tab_id} opened but could not reach {url}: {cause}")
        self.tab_id = tab_id
        self.url = url
        self.cause = cause
