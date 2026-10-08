import json
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from ufo_ext_memory.client import (
    EXTRACT_FACTS_MAX,
    EXTRACT_PAGES_MAX,
    IMPORT_BATCH,
    PAGES_BATCH,
    QUERY_MAX_CHARS,
    ClusterResults,
    Correct,
    ExtractPage,
    ImportRecord,
    MemoryApi,
    PageMirror,
    PageResults,
    Reach,
    Search,
    SectionResults,
    Write,
)
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.memory_service import MEMORY_WIRE, MemoryServiceStandIn
from ufo_testsupport.service_stand_in import SentRequest

from ufo.sdk.cloud import CloudRefused

GOLDEN: dict[str, dict] = json.loads(MEMORY_WIRE.read_text(encoding="utf-8"))
AGENT = UUID("5b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e")
MEMBER = UUID("7c2d3e4f-5a6b-4c7d-9e8f-0a1b2c3d4e5f")
MEMORY_ID = UUID("0192f0c6-2b3c-7d4e-9f5a-6b7c8d9e0f1a")
GOLDEN_SUBJECTS = ("shared", f"member:{MEMBER}")
REFUSAL = "A memory states one fact about one subject."


def _client() -> tuple[MemoryServiceStandIn, MemoryApi]:
    stand_in = MemoryServiceStandIn()
    return stand_in, MemoryApi(cloud=cloud_apis_for(stand_in.app).bound(uuid4()))


def _request(operation: str) -> dict:
    return GOLDEN[operation]["request"]


def _answer(operation: str) -> dict:
    return GOLDEN[operation]["answer"]["body"]


def _sent(stand_in: MemoryServiceStandIn, operation: str) -> SentRequest:
    sent = stand_in.sent[-1]
    assert (sent.operation, sent.method) == (operation, GOLDEN[operation]["method"])
    return sent


def _query(operation: str) -> list[tuple[str, str]]:
    return sorted((key, value) for key, value in GOLDEN[operation]["query"])


def _memory(memory_id: UUID) -> dict:
    return _answer("memory.list")["items"][0] | {"id": str(memory_id)}


async def test_write_sends_memory_write() -> None:
    stand_in, api = _client()

    written = await api.write(Write.model_validate(_request("memory.write")))

    assert _sent(stand_in, "memory.write").body == _request("memory.write")
    assert written.model_dump(mode="json") == _answer("memory.write")


async def test_search_sends_memory_search() -> None:
    stand_in, api = _client()

    matches = await api.search(Search.model_validate(_request("memory.search")))

    assert _sent(stand_in, "memory.search").body == _request("memory.search")
    assert [match.model_dump(mode="json") for match in matches] == _answer("memory.search")[
        "matches"
    ]


async def test_list_sends_memory_list() -> None:
    stand_in, api = _client()
    query = dict(GOLDEN["memory.list"]["query"])

    page = await api.list(
        subjects=GOLDEN_SUBJECTS,
        item_classes=("fact",),
        kinds=("fact", "decision"),
        cursor=query["cursor"],
        reach=Reach(agent_id=AGENT, member_id=MEMBER),
    )

    sent = _sent(stand_in, "memory.list")
    assert sorted(sent.query) == _query("memory.list")
    assert sent.body is None
    assert page.model_dump(mode="json") == _answer("memory.list")


async def test_get_sends_memory_get() -> None:
    stand_in, api = _client()

    memory = await api.get(
        MEMORY_ID, subjects=GOLDEN_SUBJECTS, reach=Reach(agent_id=AGENT, member_id=MEMBER)
    )

    sent = _sent(stand_in, "memory.get")
    assert sent.path == f"/v1/memory/memories/{MEMORY_ID}"
    assert sorted(sent.query) == _query("memory.get")
    assert memory is not None
    assert memory.model_dump(mode="json") == _answer("memory.get")


async def test_get_of_an_unknown_id_is_none() -> None:
    stand_in, api = _client()
    stand_in.answer(
        "memory.get", 404, {"error": {"code": "not_found", "message": "No memory has that id."}}
    )

    assert await api.get(MEMORY_ID, subjects=("shared",), reach=None) is None
    assert _sent(stand_in, "memory.get").query == (("subject", "shared"),)


async def test_correct_sends_memory_correct() -> None:
    stand_in, api = _client()

    correction = await api.correct(MEMORY_ID, Correct.model_validate(_request("memory.correct")))

    sent = _sent(stand_in, "memory.correct")
    assert sent.path == f"/v1/memory/memories/{MEMORY_ID}"
    assert sent.body == _request("memory.correct")
    assert correction.model_dump(mode="json") == _answer("memory.correct")


async def test_delete_sends_memory_delete() -> None:
    stand_in, api = _client()

    deleted = await api.delete(MEMORY_ID)

    sent = _sent(stand_in, "memory.delete")
    assert (sent.path, sent.query, sent.body) == (f"/v1/memory/memories/{MEMORY_ID}", (), None)
    assert deleted.model_dump(mode="json") == _answer("memory.delete")


async def test_delete_subject_sends_memory_delete_subject() -> None:
    stand_in, api = _client()
    query = dict(GOLDEN["memory.delete_subject"]["query"])

    deleted = await api.delete_subject(
        query["subject"], source_ref_prefix=query["source_ref_prefix"]
    )

    sent = _sent(stand_in, "memory.delete_subject")
    assert list(sent.query) == [tuple(pair) for pair in GOLDEN["memory.delete_subject"]["query"]]
    assert deleted.model_dump(mode="json") == _answer("memory.delete_subject")


async def test_settings_sends_memory_settings() -> None:
    stand_in, api = _client()

    settings = await api.settings()

    assert _sent(stand_in, "memory.settings").body is None
    assert settings.model_dump(mode="json") == _answer("memory.settings")


async def test_pending_sends_memory_index() -> None:
    stand_in, api = _client()

    assert await api.pending() == _answer("memory.index")["pending"]
    assert _sent(stand_in, "memory.index").body is None


async def test_pages_sends_memory_pages() -> None:
    stand_in, api = _client()
    mirrors = [PageMirror.model_validate(page) for page in _request("memory.pages")["pages"]]

    assert await api.pages(mirrors) == _answer("memory.pages")["settled"]
    assert _sent(stand_in, "memory.pages").body == _request("memory.pages")


async def test_four_hundred_fifty_mirrors_post_in_three_batches() -> None:
    stand_in, api = _client()
    page = _request("memory.pages")["pages"][0]
    mirrors = [PageMirror.model_validate(page | {"page_id": str(uuid4())}) for _ in range(450)]

    assert await api.pages(mirrors) == 3 * _answer("memory.pages")["settled"]
    bodies = [sent.body for sent in stand_in.sent]
    assert [len(body["pages"]) for body in bodies] == [PAGES_BATCH, PAGES_BATCH, 50]
    assert [mirror["page_id"] for body in bodies for mirror in body["pages"]] == [
        str(mirror.page_id) for mirror in mirrors
    ]


async def test_extract_sends_memory_extract() -> None:
    stand_in, api = _client()
    pages = [ExtractPage.model_validate(page) for page in _request("memory.extract")["pages"]]

    outcomes = await api.extract(pages)

    assert _sent(stand_in, "memory.extract").body == _request("memory.extract")
    assert [outcome.model_dump(mode="json") for outcome in outcomes] == _answer("memory.extract")[
        "pages"
    ]


@pytest.mark.parametrize("name", ["clusters", "sections", "page"])
async def test_consolidate_sends_memory_consolidate(name: str) -> None:
    stand_in, api = _client()
    operation = f"memory.consolidate.{name}"

    work = await api.consolidate(name)

    assert _sent(stand_in, operation).body == _request(operation)
    assert work.model_dump(mode="json", by_alias=True) == _answer(operation)


@pytest.mark.parametrize(
    ("name", "results"),
    [("clusters", ClusterResults), ("sections", SectionResults), ("page", PageResults)],
)
async def test_consolidate_results_sends_memory_consolidate_results(
    name: str, results: type[ClusterResults | SectionResults | PageResults]
) -> None:
    stand_in, api = _client()
    operation = f"memory.consolidate_results.{name}"

    outcome = await api.consolidate_results(results.model_validate(_request(operation)))

    assert _sent(stand_in, operation).body == _request(operation)
    assert outcome.model_dump(mode="json") == _answer(operation)


async def test_a_consolidate_results_conflict_raises_conflict() -> None:
    stand_in, api = _client()
    stand_in.answer(
        "memory.consolidate_results.sections",
        409,
        {"error": {"code": "conflict", "message": "Consolidation is off for the workspace."}},
    )

    with pytest.raises(CloudRefused) as refused:
        await api.consolidate_results(
            SectionResults.model_validate(_request("memory.consolidate_results.sections"))
        )

    assert (refused.value.status, refused.value.code) == (409, "conflict")


async def test_import_records_sends_memory_import() -> None:
    stand_in, api = _client()
    records = [
        ImportRecord.model_validate(record) for record in _request("memory.import")["records"]
    ]

    imported = await api.import_records(records)

    assert _sent(stand_in, "memory.import").body == _request("memory.import")
    assert imported.model_dump(mode="json") == _answer("memory.import")


async def test_newest_pages_until_its_total() -> None:
    stand_in, api = _client()
    ids = [uuid4() for _ in range(5)]
    stand_in.queue(
        "memory.list",
        [
            (
                200,
                {
                    "items": [_memory(ids[0]), _memory(ids[1])],
                    "next_cursor": "older|2",
                    "prev_cursor": None,
                },
            ),
            (200, {"items": [_memory(ids[2])], "next_cursor": "older|3", "prev_cursor": "newer|3"}),
        ],
    )

    newest = await api.newest(subjects=("shared",), total=3)

    assert [memory.id for memory in newest] == ids[:3]
    assert [dict(sent.query) for sent in stand_in.sent] == [
        {"subject": "shared", "limit": "3"},
        {"subject": "shared", "cursor": "older|2", "limit": "1"},
    ]


async def test_newest_stops_at_the_end_of_the_list() -> None:
    stand_in, api = _client()
    stand_in.queue(
        "memory.list",
        [(200, {"items": [_memory(MEMORY_ID)], "next_cursor": None, "prev_cursor": None})],
    )

    newest = await api.newest(item_classes=("overview",), total=500)

    assert [memory.id for memory in newest] == [MEMORY_ID]
    assert [sent.query for sent in stand_in.sent] == [
        (("item_class", "overview"), ("limit", "200"))
    ]


async def test_a_refused_write_raises_with_the_sentence() -> None:
    stand_in, api = _client()
    stand_in.answer("memory.write", 400, {"error": {"code": "invalid_request", "message": REFUSAL}})

    with pytest.raises(CloudRefused) as refused:
        await api.write(Write.model_validate(_request("memory.write")))

    assert (refused.value.code, refused.value.message) == ("invalid_request", REFUSAL)


async def test_a_query_past_the_bound_is_cut_at_a_word() -> None:
    stand_in, api = _client()
    words = "tea " * (QUERY_MAX_CHARS // 4) + "oolong"
    search = Search(
        queries=[words, "  What does Alice drink?  "],
        subjects=["shared"],
        limit=8,
        reach=Reach(agent_id=AGENT, member_id=MEMBER),
    )

    await api.search(search)

    cut, short = _sent(stand_in, "memory.search").body["queries"]
    assert len(cut) <= QUERY_MAX_CHARS
    assert words.startswith(cut)
    assert cut.endswith("tea")
    assert short == "What does Alice drink?"


async def test_a_query_without_whitespace_in_the_bound_is_cut_at_the_bound() -> None:
    stand_in, api = _client()
    unbroken = "茶" * (QUERY_MAX_CHARS + 1)
    search = Search(
        queries=[unbroken, "   "],
        subjects=["shared"],
        limit=8,
        reach=Reach(agent_id=AGENT, member_id=MEMBER),
    )

    await api.search(search)

    assert _sent(stand_in, "memory.search").body["queries"] == [unbroken[:QUERY_MAX_CHARS]]


async def test_an_empty_query_sends_no_search() -> None:
    stand_in, api = _client()
    search = Search(
        queries=["   ", "\t\n"],
        subjects=["shared"],
        limit=8,
        reach=Reach(agent_id=AGENT, member_id=None),
    )

    assert await api.search(search) == ()
    assert stand_in.sent == []


async def test_a_write_omits_the_optional_fields_its_caller_left_unset() -> None:
    stand_in, api = _client()

    await api.write(
        Write(
            subject="shared",
            body="The team ships on Fridays.",
            kind="fact",
            item_class="fact",
            confidence=5,
        )
    )

    assert _sent(stand_in, "memory.write").body == {
        "subject": "shared",
        "body": "The team ships on Fridays.",
        "kind": "fact",
        "item_class": "fact",
        "confidence": 5,
    }


async def test_a_null_member_reach_is_sent_as_null() -> None:
    stand_in, api = _client()

    await api.search(
        Search(
            queries=["What does Alice drink?"],
            subjects=["shared"],
            limit=8,
            reach=Reach(agent_id=AGENT, member_id=None),
        )
    )

    assert _sent(stand_in, "memory.search").body == {
        "queries": ["What does Alice drink?"],
        "subjects": ["shared"],
        "limit": 8,
        "reach": {"agent_id": str(AGENT), "member_id": None},
    }


async def test_list_repeats_its_filters_in_order() -> None:
    stand_in, api = _client()

    await api.list(
        subjects={"shared", "member:b", "member:a"},
        item_classes=["semantic", "fact"],
        kinds=("task", "event"),
        limit=50,
        reach=Reach(agent_id=AGENT, member_id=None),
    )

    assert _sent(stand_in, "memory.list").query == (
        ("subject", "member:a"),
        ("subject", "member:b"),
        ("subject", "shared"),
        ("kind", "event"),
        ("kind", "task"),
        ("item_class", "fact"),
        ("item_class", "semantic"),
        ("limit", "50"),
        ("reach.agent_id", str(AGENT)),
    )


def _search(**changes: object) -> Search:
    fields: dict[str, object] = {
        "queries": ["tea"],
        "subjects": ["shared"],
        "limit": 8,
        "reach": Reach(agent_id=AGENT, member_id=None),
    }
    return Search.model_validate(fields | changes)


def _extract_page(facts: int) -> ExtractPage:
    page = _request("memory.extract")["pages"][0]
    return ExtractPage.model_validate(page | {"facts": [page["facts"][0]] * facts})


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.search(_search(limit=51)),
        lambda api: api.search(_search(queries=["a", "b", "c", "d"])),
        lambda api: api.search(_search(subjects=[f"member:{n}" for n in range(65)])),
        lambda api: api.list(limit=201),
        lambda api: api.extract([_extract_page(1)] * (EXTRACT_PAGES_MAX + 1)),
        lambda api: api.extract([_extract_page(0)]),
        lambda api: api.extract([_extract_page(EXTRACT_FACTS_MAX + 1)]),
        lambda api: api.import_records(
            [ImportRecord.model_validate(_request("memory.import")["records"][1])]
            * (IMPORT_BATCH + 1)
        ),
    ],
    ids=[
        "search-limit",
        "search-queries",
        "search-subjects",
        "list-limit",
        "extract-pages",
        "extract-no-fact",
        "extract-facts",
        "import-batch",
    ],
)
async def test_a_call_past_its_bound_is_refused_before_it_is_sent(
    call: Callable[[MemoryApi], Awaitable[object]],
) -> None:
    stand_in, api = _client()

    with pytest.raises(ValueError):
        await call(api)

    assert stand_in.sent == []
