"""The Recurly connector over a mock transport: the `{data, has_more, next}` cursor walk with the
watermark advancing over `updated_at`, the per-account and per-coupon collections and where a capped
run resumes in them, and a refusal surfacing as `StreamSkipped`. Offline — a canned transport, no
DB, no token."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.recurly import RECURLY_STREAMS, RecurlyConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.backend import MAX_RECORDS_PER_RUN
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

ACCOUNTS: Mapping[str, tuple[ParentRecord, ...]] = {
    "accounts": (ParentRecord(ref="accounts/a1", fields={"id": "a1"}),)
}
COUPONS: Mapping[str, tuple[ParentRecord, ...]] = {
    "coupons": (
        ParentRecord(ref="coupons/c1", fields={"id": "c1", "coupon_type": "bulk"}),
        ParentRecord(ref="coupons/c2", fields={"id": "c2", "coupon_type": "single_code"}),
        ParentRecord(ref="coupons/c3", fields={"id": "c3"}),
    )
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    parents: ParentPages | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents)
    return await ConnectorBackend(connector=RecurlyConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_accounts_walk_next_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounts"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "a2", "updated_at": "2026-02-02T00:00:00Z"}],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "a1", "updated_at": "2026-02-01T00:00:00Z"}],
                "has_more": True,
                "next": "/accounts?cursor=c2",
            },
        )

    result = await _fetch("accounts", handle)
    assert _refs(result) == {"accounts/a1", "accounts/a2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert {page.updated_at for page in result.pages} == {
        "2026-02-01T00:00:00.000000+00:00",
        "2026-02-02T00:00:00.000000+00:00",
    }


async def test_account_notes_fan_out_over_landed_accounts(parents_reader: ParentsReader) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/accounts/a1/notes":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "n1", "created_at": "2026-02-01T00:00:00Z"}],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("account_notes", handle, parents=parents_reader(ACCOUNTS))
    assert _refs(result) == {"account_notes/a1/n1"}
    assert asked == ["/accounts/a1/notes"]
    assert result.next_cursor is not None
    assert list(json.loads(result.next_cursor).values()) == ["2026-02-01T00:00:00Z"]
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"


async def test_a_capped_run_resumes_each_account_at_its_own_watermark(
    parents_reader: ParentsReader,
) -> None:
    """A run capped inside one account's notes checkpoints that account and never reaches the next,
    and the resume asks the capped one from its own watermark rather than from the beginning."""
    asked: list[tuple[str, str | None]] = []
    bulk = [
        {"id": f"n{index}", "created_at": f"2026-02-01T00:00:{index % 60:02d}Z"}
        for index in range(MAX_RECORDS_PER_RUN)
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        begin_time = request.url.params.get("begin_time")
        asked.append((request.url.path, begin_time))
        if request.url.path == "/accounts/a1/notes":
            if begin_time:
                return httpx.Response(200, json={"data": [], "has_more": False})
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "last", "created_at": "2026-02-02T00:00:00Z"}],
                        "has_more": False,
                    },
                )
            return httpx.Response(
                200,
                json={"data": bulk, "has_more": True, "next": "/accounts/a1/notes?page=2"},
            )
        return httpx.Response(
            200,
            json={"data": [{"id": "n2", "created_at": "2026-01-01T00:00:00Z"}], "has_more": False},
        )

    landed = {
        "accounts": (
            ParentRecord(ref="accounts/a1", fields={"id": "a1"}),
            ParentRecord(ref="accounts/a2", fields={"id": "a2"}),
        )
    }
    capped = await _fetch("account_notes", handle, parents=parents_reader(landed))

    assert len(capped.pages) > MAX_RECORDS_PER_RUN
    assert [path for path, _ in asked] == ["/accounts/a1/notes", "/accounts/a1/notes"]
    assert capped.next_cursor is not None

    asked.clear()
    resumed = await _fetch(
        "account_notes", handle, cursor=capped.next_cursor, parents=parents_reader(landed)
    )

    assert asked == [
        ("/accounts/a1/notes", "2026-02-02T00:00:00Z"),
        ("/accounts/a2/notes", None),
    ]
    assert _refs(resumed) == {"account_notes/a2/n2"}


async def test_unique_coupons_fan_out_over_bulk_coupons_alone(
    parents_reader: ParentsReader,
) -> None:
    """Recurly publishes unique codes only under a bulk coupon, so the edge hangs under the coupons
    whose `coupon_type` is bulk — one request for the bulk one, none for the single-code one, and
    none for a coupon whose record does not say."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/coupons/c1/unique_coupon_codes":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "u1", "code": "XYZ", "updated_at": "2026-02-01T00:00:00Z"}],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("unique_coupons", handle, parents=parents_reader(COUPONS))
    assert _refs(result) == {"unique_coupons/c1/u1"}
    assert asked == ["/coupons/c1/unique_coupon_codes"]


async def test_a_coupon_page_carries_the_type_its_child_edge_weighs() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/coupons"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "c1", "coupon_type": "bulk", "updated_at": "2026-02-01T00:00:00Z"}],
                "has_more": False,
            },
        )

    result = await _fetch("coupons", handle)
    assert result.pages[0].parent_fields == {"id": "c1", "coupon_type": "bulk"}


async def test_children_are_declared_edges_over_streams_the_catalog_holds() -> None:
    declared = {stream.name: stream for stream in RECURLY_STREAMS}
    edges = {
        name: tuple((edge.stream, edge.path) for edge in stream.parents)
        for name, stream in declared.items()
        if stream.parents
    }
    assert edges == {
        "account_coupon_redemptions": (("accounts", "/accounts/{id}/coupon_redemptions"),),
        "account_notes": (("accounts", "/accounts/{id}/notes"),),
        "billing_infos": (("accounts", "/accounts/{id}/billing_infos"),),
        "shipping_addresses": (("accounts", "/accounts/{id}/shipping_addresses"),),
        "unique_coupons": (("coupons", "/coupons/{id}/unique_coupon_codes"),),
    }
    assert declared["unique_coupons"].parents[0].where == {"coupon_type": ("bulk",)}
    assert "unique_coupons_parent" not in declared
    assert not any(declared[name].canonical for name in edges)


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("accounts", handle)
