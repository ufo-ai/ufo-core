"""Instagram connector over a mock transport: the Page walk (`/me/accounts`), the linked
Instagram-business-account fan-out, a media collection paged by `data` + `paging.next`, the
`timestamp` watermark, the per-object insights declared as edges under `media` and `stories`, and a
refusal at the account walk surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.instagram import InstagramConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "media": (ParentRecord(ref="media/m1", fields={"id": "m1"}),),
    "stories": (ParentRecord(ref="stories/s1", fields={"id": "s1"}),),
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    parents: ParentPages,
    cursor: str | None = None,
):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=InstagramConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


_PAGE = {
    "id": "p1",
    "name": "Acme Page",
    "instagram_business_account": {"id": "iga1", "username": "acme", "name": "Acme"},
}


def _handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.facebook.com"
        path = request.url.path
        if path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [_PAGE], "paging": {}})
        if path.endswith("/iga1/media"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "caption": "Launch day",
                            "media_type": "IMAGE",
                            "permalink": "https://instagram.com/p/m1",
                            "timestamp": "2026-02-01T00:00:00+0000",
                        }
                    ],
                    "paging": {},
                },
            )
        if path.endswith("/iga1/insights"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "name": "reach",
                            "values": [{"value": 10, "end_time": "2026-02-02T00:00:00+0000"}],
                        }
                    ]
                },
            )
        if path.endswith("/m1/insights") or path.endswith("/s1/insights"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "name": "reach",
                            "period": "lifetime",
                            "values": [{"value": 4}],
                            "id": f"{path.split('/')[-2]}/insights/reach/lifetime",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_pages_walk_keys_by_id(parents_reader: ParentsReader) -> None:
    result = await _fetch("pages", _handler(), parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {"pages/p1"}
    assert result.snapshot is False


async def test_media_fans_out_and_advances_timestamp_watermark(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch("media", _handler(), parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {"media/m1"}
    assert result.next_cursor == "2026-02-01T00:00:00+0000"
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    assert result.pages[0].parent_fields == {"id": "m1"}


@pytest.mark.parametrize(
    ("stream", "obj", "metrics"),
    [
        ("media_insights", "m1", "impressions,reach,engagement,saved,video_views"),
        ("story_insights", "s1", "impressions,reach,replies,taps_forward,taps_back,exits"),
    ],
)
async def test_object_insights_are_addressed_under_the_object_they_measure(
    stream: str, obj: str, metrics: str, parents_reader: ParentsReader
) -> None:
    """A metric name tells one insight from the next inside one object and nowhere else, so the
    object the edge's `{id}` names is what addresses it. The objects themselves are the parent
    stream's landed pages, so the run walks neither `/me/accounts` nor the media collection."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return _handler()(request)

    result = await _fetch(stream, handle, parents=parents_reader(LANDED))
    assert asked == [f"/v25.0/{obj}/insights"]
    assert {page.source_ref for page in result.pages} == {f"{stream}/{obj}/reach"}


async def test_object_insights_the_account_cannot_serve_drop_the_object(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/m1/insights"):
            return httpx.Response(400, json={"error": {"message": "unsupported metric"}})
        return _handler()(request)

    result = await _fetch("media_insights", handle, parents=parents_reader(LANDED))
    assert result.pages == ()


async def test_user_insights_use_the_daily_snapshot_end_time(parents_reader: ParentsReader) -> None:
    result = await _fetch("user_insights", _handler(), parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {
        "user_insights/iga1:reach:2026-02-02T00:00:00+0000"
    }
    assert result.pages[0].created_at == "2026-02-02T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_refusal_at_account_walk_maps_to_stream_skipped(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    with pytest.raises(StreamSkipped):
        await _fetch("media", handle, parents=parents_reader(LANDED))
