"""What the page-alerts extension declares: chat tools that bind a watch to the conversation it
was asked in, and the page_change hook that classifies changed pages off-turn and invokes an
alerting turn into the bound conversation on a match."""

from ufo.sdk.manifest import HookSpec, Manifest
from ufo.sdk.tools import ToolDef
from ufo_ext_page_alerts.alerts import (
    CancelPageWatchInput,
    ListPageWatchesInput,
    WatchPagesInput,
    cancel_page_watch,
    list_page_watches,
    on_page_change,
    watch_pages,
)

NAME = "page_alerts"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="watch_pages",
                description=(
                    "Watch the workspace's synced pages for a topic: when a changed page matches, "
                    "the platform alerts this conversation. Give a short topic phrase and an "
                    "optional watch name."
                ),
                input_model=WatchPagesInput,
                handler=watch_pages,
            ),
            ToolDef(
                name="list_page_watches",
                description="List this workspace's page watches with their topics.",
                input_model=ListPageWatchesInput,
                handler=list_page_watches,
            ),
            ToolDef(
                name="cancel_page_watch",
                description="Cancel a page watch by its name.",
                input_model=CancelPageWatchInput,
                handler=cancel_page_watch,
            ),
        ),
        hooks=(HookSpec(event="page_change", handler=on_page_change),),
    )
