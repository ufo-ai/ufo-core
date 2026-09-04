"""HubSpot connector over a mock transport: the CRM search → flatten → watermark path, the
archived-id sweep that tombstones deleted records, the declared-`Pagination` product-API path, and
the `403`-scope refusal that surfaces as `StreamSkipped`. Offline — a canned transport, no DB, no
token, no broker."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.hubspot import ALL_STREAMS, HubSpotConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=HubSpotConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _companies_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.hubapi.com"
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/companies":
            return httpx.Response(
                200, json={"results": [{"name": "name"}, {"name": "hs_lastmodifieddate"}]}
            )
        if request.method == "POST" and path == "/crm/v3/objects/companies/search":
            body = json.loads(request.content)
            if not body.get("after"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "c1",
                                "createdAt": "2026-01-01T00:00:00Z",
                                "updatedAt": "2026-02-05T00:00:00Z",
                                "properties": {
                                    "name": "Acme",
                                    "hs_lastmodifieddate": "2026-02-05T00:00:00Z",
                                },
                            }
                        ],
                        "paging": {},
                    },
                )
            return httpx.Response(200, json={"results": [], "paging": {}})
        if request.method == "GET" and path == "/crm/v3/objects/companies":
            return httpx.Response(200, json={"results": [{"id": "c9"}], "paging": {}})
        return httpx.Response(404, json={"path": path})

    return handle


async def test_companies_search_flattens_watermark_and_sweeps_deletes() -> None:
    result = await _fetch("companies", _companies_handler())

    assert {page.source_ref for page in result.pages} == {"companies/c1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T00:00:00Z"
    assert result.deletes == ("companies/c9",)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"

    body = result.pages[0].body
    assert "Acme" in body
    assert "hs_lastmodifieddate" in body


async def test_companies_incremental_filters_the_boundary_repeat() -> None:
    seen_bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/companies":
            return httpx.Response(200, json={"results": [{"name": "hs_lastmodifieddate"}]})
        if request.method == "POST" and path == "/crm/v3/objects/companies/search":
            seen_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "c1",
                            "properties": {"hs_lastmodifieddate": "2026-02-05T00:00:00Z"},
                        }
                    ],
                    "paging": {},
                },
            )
        if request.method == "GET" and path == "/crm/v3/objects/companies":
            return httpx.Response(200, json={"results": [], "paging": {}})
        return httpx.Response(404, json={"path": path})

    result = await _fetch("companies", handle, cursor="2026-02-01T00:00:00Z")
    assert {page.source_ref for page in result.pages} == {"companies/c1"}
    assert seen_bodies and seen_bodies[0]["filterGroups"][0]["filters"][0]["value"] == (
        "2026-02-01T00:00:00Z"
    )


async def test_contacts_watermark_rides_lastmodifieddate() -> None:
    seen_bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/contacts":
            return httpx.Response(
                200, json={"results": [{"name": "email"}, {"name": "lastmodifieddate"}]}
            )
        if request.method == "POST" and path == "/crm/v3/objects/contacts/search":
            seen_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "p1",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "properties": {
                                "email": "ada@example.com",
                                "lastmodifieddate": "2026-02-05T00:00:00Z",
                            },
                        }
                    ],
                    "paging": {},
                },
            )
        if request.method == "GET" and path == "/crm/v3/objects/contacts":
            return httpx.Response(200, json={"results": [], "paging": {}})
        return httpx.Response(404, json={"path": path})

    result = await _fetch("contacts", handle, cursor="2026-02-01T00:00:00Z")

    assert result.next_cursor == "2026-02-05T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"
    assert seen_bodies[0]["sorts"] == [
        {"propertyName": "lastmodifieddate", "direction": "ASCENDING"}
    ]
    assert seen_bodies[0]["filterGroups"][0]["filters"][0]["propertyName"] == "lastmodifieddate"


def _owners_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/crm/v3/owners":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "o1",
                            "email": "ada@example.com",
                            "createdAt": "2026-02-01T00:00:00Z",
                            "updatedAt": "2026-03-01T00:00:00Z",
                        }
                    ],
                    "paging": {},
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_owners_strategy_pagination_flattens_product_api() -> None:
    result = await _fetch("owners", _owners_handler())
    assert {page.source_ref for page in result.pages} == {"owners/o1"}
    assert result.next_cursor == "2026-03-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-03-01T00:00:00.000000+00:00"
    assert "ada@example.com" in result.pages[0].body


async def test_product_streams_default_to_camel_case_updated_at() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/marketing/marketing-events/2026-03":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "event-1",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-02-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("marketing_events", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    affected = {
        "marketing_events",
        "knowledge_articles",
        "campaign_assets",
        "pipelines",
        "pipeline_stages",
        "event_types",
        "association_labels",
        "associations",
        "list_memberships",
    }
    assert {
        stream.name: stream.updated_at_field for stream in ALL_STREAMS if stream.name in affected
    } == dict.fromkeys(affected, "updatedAt")


async def test_synthesized_owner_teams_declare_no_record_timestamps() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/owners":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "owner-1",
                            "teams": [{"id": "team-1", "name": "Sales", "primary": True}],
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("owner_teams", handle)
    assert result.pages[0].created_at is None
    assert result.pages[0].updated_at is None


async def test_blog_posts_preserve_cms_timestamps() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cms/blogs/2026-03/posts":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "post-1",
                            "created": "2026-01-01T00:00:00Z",
                            "updated": "2026-02-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("blog_posts", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_analytics_views_preserve_snake_case_timestamps() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/analytics/v2/views":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "v1",
                            "name": "Primary",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-02-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("analytics_views", handle)
    assert {page.source_ref for page in result.pages} == {"analytics_views/v1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_form_submissions_project_submission_time_as_creation() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/marketing/v3/forms":
            return httpx.Response(200, json={"results": [{"id": "f1", "name": "Contact"}]})
        if request.url.path == "/form-integrations/v1/submissions/forms/f1":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "conversionId": "s1",
                            "submittedAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("form_submissions", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_consent_states_project_capture_time_as_creation() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/objects/contacts":
            return httpx.Response(
                200,
                json={"results": [{"id": "c1", "properties": {"email": "ada@example.com"}}]},
            )
        if request.url.path == "/communication-preferences/2026-03/statuses/ada@example.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "subscriptionId": "news",
                            "timestamp": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/unsubscribe-all"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("consent_states", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_stream_skipped_when_the_object_is_scope_gated() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "This app does not have proper permissions to read companies"},
        )

    with pytest.raises(StreamSkipped):
        await _fetch("companies", handle)


async def test_consent_states_key_on_the_contact_id_not_the_contact_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/objects/contacts":
            return httpx.Response(
                200,
                json={"results": [{"id": "c1", "properties": {"email": "ada.new@example.com"}}]},
            )
        if request.url.path.endswith("/statuses/ada.new@example.com"):
            return httpx.Response(
                200, json={"results": [{"subscriptionId": "news", "businessUnitId": "bu1"}]}
            )
        if request.url.path.endswith("/unsubscribe-all"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("consent_states", handle)
    assert [page.source_ref for page in result.pages] == [
        "consent_states/ada.new@example.com:news:bu1"
    ]
    assert [page.source_identity for page in result.pages] == ["consent_states/c1:news:bu1"]
    assert "ada.new@example.com" in result.pages[0].body


async def test_event_types_end_their_key_chain_at_the_fully_qualified_name() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/v3/events/event-types"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"fullyQualifiedName": "pe1_signed_up", "name": "Signed up (renamed)"},
                    {"eventType": "nameless"},
                ]
            },
        )

    result = await _fetch("event_types", handle)
    assert [page.source_ref for page in result.pages] == ["event_types/pe1_signed_up"]
    assert result.dropped == 1


async def test_event_occurrences_require_the_provider_id() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/v3/events"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "evt1",
                        "eventType": "pe1_signed_up",
                        "objectType": "contact",
                        "objectId": "c1",
                        "occurredAt": "2026-01-01T00:00:00Z",
                        "properties": {"page": "/pricing"},
                    },
                    {"eventType": "pe1_signed_up", "properties": {"page": "/pricing"}},
                ]
            },
        )

    result = await _fetch("event_occurrences", handle)
    assert [page.source_ref for page in result.pages] == ["event_occurrences/evt1"]
    assert [page.source_identity for page in result.pages] == ["event_occurrences/evt1"]
    assert result.dropped == 1


async def test_email_events_require_the_provider_id() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/email/public/v1/events"
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "email-event-1",
                        "created": 1767225600000,
                        "recipient": "ada@example.com",
                        "type": "OPEN",
                        "emailCampaignId": 55,
                        "userAgent": "an agent that changes",
                    },
                    {"type": "OPEN", "emailCampaignId": 55},
                ],
                "hasMore": False,
            },
        )

    result = await _fetch("email_events", handle)
    assert [page.source_ref for page in result.pages] == ["email_events/email-event-1"]
    assert [page.source_identity for page in result.pages] == ["email_events/email-event-1"]
    assert result.dropped == 1
