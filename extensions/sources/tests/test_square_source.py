"""The Square connector over a mock transport: the body-cursor list (`customers`), the POST catalog
search (`catalog_items`), the location-fanned POST order search (`orders`), the `Square-Version`
header, and a refusal as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.square import SQUARE_VERSION, SquareConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=SquareConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_customers_body_cursor_and_version_header() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Square-Version"))
        assert request.url.path.endswith("/customers")
        return httpx.Response(
            200, json={"customers": [{"id": "c1", "updated_at": "2026-02-01T00:00:00Z"}]}
        )

    result = await _fetch("customers", handle)
    assert _refs(result) == {"customers/c1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert seen and all(version == SQUARE_VERSION for version in seen)


@pytest.mark.parametrize(
    ("stream", "object_type"),
    [("catalog_items", "ITEM"), ("catalog_categories", "CATEGORY")],
)
async def test_catalog_streams_project_updated_at(stream: str, object_type: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/catalog/search")
        assert object_type.encode() in request.content
        return httpx.Response(
            200, json={"objects": [{"id": "o1", "updated_at": "2026-02-01T00:00:00Z"}]}
        )

    result = await _fetch(stream, handle)
    assert _refs(result) == {f"{stream}/o1"}
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_orders_fan_out_over_locations() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/locations"):
            return httpx.Response(200, json={"locations": [{"id": "L1"}]})
        if request.url.path.endswith("/orders/search"):
            return httpx.Response(
                200, json={"orders": [{"id": "or1", "created_at": "2026-02-01T00:00:00Z"}]}
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("orders", handle)
    assert _refs(result) == {"orders/or1"}
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": []})

    with pytest.raises(StreamSkipped):
        await _fetch("customers", handle)
