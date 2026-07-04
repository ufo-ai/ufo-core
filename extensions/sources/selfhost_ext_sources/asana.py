"""The Asana connector — workspaces synced into recallable memory pages.

Asana paginates with an `offset` query param and reports the next offset at `next_page.offset` in
the response body, so the `next_cursor` strategy drives it declaratively. A run enumerates the whole
current collection, so the stream is a `delete_missing` snapshot: a workspace the grant no longer
sees is tombstoned. Records key on Asana's `gid`. The provider credential is resolved through the
auth proxy the runner threads (a broker's proxying transport, or a member-added key host-side) —
this connector holds no token itself."""

from selfhost_ext_sources.connector import Pagination, PaginationStrategy, StreamSpec
from selfhost_ext_sources.rest import RestConnector

ASANA_PAGE_LIMIT = 100

ASANA_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="workspaces",
        source_object="workspaces",
        primary_key="gid",
        delete_missing=True,
        pagination=Pagination(
            strategy=PaginationStrategy.next_cursor,
            path="/workspaces",
            record_path="data",
            cursor_path="next_page.offset",
            cursor_param="offset",
            page_size_param="limit",
            page_size=ASANA_PAGE_LIMIT,
        ),
    ),
]


class AsanaConnector(RestConnector):
    name = "asana"
    base_url = "https://app.asana.com/api/1.0"
    streams_list = ASANA_STREAMS
