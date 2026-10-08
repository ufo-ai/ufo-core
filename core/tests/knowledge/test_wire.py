import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import RootModel
from ufo_testsupport.cloud import TEST_BEARER, cloud_apis_for
from ufo_testsupport.memory_service import MEMORY_WIRE, MemoryServiceStandIn
from ufo_testsupport.service_stand_in import ServiceStandIn
from ufo_testsupport.sources_service import SOURCES_WIRE, SourcesServiceStandIn

from ufo.runtime.cloud import CloudRefused

SEAMS = {
    "memory.write": ("POST", "/v1/memory/memories"),
    "memory.search": ("POST", "/v1/memory/search"),
    "memory.list": ("GET", "/v1/memory/memories"),
    "memory.get": ("GET", "/v1/memory/memories/{id}"),
    "memory.correct": ("PATCH", "/v1/memory/memories/{id}"),
    "memory.delete": ("DELETE", "/v1/memory/memories/{id}"),
    "memory.delete_subject": ("DELETE", "/v1/memory/memories"),
    "memory.settings": ("GET", "/v1/memory/settings"),
    "memory.index": ("GET", "/v1/memory/index"),
    "memory.pages": ("POST", "/v1/memory/pages"),
    "memory.extract": ("POST", "/v1/memory/extract"),
    "memory.consolidate.clusters": ("POST", "/v1/memory/consolidate"),
    "memory.consolidate.sections": ("POST", "/v1/memory/consolidate"),
    "memory.consolidate.page": ("POST", "/v1/memory/consolidate"),
    "memory.consolidate_results.clusters": ("POST", "/v1/memory/consolidate/results"),
    "memory.consolidate_results.sections": ("POST", "/v1/memory/consolidate/results"),
    "memory.consolidate_results.page": ("POST", "/v1/memory/consolidate/results"),
    "memory.import": ("POST", "/v1/memory/import"),
    "sources.connect": ("POST", "/v1/sources"),
    "sources.list": ("GET", "/v1/sources"),
    "sources.get": ("GET", "/v1/sources/{id}"),
    "sources.patch": ("PATCH", "/v1/sources/{id}"),
    "sources.delete": ("DELETE", "/v1/sources/{id}"),
    "sources.sync": ("POST", "/v1/sources/{id}/sync"),
    "sources.page_upload": ("PUT", "/v1/sources/{id}/pages/{ref}"),
    "sources.pages_list": ("GET", "/v1/sources/pages"),
    "sources.page_get": ("GET", "/v1/sources/pages/{id}"),
    "sources.pages_read": ("POST", "/v1/sources/pages/read"),
    "sources.page_forget": ("DELETE", "/v1/sources/pages/{id}"),
    "sources.changes": ("GET", "/v1/sources/changes"),
    "sources.trigger_create": ("POST", "/v1/sources/triggers"),
    "sources.triggers_list": ("GET", "/v1/sources/triggers"),
    "sources.trigger_patch": ("PATCH", "/v1/sources/triggers/{id}"),
    "sources.trigger_delete": ("DELETE", "/v1/sources/triggers/{id}"),
    "sources.import": ("POST", "/v1/sources/import"),
    "sources.trigger_event": ("POST", "<events_url>"),
}
EVENT_ONLY = "sources.trigger_event"
PAIR_KEYS = {"method", "path", "query", "request", "answer"}
NOT_FOUND = {"error": {"code": "not_found", "message": "No memory of that id is visible."}}


class Json(RootModel[object]):
    pass


def _golden(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _wire() -> dict[str, dict]:
    return _golden(MEMORY_WIRE) | _golden(SOURCES_WIRE)


async def _call(stand_in: ServiceStandIn, pair: dict) -> object:
    api = cloud_apis_for(stand_in.app).bound(uuid4())
    answer = await api.send(
        pair["method"],
        pair["path"].replace("{id}", str(uuid4())).replace("{ref}", "on-call"),
        body=None if pair["request"] is None else Json(pair["request"]),
        params=[(key, value) for key, value in pair["query"]],
        answer=Json,
    )
    return answer.root


def test_every_operation_is_pinned() -> None:
    assert set(_golden(MEMORY_WIRE)) == {name for name in SEAMS if name.startswith("memory.")}
    assert set(_golden(SOURCES_WIRE)) == {name for name in SEAMS if name.startswith("sources.")}
    assert len(SEAMS) == 36


def test_paths_and_methods_are_the_seams() -> None:
    assert {name: (pair["method"], pair["path"]) for name, pair in _wire().items()} == SEAMS


def test_every_answer_is_json_with_a_status() -> None:
    for name, pair in _wire().items():
        assert set(pair) == PAIR_KEYS, name
        assert pair["request"] is None or isinstance(pair["request"], dict), name
        assert all(
            len(item) == 2 and all(isinstance(part, str) for part in item) for item in pair["query"]
        ), name
        assert set(pair["answer"]) == {"status", "body"}, name
        assert pair["answer"]["status"] in (200, 201), name
        assert isinstance(pair["answer"]["body"], dict), name


@pytest.mark.parametrize(
    ("stand_in_type", "golden"),
    [(MemoryServiceStandIn, MEMORY_WIRE), (SourcesServiceStandIn, SOURCES_WIRE)],
)
async def test_the_stand_ins_answer_the_golden_and_record_what_they_were_sent(
    stand_in_type: type[ServiceStandIn], golden: Path
) -> None:
    stand_in = stand_in_type()
    served = {name: pair for name, pair in _golden(golden).items() if name != EVENT_ONLY}
    for operation, pair in served.items():
        assert await _call(stand_in, pair) == pair["answer"]["body"], operation
        sent = stand_in.sent[-1]
        assert sent.operation == operation
        assert sent.method == pair["method"]
        assert sent.query == tuple((key, value) for key, value in pair["query"])
        assert sent.headers["authorization"] == f"Bearer {TEST_BEARER}"
        assert sent.body == pair["request"], operation
    assert [sent.operation for sent in stand_in.sent] == list(served)


async def test_a_queued_answer_answers_once_then_the_golden() -> None:
    stand_in = MemoryServiceStandIn()
    pair = _golden(MEMORY_WIRE)["memory.get"]
    stand_in.queue("memory.get", [(404, NOT_FOUND)])

    with pytest.raises(CloudRefused) as refused:
        await _call(stand_in, pair)

    assert (refused.value.status, refused.value.code) == (404, "not_found")
    assert await _call(stand_in, pair) == pair["answer"]["body"]


async def test_a_replaced_answer_holds_for_every_later_call() -> None:
    stand_in = MemoryServiceStandIn()
    pair = _golden(MEMORY_WIRE)["memory.index"]
    stand_in.answer("memory.index", 200, {"pending": 0})

    assert await _call(stand_in, pair) == {"pending": 0}
    assert await _call(stand_in, pair) == {"pending": 0}


async def test_a_stand_in_refuses_a_call_without_a_bearer() -> None:
    stand_in = SourcesServiceStandIn()
    transport = httpx.ASGITransport(app=stand_in.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.test") as client:
        response = await client.get("/v1/sources/changes")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert stand_in.sent == []
