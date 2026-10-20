"""HubSpot connector over a mock transport: the CRM search → flatten → watermark path, the
archived-id sweep that tombstones deleted records, the declared-`Pagination` product-API path, the
seven streams read under the parent record that holds them, and the `403`-scope refusal that
surfaces as `StreamSkipped`. Offline — a canned transport, no DB, no token, no broker."""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.hubspot import (
    _CAMPAIGN_ASSET_TYPES,
    ALL_STREAMS,
    HubSpotConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.backend import MAX_RECORDS_PER_RUN
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    no_parents,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "campaigns": (ParentRecord(ref="campaigns/cmp1", fields={"id": "cmp1"}),),
    "lists": (ParentRecord(ref="lists/42", fields={"listId": "42"}),),
    "forms": (ParentRecord(ref="forms/f1", fields={"id": "f1"}),),
    "conversations": (ParentRecord(ref="conversations/t1", fields={"id": "t1"}),),
    "contacts": (
        ParentRecord(ref="contacts/c1", fields={"id": "c1", "email": "ada.new@example.com"}),
    ),
    "owners": (
        ParentRecord(ref="owners/o1", fields={"userId": 77}),
        ParentRecord(ref="owners/o2", fields={}),
    ),
}


def _spec(name: str):
    return next(stream for stream in ALL_STREAMS if stream.name == name)


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages = no_parents,
):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=HubSpotConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
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


async def test_product_streams_default_to_camel_case_updated_at(
    parents_reader: ParentsReader,
) -> None:
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


async def test_form_submissions_project_submission_time_as_creation(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200, json={"results": [{"conversionId": "s1", "submittedAt": "2026-01-01T00:00:00Z"}]}
        )

    result = await _fetch("form_submissions", handle, parents=parents_reader(LANDED))
    assert seen == ["/form-integrations/v1/submissions/forms/f1"]
    assert result.pages[0].source_identity == "form_submissions/f1/s1"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_consent_states_project_capture_time_as_creation(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unsubscribe-all"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={"results": [{"subscriptionId": "news", "timestamp": "2026-01-01T00:00:00Z"}]},
        )

    result = await _fetch("consent_states", handle, parents=parents_reader(LANDED))
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


async def test_consent_states_key_on_the_contact_id_not_the_contact_email(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        if request.url.path.endswith("/unsubscribe-all"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "subscriptionId": "news",
                        "businessUnitId": "bu1",
                        "subscriptionName": "Newsletter",
                        "status": "SUBSCRIBED",
                        "legalBasis": "CONSENT_WITH_NOTICE",
                        "timestamp": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("consent_states", handle, parents=parents_reader(LANDED))
    assert seen == [
        "/communication-preferences/2026-03/statuses/ada.new%40example.com"
        "/unsubscribe-all?channel=EMAIL",
        "/communication-preferences/2026-03/statuses/ada.new%40example.com?channel=EMAIL",
    ]
    assert [page.source_ref for page in result.pages] == [
        "consent_states/ada.new@example.com:news:bu1"
    ]
    assert [page.source_identity for page in result.pages] == ["consent_states/c1:news:bu1"]
    assert result.dropped == 0
    assert [page.digest for page in result.pages] == [
        "sha256:fef4c4aa4d0c10d05cc678f51467b8d2a083fa399fcc784e380e21384d33fffa"
    ]


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


async def test_list_memberships_read_only_the_list_that_holds_them(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"results": [{"recordId": "301"}]})

    result = await _fetch("list_memberships", handle, parents=parents_reader(LANDED))
    assert seen == ["/crm/lists/2026-03/42/memberships"]
    assert [page.source_identity for page in result.pages] == ["list_memberships/42/301"]


async def test_conversation_messages_read_only_the_thread_that_holds_them(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200, json={"results": [{"id": "m1", "createdAt": "2026-01-01T00:00:00Z"}]}
        )

    result = await _fetch("conversation_messages", handle, parents=parents_reader(LANDED))
    assert seen == ["/conversations/v3/conversations/threads/t1/messages"]
    assert [page.source_identity for page in result.pages] == ["conversation_messages/t1/m1"]


async def test_a_refused_asset_type_says_which_one_and_why(
    caplog: pytest.LogCaptureFixture,
    parents_reader: ParentsReader,
) -> None:

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/FORM"):
            return httpx.Response(200, json={"results": [{"id": "a1"}]})
        return httpx.Response(403, json={"message": "not in this tier"})

    with caplog.at_level(logging.WARNING):
        result = await _fetch("campaign_assets", handle, parents=parents_reader(LANDED))

    refused = [
        record.ufo for record in caplog.records if record.msg == "source_sync.collection_refused"
    ]
    assert len(refused) == len(_CAMPAIGN_ASSET_TYPES) - 1
    assert {fields["collection"] for fields in refused} == set(_CAMPAIGN_ASSET_TYPES) - {"FORM"}
    assert {fields["campaign"] for fields in refused} == {"cmp1"}
    assert {fields["http_status"] for fields in refused} == {403}
    assert {fields["stream"] for fields in refused} == {"campaign_assets"}
    assert [page.source_identity for page in result.pages] == ["campaign_assets/cmp1/FORM:a1"]


async def test_campaign_assets_ask_every_asset_type_of_the_campaign_that_landed(
    parents_reader: ParentsReader,
) -> None:
    """The dated segment is the rollout HubSpot documents; `2026-03` names no campaigns rollout
    the references carry."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if not request.url.path.endswith("/FORM"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [{"id": "a1", "name": "Signup"}]})

    result = await _fetch("campaign_assets", handle, parents=parents_reader(LANDED))
    assert seen[0].startswith("/marketing/campaigns/2026-09/cmp1/assets/")
    assert "/marketing/campaigns/2026-09/cmp1/assets/FORM" in seen
    assert [page.source_identity for page in result.pages] == ["campaign_assets/cmp1/FORM:a1"]


async def test_sequence_enrollments_land_the_single_object_the_endpoint_answers(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "id": "e1",
                "sequenceId": "s9",
                "enrolledAt": "2026-01-01T00:00:00Z",
                "toEmail": "ada@example.com",
            },
        )

    result = await _fetch("sequence_enrollments", handle, parents=parents_reader(LANDED))
    assert seen == ["/automation/v4/sequences/enrollments/contact/c1"]
    assert [page.source_identity for page in result.pages] == ["sequence_enrollments/c1/e1"]
    assert result.dropped == 0


async def test_a_parent_that_has_landed_nothing_yet_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    result = await _fetch("conversation_messages", handle, parents=no_parents)
    assert result.pages == ()


def test_no_child_hubspot_declares_is_canonical() -> None:
    """None of the seven has ever had a `source` row — `ConnectedSources` registers only canonical
    streams — so the parent the address now carries restamps no landed page."""
    children = [stream for stream in ALL_STREAMS if stream.parents]
    assert sorted(stream.name for stream in children) == [
        "campaign_assets",
        "consent_states",
        "conversation_messages",
        "form_submissions",
        "list_memberships",
        "sequence_enrollments",
        "sequences",
    ]
    assert [stream.name for stream in children if stream.canonical] == []


async def test_consent_states_ask_both_collections_the_contact_holds(
    parents_reader: ParentsReader,
) -> None:
    consent = _spec("consent_states")
    assert [edge.carry for edge in consent.parents] == [{"contact_id": "id"}, {"contact_id": "id"}]
    assert consent.key_scope == "global"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unsubscribe-all"):
            return httpx.Response(200, json={"results": [{"purpose": "unsubscribe_all"}]})
        return httpx.Response(200, json={"results": []})

    result = await _fetch("consent_states", handle, parents=parents_reader(LANDED))
    assert [page.source_identity for page in result.pages] == [
        "consent_states/c1:unsubscribe_all:default"
    ]


async def test_sequences_pass_over_an_owner_that_is_not_a_user(
    parents_reader: ParentsReader,
) -> None:
    """A HubSpot owner need not be a user, and one that is not carries no `userId` and has no
    sequences."""
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [{"id": "seq1", "name": "Onboarding"}]})

    result = await _fetch("sequences", handle, parents=parents_reader(LANDED))

    assert [edge.optional for edge in _spec("sequences").parents] == [True]
    assert seen == [{"userId": "77", "limit": "100"}]
    assert [page.source_identity for page in result.pages] == ["sequences/77/seq1"]


HUBSPOT_RESULT_WINDOW = 10_000


def _company_records(count: int) -> list[dict]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        {
            "id": f"c{index}",
            "createdAt": "2026-01-01T00:00:00Z",
            "properties": {
                "hs_lastmodifieddate": (base + timedelta(minutes=index)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            },
        }
        for index in range(count)
    ]


async def _drive_runs(stream: str, handler: Callable[[httpx.Request], httpx.Response]) -> list[str]:
    refs: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        result = await _fetch(stream, handler, cursor=cursor)
        refs.extend(page.source_ref for page in result.pages)
        cursor = result.next_cursor
        if len(result.pages) < MAX_RECORDS_PER_RUN:
            return refs
    raise AssertionError(f"{stream} did not finish within five runs")


async def test_companies_search_reanchors_at_the_result_window() -> None:
    """A capped run resumes by re-driving its search and skipping what it already landed, so a
    portal larger than twice the cap pages deeper on every run."""
    records = _company_records(HUBSPOT_RESULT_WINDOW + 150)
    seen_after: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/companies":
            return httpx.Response(200, json={"results": [{"name": "hs_lastmodifieddate"}]})
        if request.method == "POST" and path == "/crm/v3/objects/companies/search":
            body = json.loads(request.content)
            after = int(body.get("after", 0))
            end = after + body["limit"]
            seen_after.append(after)
            if end > HUBSPOT_RESULT_WINDOW:
                return httpx.Response(
                    400,
                    json={
                        "status": "error",
                        "category": "VALIDATION_ERROR",
                        "message": (
                            "Result window is too large, count + offset must be less than or "
                            f"equal to: {HUBSPOT_RESULT_WINDOW} but was {end}"
                        ),
                    },
                )
            groups = body.get("filterGroups") or []
            floor = groups[0]["filters"][0]["value"] if groups else None
            matching = [
                record
                for record in records
                if floor is None or record["properties"]["hs_lastmodifieddate"] >= floor
            ]
            paging = {"next": {"after": str(end)}} if end < len(matching) else {}
            return httpx.Response(200, json={"results": matching[after:end], "paging": paging})
        if request.method == "GET" and path == "/crm/v3/objects/companies":
            return httpx.Response(200, json={"results": [], "paging": {}})
        return httpx.Response(404, json={"path": path})

    refs = await _drive_runs("companies", handle)

    assert len(refs) == len(set(refs)) == len(records)
    assert max(seen_after) == HUBSPOT_RESULT_WINDOW - 100


def _site_search_handler(
    seen: list[int], *, total: int, refuses_past: int = HUBSPOT_RESULT_WINDOW
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/cms/v3/site-search/search":
            return httpx.Response(404, json={"path": request.url.path})
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        seen.append(offset)
        if offset + limit > refuses_past:
            return httpx.Response(
                400,
                json={
                    "status": "error",
                    "message": (
                        "Result window is too large, count + offset must be less than or equal "
                        f"to: {refuses_past} but was {offset + limit}"
                    ),
                },
            )
        return httpx.Response(
            200,
            json={
                "total": total,
                "offset": offset,
                "limit": limit,
                "results": [
                    {"id": f"ka{index}", "title": f"Article {index}"}
                    for index in range(offset, min(offset + limit, total))
                ],
            },
        )

    return handle


async def _drive_site_search(
    handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> tuple[list[str], str | None]:
    refs: list[str] = []
    for _ in range(5):
        result = await _fetch("knowledge_articles", handler, cursor=cursor)
        refs.extend(page.source_ref for page in result.pages)
        cursor = result.next_cursor
        if len(result.pages) < MAX_RECORDS_PER_RUN:
            return refs, cursor
    raise AssertionError("knowledge_articles did not finish within five runs")


async def test_site_search_stops_at_the_result_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The walk ends at the window HubSpot serves, reports the content past it once, and asks for
    no page the endpoint answers with a 400."""
    seen: list[int] = []

    with caplog.at_level(logging.WARNING):
        refs, cursor = await _drive_site_search(
            _site_search_handler(seen, total=HUBSPOT_RESULT_WINDOW + 150)
        )

    assert refs == [f"knowledge_articles/ka{index}" for index in range(HUBSPOT_RESULT_WINDOW)]
    assert seen == sorted(set(seen))
    assert max(seen) + 100 <= HUBSPOT_RESULT_WINDOW
    reached = [
        record.ufo for record in caplog.records if record.msg == "source_sync.result_window_reached"
    ]
    assert [fields["window"] for fields in reached] == [HUBSPOT_RESULT_WINDOW]
    assert reached[0]["stream"] == "knowledge_articles"

    caplog.clear()
    seen.clear()
    with caplog.at_level(logging.WARNING):
        again, _ = await _drive_site_search(
            _site_search_handler(seen, total=HUBSPOT_RESULT_WINDOW + 150), cursor
        )

    assert again == refs
    assert "source_sync.result_window_reached" not in {record.msg for record in caplog.records}


async def test_site_search_ends_the_run_when_hubspot_refuses_the_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A portal whose window closes earlier than the documented 10,000 results refuses the page with
    a 400: the run keeps what it read and ends, rather than failing every run forever."""
    seen: list[int] = []
    handler = _site_search_handler(seen, total=HUBSPOT_RESULT_WINDOW + 150, refuses_past=9_000)

    with caplog.at_level(logging.WARNING):
        refs, cursor = await _drive_site_search(handler)

    assert refs == [f"knowledge_articles/ka{index}" for index in range(9_000)]
    assert [record.msg for record in caplog.records].count("source_sync.result_window_reached") == 1

    caplog.clear()
    again, _ = await _drive_site_search(handler, cursor)

    assert again == refs


async def test_site_search_skips_the_stream_on_a_400_that_is_not_the_window() -> None:
    """A site-search 400 other than the window refusal skips the stream, and the skip reason carries
    HubSpot's bounded `message` and `category`, which the failure record never logs."""
    message = "Invalid search query " + "x" * 1_000

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "status": "error",
                "message": message,
                "correlationId": "c0ffee",
                "category": "VALIDATION_ERROR",
            },
        )

    with pytest.raises(StreamSkipped) as skipped:
        await _fetch("knowledge_articles", handle)

    reason = skipped.value.reason
    assert "knowledge_articles" in reason
    assert "Invalid search query" in reason
    assert "[VALIDATION_ERROR]" in reason
    assert message not in reason
    assert len(reason) < 500


async def test_site_search_skip_reason_survives_a_body_without_json() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request")

    with pytest.raises(StreamSkipped) as skipped:
        await _fetch("knowledge_articles", handle)

    assert "see upstream response" in skipped.value.reason


async def test_site_search_server_errors_still_fail_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": "error", "message": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("knowledge_articles", handle)


async def test_crm_search_400_still_fails_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/properties/companies":
            return httpx.Response(200, json={"results": [{"name": "hs_lastmodifieddate"}]})
        return httpx.Response(
            400, json={"status": "error", "message": "bad filter", "category": "VALIDATION_ERROR"}
        )

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("companies", handle)
